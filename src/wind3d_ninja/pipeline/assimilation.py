from __future__ import annotations

import csv
import json
import math
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from xml.etree.ElementTree import Element, SubElement, tostring

import netCDF4
import numpy as np
from pyproj import CRS, Transformer

from ..utils.windows_workspace import windows_safe_output
from ..utils.wind_coordinates import earth_to_grid
from ..config.models import AppConfig
from ..observations.models import Observation
from ..observations.profiles import build_station_profiles
from ..postprocess.assimilation import (
    StationConstraint, analyse_scalar, analyse_vector, apply_observation_stencil,
    load_station_csv, observation_stencil,
)
from .turbulence import TurbulenceBatchRunner, _direction_error, _metrics, _select_fixed


@dataclass(frozen=True)
class AssimilationBatchResult:
    output_dir: Path
    completed: int
    netcdf_files: tuple[Path, ...]
    kmz_files: tuple[Path, ...]
    validation_csv: Path
    report_json: Path

    def as_dict(self) -> dict[str, object]:
        return {"mode": "station_optimal_interpolation_analysis", "output_dir": str(self.output_dir),
                "completed": self.completed, "netcdf_files": [str(p) for p in self.netcdf_files],
                "kmz_files": [str(p) for p in self.kmz_files], "validation_csv": str(self.validation_csv),
                "report_json": str(self.report_json)}


def _components(speed: float, direction: float) -> tuple[float, float]:
    angle = math.radians(direction)
    return -speed * math.sin(angle), -speed * math.cos(angle)


def _polar(u: float, v: float) -> tuple[float, float]:
    return math.hypot(u, v), math.degrees(math.atan2(-u, -v)) % 360


def _background_metrics(rows: list[dict]) -> dict:
    return _metrics([{**row, "speed_error_ms": row.get("background_speed_error_ms"),
                      "direction_error_deg": row.get("background_direction_error_deg")}
                     for row in rows])


def _sample(field, x, y, px, py) -> float:
    stencil = observation_stencil(x, y, px, py)
    return math.nan if stencil.outside else apply_observation_stencil(field, stencil)


def _csv_observations(records, canopy: float) -> list[Observation]:
    result = []
    for record in records:
        height = record.height_m - canopy if record.height_reference == "agl" else record.height_m
        if height <= 0:
            raise ValueError(f"Station {record.station_id} is not above vegetation after height conversion")
        speed, direction = _polar(record.u_ms, record.v_ms)
        result.append(Observation(
            source="fixed_station", station=f"csv:{record.station_id}@{height:g}m", time_utc=record.time_utc,
            lat=record.latitude, lon=record.longitude, height_m=height, speed_ms=speed, direction_deg=direction,
            metadata={"station_group_id": f"csv:{record.station_id}", "product": "fixed_station_csv",
                      "wind_direction_reference": "from_true_north", "canopy_height_m": canopy,
                      "wind_sigma_ms": record.wind_sigma_ms, "k_m2s2": record.k_m2s2,
                      "k_sigma_m2s2": record.k_sigma_m2s2, "height_above_vegetation_m": height},
        ))
    return result


def _constraint(observation: Observation, transformer: Transformer, wind_sigma: float) -> StationConstraint:
    x, y = transformer.transform(observation.lon, observation.lat)
    u, v = _components(observation.speed_ms, observation.direction_deg)
    return StationConstraint(observation.station, x, y, observation.height_m, u, v,
                             float(observation.metadata.get("wind_sigma_ms", wind_sigma)),
                             observation.metadata.get("k_m2s2"), observation.metadata.get("k_sigma_m2s2"),
                             source=observation.source)


def _write_analysis_netcdf(background: Path, output: Path, u, v, k, settings: dict) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(background, output)
    with netCDF4.Dataset(output, "a") as dataset:
        dataset.background_solver_mode = dataset.solver_mode
        dataset.solver_mode = "station_optimal_interpolation_analysis"
        dataset.product_type = "station_observation_analysis"
        dataset.title = "Station-constrained mean-wind analysis on a RANS background"
        dataset.source = "WindNinja RANS background plus independent Gaussian optimal interpolation"
        dataset.assimilation_coupled_to_rans = "false"
        dataset.assimilation_settings = json.dumps(settings)
        dataset.validation_quantity = "independent held-out UAV mean wind"
        dataset.turbulence_product = ("TKE_analysis from compatible station statistics; observation-derived constant prior"
                                      if k is not None else "no height-resolved turbulence observations assimilated")
        for name in ("U_wind", "V_wind"):
            existing = dataset[name]
            prior = dataset.createVariable(name + "_background", "f4", existing.dimensions, fill_value=-9999, zlib=True)
            for attr in existing.ncattrs():
                if attr != "_FillValue":
                    prior.setncattr(attr, existing.getncattr(attr))
            prior[:] = existing[:]
        dataset["U_wind"][0] = np.ma.masked_invalid(u)
        dataset["V_wind"][0] = np.ma.masked_invalid(v)
        if k is not None:
            variable = dataset.createVariable("TKE_analysis", "f4", ("height", "lat", "lon"), fill_value=-9999, zlib=True)
            variable.units = "m2 s-2"
            variable.long_name = "Observation-constrained turbulent kinetic energy analysis"
            variable.coordinates = "lat lon"
            variable.grid_mapping = "crs"
            variable.prior_definition = "constant mean of assimilated fixed-station TKE observations; not native RANS TKE"
            variable[0] = np.ma.masked_invalid(k)


def _write_analysis_kmz(path: Path, x, y, u, v, crs: CRS, height: float) -> None:
    root = Element("kml", xmlns="http://www.opengis.net/kml/2.2")
    document = SubElement(root, "Document")
    SubElement(document, "name").text = f"Mean wind observation analysis, {height:g} m above vegetation"
    SubElement(document, "description").text = "Independent station analysis; this layer shows mean wind, not native turbulence."
    style = SubElement(document, "Style", id="analysis_wind")
    line_style = SubElement(style, "LineStyle")
    SubElement(line_style, "color").text = "ff38b7ff"
    SubElement(line_style, "width").text = "2"
    transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    stride = max(1, math.ceil(math.sqrt(u.size / 1500)))
    length = min(abs(x[1] - x[0]) if len(x) > 1 else 100, abs(y[1] - y[0]) if len(y) > 1 else 100) * 0.4
    for row in range(0, len(y), stride):
        for column in range(0, len(x), stride):
            if not np.isfinite(u[row, column]) or not np.isfinite(v[row, column]):
                continue
            speed, direction = _polar(float(u[row, column]), float(v[row, column]))
            if speed < 1e-8:
                continue
            grid_u, grid_v = earth_to_grid(u[row, column], v[row, column], crs, x[column], y[row])
            dx, dy = length * float(grid_u) / speed, length * float(grid_v) / speed
            tip_x, tip_y = x[column] + dx, y[row] + dy
            points = [(x[column], y[row]), (tip_x, tip_y),
                      (tip_x - 0.25 * dx + 0.15 * dy, tip_y - 0.25 * dy - 0.15 * dx),
                      (tip_x, tip_y), (tip_x - 0.25 * dx - 0.15 * dy, tip_y - 0.25 * dy + 0.15 * dx)]
            placemark = SubElement(document, "Placemark")
            SubElement(placemark, "name").text = f"{speed:.2f} m/s, from {direction:.1f} deg"
            SubElement(placemark, "styleUrl").text = "#analysis_wind"
            line = SubElement(placemark, "LineString")
            SubElement(line, "coordinates").text = " ".join(f"{lon:.8f},{lat:.8f},0" for lon, lat in
                                                           (transformer.transform(px, py) for px, py in points))
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("doc.kml", tostring(root, encoding="utf-8", xml_declaration=True))


class AssimilationBatchRunner:
    def __init__(self, config: AppConfig, progress_callback=None):
        self.config = config
        self.progress_callback = progress_callback

    @windows_safe_output
    def run(self, input_dir: Path, output_dir: Path, heights: list[int], resolutions: list[int],
            buffer_km: float, manual_bounds, generate_kmz: bool, *, station_observations: Path | None = None,
            horizontal_length_scale_m: float = 1000.0, background_wind_sigma_ms: float = 2.0,
            observation_wind_sigma_ms: float = 0.5, vertical_length_scale_m: float = 30.0,
            **background_options) -> AssimilationBatchResult:
        for name, value in (("horizontal_length_scale_m", horizontal_length_scale_m),
                            ("background_wind_sigma_ms", background_wind_sigma_ms),
                            ("observation_wind_sigma_ms", observation_wind_sigma_ms),
                            ("vertical_length_scale_m", vertical_length_scale_m)):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        records = load_station_csv(station_observations) if station_observations else ()
        output_dir = output_dir.expanduser().resolve()
        background = TurbulenceBatchRunner(self.config, self.progress_callback).run(
            input_dir, output_dir / "background", heights, resolutions, buffer_km, manual_bounds,
            generate_kmz, **background_options,
        )
        prior_report = json.loads(background.report_json.read_text(encoding="utf-8"))
        canopy = float(prior_report["instrument_policy"]["canopy_height_m"])
        extra = _csv_observations(records, canopy)
        fixed = list(background.fixed_observations) + extra
        window = int(prior_report["validation_window_seconds"])
        height_tolerance = float(prior_report["validation_height_tolerance_m"])
        all_validation = []
        cases = []
        netcdfs, kmzs = [], []
        settings = dict(horizontal_length_scale_m=horizontal_length_scale_m,
                        background_wind_sigma_ms=background_wind_sigma_ms,
                        default_windmaster_observation_sigma_ms=observation_wind_sigma_ms,
                        vertical_length_scale_m=vertical_length_scale_m, coupled_to_rans=False)
        for number, prior_path in enumerate(background.netcdf_files, 1):
            with netCDF4.Dataset(prior_path) as dataset:
                target = datetime.fromisoformat(dataset.target_time_utc.replace("Z", "+00:00"))
                height, resolution = float(dataset.output_height_m), int(dataset.mesh_resolution_m)
                x, y = dataset["x"][:].astype(float), dataset["y"][:].astype(float)
                u0 = np.ma.filled(dataset["U_wind"][0], np.nan).astype(float)
                v0 = np.ma.filled(dataset["V_wind"][0], np.nan).astype(float)
                crs = CRS.from_wkt(dataset["crs"].crs_wkt)
            if not crs.is_projected or any(not math.isclose(axis.unit_conversion_factor, 1.0) for axis in crs.axis_info):
                raise ValueError("Observation analysis requires a projected grid with meter axes")
            transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
            selected = _select_fixed(target, fixed, window)
            profiles = build_station_profiles(
                selected, reference_height_m=height, roughness_m=prior_report["profile_roughness_m"],
                displacement_height_m=prior_report["profile_displacement_height_m"],
                allow_log_extrapolation=prior_report["allow_profile_extrapolation"],
            )
            if not profiles.stations:
                raise ValueError(f"No station constraints at {height:g} m AV; choose an observed height or explicitly enable profile extrapolation: {profiles.rejected}")
            constraints = [_constraint(station, transformer, observation_wind_sigma_ms) for station in profiles.stations]
            wind = analyse_vector(u0, v0, x, y, constraints,
                                  length_scale_m=horizontal_length_scale_m, background_sigma=background_wind_sigma_ms)
            if not wind.used_station_ids:
                raise ValueError(f"No station constraints intersect valid background cells: {wind.excluded}")
            k_constraints = [_constraint(station, transformer, observation_wind_sigma_ms) for station in selected
                             if station.metadata.get("k_m2s2") is not None]
            k_field, k_report = None, {"used_count": 0, "status": "no_compatible_turbulence_observations"}
            valid_k_constraints = [item for item in k_constraints
                                   if math.isfinite(_sample(
                                       np.where(np.isfinite(u0) & np.isfinite(v0), 0.0, np.nan),
                                       x, y, item.x, item.y))]
            if valid_k_constraints:
                k_prior = float(np.mean([item.k_m2s2 for item in valid_k_constraints]))
                k_sigmas = [item.k_sigma_m2s2 if item.k_sigma_m2s2 is not None else max(0.1, 0.2 * item.k_m2s2)
                            for item in k_constraints]
                k = analyse_scalar(np.where(np.isfinite(u0) & np.isfinite(v0), k_prior, np.nan), x, y, k_constraints,
                                   values=[item.k_m2s2 for item in k_constraints], observation_sigmas=k_sigmas,
                                   length_scale_m=horizontal_length_scale_m, background_sigma=max(0.1, k_prior),
                                   vertical_length_scale_m=vertical_length_scale_m,
                                   vertical_offsets_m=[item.height_m - height for item in k_constraints])
                if k.used_station_ids:
                    k_field = np.maximum(k.field, 0)
                    k_report = {**k.summary, "status": "observation_constrained_tke_analysis",
                                "constant_prior_m2s2": k_prior, "prior_source": "training station TKE mean, not native RANS",
                                "nonnegative_projection_count": int(np.sum(k.field < 0)),
                                "per_station": [{**asdict(item), "analysis": None,
                                                 "observation_height_m": constraint.height_m,
                                                 "analysis_height_m": height,
                                                 "analysis_at_output_height_m2s2": (
                                                     apply_observation_stencil(k_field, observation_stencil(
                                                         x, y, constraint.x, constraint.y)) if item.used else None)}
                                                for item, constraint in zip(k.per_station, k_constraints)],
                                "excluded": k.excluded}
            output = output_dir / "nc" / prior_path.name.replace("Wind3D_", "WindAnalysis_")
            _write_analysis_netcdf(prior_path, output, wind.u_field, wind.v_field, k_field, settings)
            netcdfs.append(output)
            if generate_kmz:
                kmz = output_dir / "kmz" / (output.stem + ".kmz")
                _write_analysis_kmz(kmz, x, y, wind.u_field, wind.v_field, crs, height)
                kmzs.append(kmz)
            validation_rows = []
            for observation in background.validation_observations:
                offset = (observation.time_utc - target).total_seconds()
                if abs(offset) > window:
                    continue
                row = dict(target_time_utc=target.isoformat(), height_m=height, resolution_m=resolution,
                           validation_station=observation.station, validation_time_utc=observation.time_utc.isoformat(),
                           observed_speed_ms=observation.speed_ms, observed_direction_deg=observation.direction_deg,
                           observed_height_m=observation.height_m, time_offset_seconds=offset,
                           height_offset_m=observation.height_m - height, status="height_mismatch", role="held_out_uav")
                if abs(observation.height_m - height) <= height_tolerance:
                    px, py = transformer.transform(observation.lon, observation.lat)
                    au, av = _sample(wind.u_field, x, y, px, py), _sample(wind.v_field, x, y, px, py)
                    bu, bv = _sample(u0, x, y, px, py), _sample(v0, x, y, px, py)
                    row["status"] = "outside_or_invalid_grid"
                    if all(math.isfinite(value) for value in (au, av, bu, bv)):
                        speed, direction = _polar(au, av)
                        prior_speed, prior_direction = _polar(bu, bv)
                        row.update(status="ok", model_speed_ms=speed, model_direction_deg=direction,
                                   speed_error_ms=speed - observation.speed_ms,
                                   direction_error_deg=_direction_error(direction, observation.direction_deg),
                                   background_speed_ms=prior_speed, background_direction_deg=prior_direction,
                                   background_speed_error_ms=prior_speed - observation.speed_ms,
                                   background_direction_error_deg=_direction_error(prior_direction, observation.direction_deg),
                                   sampling_method="bilinear, same operator for background and analysis")
                validation_rows.append(row)
            case = dict(target_time_utc=target.isoformat(), height_m=height, resolution_m=resolution,
                        station_profiles=profiles.as_dict(), wind_constraints=wind.summary,
                        per_station_wind=[asdict(item) for item in wind.per_station], excluded=wind.excluded,
                        turbulence_analysis=k_report, background_metrics=_background_metrics(validation_rows),
                        **_metrics(validation_rows))
            cases.append(case)
            all_validation.extend(validation_rows)
            if self.progress_callback:
                self.progress_callback(f"[station-analysis] [{number}/{len(background.netcdf_files)}] {target.isoformat()} {height:g} m: {len(wind.used_station_ids)} station constraints")
        validation_csv = output_dir / "validation.csv"
        fields = ["target_time_utc", "height_m", "resolution_m", "validation_station", "validation_time_utc",
                  "observed_speed_ms", "observed_direction_deg", "observed_height_m", "time_offset_seconds",
                  "height_offset_m", "status", "role", "model_speed_ms", "model_direction_deg", "speed_error_ms",
                  "direction_error_deg", "background_speed_ms", "background_direction_deg", "background_speed_error_ms",
                  "background_direction_error_deg", "sampling_method"]
        with validation_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(all_validation)
        report = {"mode": "station_optimal_interpolation_analysis", "coupled_to_rans": False,
                  "analysis_quantity": "mean horizontal wind; optional TKE only from compatible station statistics",
                  "independent_validation_source": "held_out_uav", "settings": settings,
                  "background_report": str(background.report_json), "background_kmz_files": [str(p) for p in background.kmz_files],
                  "station_observations": str(station_observations) if station_observations else None,
                  "instrument_policy": prior_report["instrument_policy"], "height_reference": "vegetation_top",
                  "completed": len(netcdfs), "case_reports": cases,
                  "background_metrics": _background_metrics(all_validation), **_metrics(all_validation),
                  "conservation_limit": "independent statistical analysis does not enforce coupled mass/momentum/turbulence equations"}
        report_json = output_dir / "assimilation_report.json"
        report_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return AssimilationBatchResult(output_dir, len(netcdfs), tuple(netcdfs), tuple(kmzs), validation_csv, report_json)
