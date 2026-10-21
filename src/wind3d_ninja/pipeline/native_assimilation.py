from __future__ import annotations

import csv
import json
import math
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED

import netCDF4
import numpy as np
import rasterio
from pyproj import CRS, Transformer

from ..utils.windows_workspace import windows_safe_output
from ..utils.wind_coordinates import earth_to_grid, grid_to_earth, grid_north_bearing
from ..dem.manager import DemManager
from ..observations.instruments import load_instrument_catalog
from ..postprocess.assimilation import load_station_csv
from ..timeplan.axis import TimeAxis
from ..windninja.foam import NativeCellConstraint, configure_native_solver, prepare_native_case, read_foam_field, write_station_fv_options
from ..windninja.native import NativeRuntime
from .assimilation import _polar, _write_analysis_kmz
from .turbulence import TurbulenceBatchRunner, _direction_error, _metrics


@dataclass(frozen=True)
class NativeBatchResult:
    output_dir: Path
    completed: int
    netcdf_files: tuple[Path, ...]
    kmz_files: tuple[Path, ...]
    validation_csv: Path
    report_json: Path

    def as_dict(self):
        return {"mode": "native_rans_coupled_assimilation", "output_dir": str(self.output_dir),
                "completed": self.completed, "netcdf_files": list(map(str, self.netcdf_files)),
                "kmz_files": list(map(str, self.kmz_files)), "validation_csv": str(self.validation_csv),
                "report_json": str(self.report_json)}


def _positive(name, value):
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


def _load_native_records(path: Path):
    records = load_station_csv(path)
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not records:
        raise ValueError("Native coupled assimilation requires nonempty fixed-station CSV")
    result = []
    for record, row in zip(records, rows):
        k = record.k_m2s2
        epsilon = None
        epsilon_source = None
        if k is not None:
            _positive("k_m2s2", k)
            explicit = row.get("epsilon_m2s3", "").strip()
            length = row.get("mixing_length_m", "").strip()
            if explicit:
                epsilon = _positive("epsilon_m2s3", float(explicit))
                epsilon_source = "explicit epsilon_m2s3"
            elif length:
                length = _positive("mixing_length_m", float(length))
                epsilon = 0.09 ** 0.75 * k ** 1.5 / length
                epsilon_source = "mixing_length_m with Cmu=0.09"
        w = float(row.get("w_ms") or 0)
        if not math.isfinite(w):
            raise ValueError("w_ms must be finite")
        result.append((record, epsilon, w, bool(row.get("w_ms", "").strip()), epsilon_source))
    if not any(record.k_m2s2 is not None and epsilon is not None for record, epsilon, _, _, _ in result):
        raise ValueError("Native full coupled mode requires at least one station with compatible k and epsilon_m2s3 or mixing_length_m")
    return result


def _set_control(case: Path, start: float, end: float):
    path = case / "system/controlDict"
    text = path.read_text(encoding="utf-8")
    for key, value in {"startFrom": "startTime", "startTime": start, "endTime": end,
                       "deltaT": 1, "writeControl": "timeStep", "writeInterval": end-start,
                       "purgeWrite": 0, "writeFormat": "ascii", "writeCompression": "off"}.items():
        pattern = rf"\b{key}\s+[^;]+;"
        text = re.sub(pattern, f"{key} {value};", text) if re.search(pattern, text) else text + f"\n{key} {value};\n"
    path.write_text(text, encoding="utf-8")


def _latest_fields(case: Path):
    times = []
    for path in case.iterdir():
        if path.is_dir():
            try:
                value = float(path.name)
            except ValueError:
                continue
            if math.isfinite(value) and all((path / field).is_file() for field in ("U", "p", "k", "epsilon", "nut", "phi")):
                times.append((value, path))
    if not times:
        raise ValueError("Native solver wrote no complete serial checkpoint")
    return max(times, key=lambda item: item[0])


def _solve_native(runtime, case, start, max_iterations, tolerance, progress, label):
    if max_iterations < 51:
        raise ValueError("Native coupled solving requires at least 51 iterations, including 50 coupled warmup steps")
    solution = case / "system/fvSolution"
    configured = solution.read_text(encoding="utf-8")
    warmup = re.sub(r"\bresidualControl\s*\{[^{}]*\}", "", configured)
    try:
        solution.write_text(warmup, encoding="utf-8")
        _set_control(case, start, start+50)
        control = case / "system/controlDict"
        control.write_text(re.sub(r"\bwriteControl\s+[^;]+;", "writeControl runTime;",
                                  control.read_text(encoding="utf-8")), encoding="utf-8")
        runtime.run("simpleFoam", ["-case", str(case)], case, f"log.{label}.warmup", progress)
    finally:
        solution.write_text(configured, encoding="utf-8")
    warmed, _ = _latest_fields(case)
    if warmed != start+50:
        raise RuntimeError("Native coupled warmup did not complete all 50 iterations")
    _set_control(case, warmed, start+max_iterations)
    log = runtime.run("simpleFoam", ["-case", str(case)], case, f"log.{label}", progress)
    state = _solver_state(case, log, warmed, tolerance)
    state["warmup_iterations"] = 50
    state["total_iterations"] = state["time"]-start
    return log, state


def _solver_state(case: Path, log: Path, start: float, tolerance: float):
    text = log.read_text(encoding="utf-8", errors="replace")
    if "FOAM FATAL" in text or "Floating point exception" in text:
        raise RuntimeError(f"Native solver failed: {log}")
    final, path = _latest_fields(case)
    if final <= start:
        raise RuntimeError(f"Native solver did not advance beyond {start}: {log}")
    residuals = {}
    for field, value in re.findall(r"Solving for (\w+), Initial residual = ([\d.eE+-]+)", text):
        residuals[field] = float(value)
    converged = bool(re.search(r"SIMPLE solution converged|solution converged", text, re.I))
    required = ("p", "Ux", "Uy", "Uz", "k", "epsilon")
    # The log convergence message is authoritative only with actual equation residuals.
    if not converged or any(name not in residuals for name in required):
        raise RuntimeError(f"Native solver did not demonstrate converged U/p/k/epsilon equations: {log}")
    if any(not math.isfinite(residuals[name]) or residuals[name] > tolerance for name in required):
        raise RuntimeError(f"Native solver residuals exceed tolerance {tolerance}: {log}")
    continuity_matches = re.findall(
        r"time step continuity errors\s*:\s*sum local\s*=\s*([\d.eE+-]+)\s*,\s*global\s*=\s*([\d.eE+-]+)\s*,\s*cumulative\s*=\s*([\d.eE+-]+)", text)
    if not continuity_matches:
        raise RuntimeError(f"Native solver lacks continuity projection diagnostics: {log}")
    continuity = dict(zip(("sum_local", "global", "cumulative"), map(float, continuity_matches[-1])))
    if not all(math.isfinite(value) for value in continuity.values()) or abs(continuity["global"]) > max(tolerance, 1e-8):
        raise RuntimeError(f"Native global mass continuity error exceeds tolerance: {log}")
    u = read_foam_field(path / "U")
    if u.ndim != 2 or u.shape[1] != 3:
        raise ValueError("Native U must be a vector field")
    count = len(u)
    fields = {name: read_foam_field(path / name, count) for name in ("p", "k", "epsilon", "nut")}
    read_foam_field(path / "phi")
    if np.any(fields["k"] <= 0) or np.any(fields["epsilon"] <= 0) or np.any(fields["nut"] < 0):
        raise ValueError("Native k/epsilon must be positive and nut nonnegative")
    expected_nut = 0.09 * fields["k"]**2 / fields["epsilon"]
    relation_error = float(np.max(abs(fields["nut"]-expected_nut)/np.maximum(expected_nut,1e-30)))
    if relation_error > 1e-3:
        raise ValueError("Native nut is inconsistent with converged kEpsilon Cmu*k^2/epsilon")

    return {"time": final, "checkpoint": str(path), "converged": True,
            "initial_residuals": residuals, "continuity_errors": continuity, "nut_relation_max_relative_error": relation_error, "log": str(log)}


def _cell_geometry(runtime, case, progress):
    runtime.run("postProcess", ["-case", str(case), "-func", "writeCellCentres", "-latestTime"], case, "log.cellCentres", progress)
    runtime.run("postProcess", ["-case", str(case), "-func", "writeCellVolumes", "-latestTime"], case, "log.cellVolumes", progress)
    _, path = _latest_fields(case)
    centres = read_foam_field(path / "C")
    if centres.ndim != 2 or centres.shape[1] != 3:
        raise ValueError("Native cell centres must be 3-D")
    volumes = read_foam_field(path / "V", len(centres))
    if np.any(volumes <= 0):
        raise ValueError("Native cell volumes must be positive")
    return centres, volumes


def _terrain(dem: Path, x, y, crs: CRS):
    with rasterio.open(dem) as dataset:
        transform = Transformer.from_crs(crs, dataset.crs, always_xy=True)
        dx, dy = transform.transform(np.asarray(x).ravel(), np.asarray(y).ravel())
        values = np.ma.asarray([sample[0] for sample in dataset.sample(zip(dx, dy), masked=True)])
        result = np.ma.filled(values, np.nan).astype(float)
        if dataset.nodata is not None:
            result[np.isclose(result, dataset.nodata)] = np.nan
    return result.reshape(np.asarray(x).shape)


def _constraints(records, target, window, crs, dem, canopy, centres, settings, volumes=None):
    transform = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    result, report = [], []
    candidates = {}
    for item in records:
        record = item[0]
        if abs((record.time_utc-target).total_seconds()) <= window:
            key = (record.station_id, record.height_reference, record.height_m)
            candidates.setdefault(key, []).append(item)
    selected = [
        (min(items, key=lambda item: (abs((item[0].time_utc-target).total_seconds()), item[0].time_utc)), len(items))
        for items in candidates.values()
    ]
    for (record, epsilon, w, w_measured, epsilon_source), candidate_count in selected:
        x, y = transform.transform(record.longitude, record.latitude)
        ground = float(_terrain(dem, np.asarray(x), np.asarray(y), crs))
        agl = record.height_m if record.height_reference == "agl" else record.height_m + canopy
        if not math.isfinite(ground) or agl <= 0:
            raise ValueError(f"Station {record.station_id}: invalid DEM or physical AGL")
        z = ground + agl
        mask = ((centres[:, 0]-x)**2 + (centres[:, 1]-y)**2 <= settings["horizontal_radius_m"]**2) & (abs(centres[:, 2]-z) <= settings["vertical_radius_m"])
        ids = tuple(map(int, np.flatnonzero(mask)))
        if not ids:
            raise ValueError(f"Station {record.station_id}: source radius intersects no native cells")
        grid_u, grid_v = earth_to_grid(record.u_ms, record.v_ms, crs, x, y)
        result.append(NativeCellConstraint(record.station_id, ids, float(grid_u), float(grid_v), w,
                                          record.k_m2s2 if epsilon is not None else None, epsilon, settings["velocity_relaxation_seconds"],
                                          settings["tke_relaxation_seconds"], settings["epsilon_relaxation_seconds"]))
        report.append({"station_id": record.station_id, "cell_count": len(ids),
                       "selected_time_utc": record.time_utc.isoformat(),
                       "time_offset_seconds": (record.time_utc-target).total_seconds(),
                       "window_candidate_count": candidate_count,
                       "selected_cell_volume_m3": float(np.sum(volumes[list(ids)])) if volumes is not None else None, "x": x, "y": y,
                       "z_msl_m": z, "physical_agl_m": agl, "terrain_msl_m": ground,
                       "k_m2s2": record.k_m2s2, "epsilon_m2s3": epsilon,
                       "w_ms": w, "w_measured": w_measured,
                       "epsilon_source": epsilon_source,
                       "wind_vector_reference": "native projected x/y; station input true east/north",
                       "grid_north_bearing_deg": float(grid_north_bearing(crs, x, y)),
                       "station_true_u_ms": record.u_ms, "station_true_v_ms": record.v_ms,
                       "native_grid_u_ms": float(grid_u), "native_grid_v_ms": float(grid_v),
                       "actual_source_fields": ["U", "k", "epsilon"] if epsilon is not None else ["U"]})
    if not result or not any(c.k is not None and c.epsilon is not None for c in result):
        raise ValueError("No compatible k/epsilon CSV stations within this background time window")
    return result, report


def _sample_native(runtime, case, points, progress, label, *, sample_name="sampleNative", set_name="nativeGrid", allow_empty=False, sample_time=None):
    entries = "\n".join("(" + " ".join(f"{v:.17g}" for v in point) + ")" for point in points)
    dictionary = f"""FoamFile {{ version 2.0; format ascii; class dictionary; object {sample_name}; }}
functions
{{
{sample_name}
{{
type sets;
libs ("libsampling.dll");
writeControl writeTime;
setFormat raw;
interpolationScheme cellPoint;
fields (U k epsilon nut);
sets
(
    {set_name}
    {{
        type points;
        ordered no;
        axis xyz;
        points
        (
{entries}
        );
    }}
);
}}
}}
"""
    # The Windows OF8 distribution provides libsampling.dll in its runtime.
    (case / "system" / sample_name).write_text(dictionary, encoding="utf-8")
    time_arguments = ["-latestTime"] if sample_time is None else ["-time", f"{sample_time:g}"]
    runtime.run("postProcess", ["-case", str(case), "-dict", f"system/{sample_name}", "-fields", "(U k epsilon nut)", *time_arguments],
                case, f"log.sample.{label}", progress)
    final = _latest_fields(case)[0] if sample_time is None else sample_time
    if not all((case/f"{final:g}"/field).is_file() for field in ("U","k","epsilon","nut")):
        raise ValueError("Requested native sampling checkpoint is incomplete")
    folder = case / "postProcessing" / sample_name / f"{final:g}"
    if not folder.is_dir():
        folders = [p for p in (case / "postProcessing" / sample_name).iterdir() if p.is_dir() and math.isclose(float(p.name), final)]
        if len(folders) != 1:
            raise ValueError("Native sampling output does not match final solver time")
        folder = folders[0]
    files = list(folder.glob(f"{set_name}*.xy"))
    if len(files) != 2:
        raise ValueError(f"Expected native U vector and k/epsilon scalar sample files: {folder}")
    output = np.full((len(points), 6), np.nan)
    matched = np.zeros((len(points), 2), dtype=bool)
    for path in files:
        values = np.loadtxt(path, comments="#", ndmin=2)
        if values.size == 0:
            continue
        vector = re.search(r"(?:_|^)U(?:_|\.)", path.name) is not None
        expected = 6 if vector else 6
        if values.shape[1] != expected:
            raise ValueError(f"Unexpected native sample columns: {path}")
        # Compare each emitted coordinate; never fill unreported points by interpolation in Python.
        lookup = {tuple(np.round(point, 7)): index for index, point in enumerate(points)}
        for row in values:
            index = lookup.get(tuple(np.round(row[:3], 7)))
            if index is None:
                distances = np.max(abs(points-row[:3]), axis=1)
                candidates = np.flatnonzero(distances < 0.001)
                if len(candidates) != 1:
                    raise ValueError("Native sample coordinate mismatch")
                index = int(candidates[0])
            column = 0 if vector else 1
            if matched[index, column]:
                raise ValueError("Duplicate native sample point")
            matched[index, column] = True
            if vector:
                output[index, :3] = row[3:6]
            else:
                names = path.stem.removeprefix(set_name+"_").split("_")
                if set(names) != {"k", "epsilon", "nut"} or len(names) != 3:
                    raise ValueError("Unknown native scalar sample ordering")
                output[index, 3] = row[3+names.index("k")]
                output[index, 4] = row[3+names.index("epsilon")]
                output[index, 5] = row[3+names.index("nut")]
    output[~matched.all(axis=1)] = np.nan
    if not np.isfinite(output).any() and not allow_empty:
        raise ValueError("Native solver has no valid sampled grid points")
    finite = np.isfinite(output).all(axis=1)
    if np.any(output[finite, 3:5] <= 0) or np.any(output[finite, 5] < 0):
        raise ValueError("Native sample k/epsilon must remain positive")
    return output


def _validate_native(runtime, case, observations, target, height, resolution, window, tolerance, crs, dem, canopy, progress, *, baseline_time=None):
    transform = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    rows, eligible, points = [], [], []
    for observation in observations:
        row = {"target_time_utc": target.isoformat(), "height_m": height, "resolution_m": resolution,
               "station": observation.station, "observed_speed_ms": observation.speed_ms,
               "observed_time_utc": observation.time_utc.isoformat(),
               "observed_time_raw": observation.metadata.get("time_raw"),
               "observed_time_source_field": observation.metadata.get("time_source_field"),
               "observed_time_source_timezone_offset_hours": observation.metadata.get("time_source_timezone_offset_hours"),
               "observed_direction_deg": observation.direction_deg, "observed_height_m": observation.height_m,
               "latitude": observation.lat, "longitude": observation.lon,
               "height_method": observation.metadata.get("height_method"),
               "time_offset_seconds": (observation.time_utc-target).total_seconds(),
               "height_offset_m": observation.height_m-height}
        rows.append(row)
        if abs(row["time_offset_seconds"]) > window:
            row["status"] = "time_mismatch"
            continue
        if abs(row["height_offset_m"]) > tolerance:
            row["status"] = "height_mismatch"
            continue
        px, py = transform.transform(observation.lon, observation.lat)
        ground = float(_terrain(dem, np.asarray(px), np.asarray(py), crs))
        agl = float(observation.metadata.get("instrument_agl_m", observation.height_m+canopy))
        if not math.isfinite(ground) or not math.isfinite(agl) or agl <= 0:
            row["status"] = "invalid_terrain_or_physical_agl"
            continue
        row.update(terrain_elevation_m=ground, observed_physical_agl_m=agl, sample_z_msl_m=ground+agl,
                   sample_x_m=px, sample_y_m=py,
                   sampling_method="native cellPoint at held-out UAV actual XYZ; MSL=DEM+physical AGL",
                   status="outside_or_invalid_native_mesh")
        eligible.append(row)
        points.append((px, py, ground+agl))
    if points:
        unique, inverse = np.unique(np.asarray(points), axis=0, return_inverse=True)
        samples = _sample_native(runtime, case, unique, progress, "validation",
                                 sample_name="sampleNativeValidation", set_name="nativeValidation", allow_empty=True)
        baseline = (_sample_native(runtime, case, unique, progress, "validation_baseline",
                                   sample_name="sampleNativeValidation", set_name="nativeValidation",
                                   allow_empty=True, sample_time=baseline_time)
                    if baseline_time is not None else None)
        samples[:, 0], samples[:, 1] = grid_to_earth(samples[:, 0], samples[:, 1], crs, unique[:, 0], unique[:, 1])
        if baseline is not None:
            baseline[:, 0], baseline[:, 1] = grid_to_earth(baseline[:, 0], baseline[:, 1], crs, unique[:, 0], unique[:, 1])
        for row, sample_index in zip(eligible, inverse):
            sample = samples[sample_index]
            if baseline is not None:
                prior = baseline[sample_index]
                row["native_baseline_status"] = "outside_or_invalid_native_mesh"
                if np.isfinite(prior[:3]).all():
                    prior_speed, prior_direction = _polar(float(prior[0]), float(prior[1]))
                    row.update(native_baseline_status="ok", native_baseline_speed_ms=prior_speed,
                               native_baseline_direction_deg=prior_direction,
                               native_baseline_u_ms=float(prior[0]), native_baseline_v_ms=float(prior[1]),
                               native_baseline_w_ms=float(prior[2]),
                               native_baseline_speed_error_ms=prior_speed-row["observed_speed_ms"],
                               native_baseline_direction_error_deg=_direction_error(prior_direction,row["observed_direction_deg"]))
            if np.isfinite(sample[:3]).all():
                speed, direction = _polar(float(sample[0]), float(sample[1]))
                row.update(status="ok", model_speed_ms=speed, model_direction_deg=direction,
                           model_u_ms=float(sample[0]), model_v_ms=float(sample[1]), model_w_ms=float(sample[2]),
                           speed_error_ms=speed-row["observed_speed_ms"],
                           direction_error_deg=_direction_error(direction, row["observed_direction_deg"]))
    summary = {"sampling_method": "native cellPoint at actual held-out UAV XYZ",
               "wind_vector_reference": "true east/north after local meridian convergence rotation",
               "height_reference": "MSL = local DEM terrain + observed physical AGL; AV used for height tolerance only",
               "baseline_checkpoint_time": baseline_time,
               "native_baseline_sampled_count": sum(row.get("native_baseline_status")=="ok" for row in eligible),
               "eligible_count": len(eligible), "sampled_count": sum(row["status"]=="ok" for row in eligible),
               "unique_point_count": len(np.unique(np.asarray(points),axis=0)) if points else 0}
    summary["native_analysis_metrics"] = _metrics(rows)
    if baseline_time is not None:
        summary["native_baseline_metrics"] = _metrics([
            {**row, "status": row.get("native_baseline_status", row["status"]),
             "speed_error_ms": row.get("native_baseline_speed_error_ms"),
             "direction_error_deg": row.get("native_baseline_direction_error_deg")} for row in rows])
    return rows, summary


def _write_native_nc(prior, output, baseline, analysis, z, settings, case_report):
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(prior, output)
    with netCDF4.Dataset(output, "a") as dataset:
        shape = dataset["U_wind"][0].shape
        dataset.solver_mode = "native_rans_coupled_assimilation"
        dataset.assimilation_coupled_to_rans = "true"
        dataset.source = "Windows native OpenFOAM 8 simpleFoam with independent fixed-station fvOptions"
        dataset.title = "Native RANS coupled mean wind, k and epsilon"
        dataset.native_settings = json.dumps(settings)
        dataset.native_case = json.dumps(case_report)
        dataset.native_sample_height_reference = "MSL = DEM terrain + physical AGL (output height AV + canopy)"
        dataset.turbulence_product = "native coupled RANS k, epsilon and eddy viscosity nut"
        dataset.validation_quantity = "independent held-out UAV mean wind"
        dataset.wind_vector_reference = "true east/north; native projected grid wind rotated using local meridian convergence"
        for column, name, units in ((0, "U_wind", "m s-1"), (1, "V_wind", "m s-1"),
                                     (2, "W_wind", "m s-1"), (3, "TKE", "m2 s-2"), (4, "epsilon", "m2 s-3"), (5, "nut", "m2 s-1")):
            if name in dataset.variables:
                variable = dataset[name]
            else:
                variable = dataset.createVariable(name, "f4", ("height", "lat", "lon"), fill_value=-9999, zlib=True)
            variable.units = units
            variable.coordinates = "lat lon"
            variable.grid_mapping = "crs"
            variable[0] = np.ma.masked_invalid(analysis[:, column].reshape(shape))
            prior_var = dataset.createVariable(name+"_native_baseline", "f4", variable.dimensions, fill_value=-9999, zlib=True)
            prior_var.units = units
            prior_var[0] = np.ma.masked_invalid(baseline[:, column].reshape(shape))
        vertical = dataset.createVariable("sample_z_msl", "f8", ("lat", "lon"), fill_value=-9999)
        vertical.units = "m"
        vertical[:] = np.ma.masked_invalid(z)


class NativeCoupledBatchRunner:
    def __init__(self, config, progress_callback=None):
        self.config, self.progress_callback = config, progress_callback

    @windows_safe_output
    def run(self, input_dir, output_dir, heights, resolutions, buffer_km, manual_bounds, generate_kmz,
            *, station_observations: Path, native_runtime: Path | None = None,
            horizontal_radius_m=300.0, vertical_radius_m=50.0, velocity_relaxation_seconds=10.0,
            tke_relaxation_seconds=10.0, epsilon_relaxation_seconds=10.0,
            max_iterations=1000, residual_tolerance=1e-5, **background_options):
        settings = {name: _positive(name, value) for name, value in {
            "horizontal_radius_m": horizontal_radius_m, "vertical_radius_m": vertical_radius_m,
            "velocity_relaxation_seconds": velocity_relaxation_seconds, "tke_relaxation_seconds": tke_relaxation_seconds,
            "epsilon_relaxation_seconds": epsilon_relaxation_seconds, "residual_tolerance": residual_tolerance}.items()}
        if isinstance(max_iterations, bool) or not isinstance(max_iterations, int) or max_iterations < 51:
            raise ValueError("max_iterations must be an integer of at least 51, including 50 coupled warmup steps")
        settings.update(max_iterations=max_iterations, w_default_assumption="w=0 m/s where CSV lacks w_ms")
        records = _load_native_records(Path(station_observations))
        runtime = NativeRuntime(native_runtime)
        output_dir = Path(output_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        catalog = load_instrument_catalog(input_dir, selected_product=background_options.get("selected_product"))
        override = background_options.get("dem_override")
        dem = DemManager(self.config.dem).get_dem([override] if override else [], catalog.records, buffer_km, manual_bounds)
        owned_dem = output_dir / "terrain" / "native_domain.tif"
        owned_dem.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dem, owned_dem)
        background_options["dem_override"] = owned_dem
        before = {p.resolve() for p in owned_dem.parent.glob("NINJAFOAM*") if p.is_dir()}
        began = time.time()
        background = TurbulenceBatchRunner(self.config, self.progress_callback).run(
            input_dir, output_dir/"background", heights, resolutions, buffer_km, manual_bounds, False, **background_options)
        native_sources = [p for p in owned_dem.parent.glob("NINJAFOAM*") if p.is_dir() and p.resolve() not in before]
        if len(native_sources) != len(background.netcdf_files):
            raise ValueError("Retained native foam case count does not match background NetCDF count; refusing stale or ambiguous cases")
        native_sources.sort(key=lambda p: max((f.stat().st_mtime_ns for f in p.rglob("U")), default=0))
        priors = sorted(background.netcdf_files, key=lambda p: p.stat().st_mtime_ns)
        source_times = [max((f.stat().st_mtime_ns for f in source.rglob("U")), default=0) for source in native_sources]
        prior_times = [p.stat().st_mtime_ns for p in priors]
        if len(set(source_times)) != len(source_times) or len(set(prior_times)) != len(prior_times):
            raise ValueError("Retained source case ordering is ambiguous")
        for index, (source_time, prior_time) in enumerate(zip(source_times, prior_times)):
            if source_time > prior_time or (index and source_time < prior_times[index-1]):
                raise ValueError("Retained checkpoint chronology does not match background NetCDF chronology")
        report = json.loads(background.report_json.read_text(encoding="utf-8"))
        canopy = float(report["instrument_policy"]["canopy_height_m"])
        window = int(report["validation_window_seconds"])
        netcdfs, kmzs, cases, validation = [], [], [], []
        run_directory = output_dir/"native_cases"/f"run_{time.time_ns()}"
        for index, (prior, source) in enumerate(zip(priors, native_sources), 1):
            if prior.stat().st_mtime < began:
                raise ValueError("Background NetCDF predates native run")
            with netCDF4.Dataset(prior) as ds:
                target = datetime.fromisoformat(ds.target_time_utc.replace("Z", "+00:00"))
                height, resolution = float(ds.output_height_m), int(ds.mesh_resolution_m)
                x, y = np.asarray(ds["x"][:]), np.asarray(ds["y"][:])
                crs = CRS.from_wkt(ds["crs"].crs_wkt)
            if not crs.is_projected or any(not math.isclose(a.unit_conversion_factor, 1) for a in crs.axis_info):
                raise ValueError("Native assimilation requires projected metre axes")
            case = run_directory/f"case_{index:04d}"
            reconstruction = prepare_native_case(source, case, runtime.boundary_library)
            solver_configuration = configure_native_solver(case, max_iterations, residual_tolerance)
            write_station_fv_options(case, [])
            log, baseline_state = _solve_native(runtime, case, 0, max_iterations, residual_tolerance, self.progress_callback, "baseline")
            centres, volumes = _cell_geometry(runtime, case, self.progress_callback)
            xx, yy = np.meshgrid(x, y)
            z = _terrain(owned_dem, xx, yy, crs) + height + canopy
            valid = np.isfinite(z)
            points = np.column_stack((xx[valid], yy[valid], z[valid]))
            baseline_valid = _sample_native(runtime, case, points, self.progress_callback, "baseline")
            baseline_valid[:, 0], baseline_valid[:, 1] = grid_to_earth(
                baseline_valid[:, 0], baseline_valid[:, 1], crs, points[:, 0], points[:, 1])
            constraints, constraint_report = _constraints(records, target, window, crs, owned_dem, canopy, centres, settings, volumes)
            options_path = write_station_fv_options(case, constraints)
            source_names = re.findall(r"\b(station_[0-9a-f]+)\s*\{", options_path.read_text(encoding="utf-8"))
            start = baseline_state["time"]
            log, analysis_state = _solve_native(runtime, case, start, max_iterations, residual_tolerance, self.progress_callback, "analysis")
            source_log = log.read_text(encoding="utf-8", errors="replace")
            if "stationNudging" not in source_log or any(name not in source_log for name in source_names):
                raise RuntimeError("Native solver did not acknowledge every station fvOptions source")
            analysis_state["active_source_names"] = source_names
            analysis_valid = _sample_native(runtime, case, points, self.progress_callback, "analysis")
            analysis_valid[:, 0], analysis_valid[:, 1] = grid_to_earth(
                analysis_valid[:, 0], analysis_valid[:, 1], crs, points[:, 0], points[:, 1])
            baseline, analysis = (np.full((xx.size, 6), np.nan) for _ in range(2))
            baseline[valid.ravel()], analysis[valid.ravel()] = baseline_valid, analysis_valid
            source_membership = np.zeros(len(centres), dtype=int)
            for constraint in constraints:
                source_membership[list(constraint.cell_ids)] += 1
            case_report = {"source_union_cell_count": int(np.count_nonzero(source_membership)),
                           "overlapping_source_cell_count": int(np.count_nonzero(source_membership > 1)),
                           "source_union_volume_m3": float(volumes[source_membership > 0].sum()),
                           "overlapping_source_volume_m3": float(volumes[source_membership > 1].sum()),
                           "background": str(prior), "source_case": str(source), "reconstruction": reconstruction, "solver_configuration": solver_configuration,
                           "baseline": baseline_state, "analysis": analysis_state, "constraints": constraint_report,
                           "cell_count": len(centres), "cell_volume_sum_m3": float(volumes.sum()),
                           "valid_sample_count": int(np.isfinite(analysis[:, 0]).sum()),
                           "profile_roughness_m": report["profile_roughness_m"],
                           "native_roughness_note": "Retained WindNinja boundary coefficients are preserved; profile roughness may differ"}
            output = output_dir/"nc"/prior.name.replace("Wind3D_", "WindNative_")
            _write_native_nc(prior, output, baseline, analysis, z, settings, case_report)
            netcdfs.append(output)
            if generate_kmz:
                kmz = output_dir/"kmz"/(output.stem+".kmz")
                _write_analysis_kmz(kmz, x, y, analysis[:,0].reshape(xx.shape), analysis[:,1].reshape(xx.shape), crs, height)
                with ZipFile(kmz) as archive:
                    document = archive.read("doc.kml").replace(
                        b"Independent station analysis; this layer shows mean wind, not native turbulence.",
                        b"Windows native OpenFOAM 8 RANS coupled wind sampled from converged solver fields.").replace(
                        b"Mean wind observation analysis", b"Native RANS coupled mean wind")
                with ZipFile(kmz, "w", ZIP_DEFLATED) as archive:
                    archive.writestr("doc.kml", document)
                kmzs.append(kmz)
            validation_rows, validation_summary = _validate_native(
                runtime, case, background.validation_observations, target, height, resolution, window,
                float(report["validation_height_tolerance_m"]), crs, owned_dem, canopy, self.progress_callback, baseline_time=baseline_state["time"])
            validation.extend(validation_rows)
            case_report["held_out_validation"] = validation_summary
            cases.append(case_report)
        validation_csv = output_dir/"validation.csv"
        fields = sorted({key for row in validation for key in row}) or ["status"]
        with validation_csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fields)
            writer.writeheader()
            writer.writerows(validation)
        report_path = output_dir/"native_report.json"
        report_path.write_text(json.dumps({"mode": "native_rans_coupled_assimilation", "coupled_to_rans": True,
            "runtime": str(runtime.root), "settings": settings, "instrument_policy": report["instrument_policy"],
            "background_report": str(background.report_json), "cases": cases, "validation": _metrics(validation)},
            indent=2, ensure_ascii=False), encoding="utf-8")
        return NativeBatchResult(output_dir, len(netcdfs), tuple(netcdfs), tuple(kmzs), validation_csv, report_path)
