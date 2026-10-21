from __future__ import annotations

import csv
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Iterable

from pyproj import Transformer

from ..utils.windows_workspace import windows_safe_output, solver_safe_dem
from ..config.models import AppConfig
from ..dem.manager import DemManager
from ..observations.instruments import (
    InstrumentPolicy,
    RadarHeightReference,
    UavHeightReference,
    WindDirectionReference,
    WindSpeedUnit,
    load_instrument_catalog,
    normalize_catalog,
)
from ..observations.models import Observation
from ..observations.profiles import build_station_profiles
from ..utils.wind_coordinates import grid_north_bearing
from ..postprocess.kmz_writer import KmzWriter
from ..postprocess.netcdf_writer import (
    Wind3DNetcdfWriter,
    make_netcdf_filename,
    read_ascii_grid,
)
from ..stations.list_file import StationListWriter
from ..stations.writer import StationFileWriter, safe_station_name
from ..timeplan.axis import TimeAxis
from ..utils.geometry import GeoBounds, bounds_from_points
from ..windninja.config_writer import WindNinjaConfigWriter
from ..windninja.runner import WindNinjaRunner


UTC = timezone.utc


@dataclass(frozen=True)
class TurbulenceBatchResult:
    output_dir: Path
    cases: int
    completed: int
    validation_csv: Path
    report_json: Path
    netcdf_files: tuple[Path, ...]
    kmz_files: tuple[Path, ...]
    fixed_observations: tuple[Observation, ...] = ()
    validation_observations: tuple[Observation, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "output_dir": str(self.output_dir),
            "cases": self.cases,
            "completed": self.completed,
            "validation_csv": str(self.validation_csv),
            "report_json": str(self.report_json),
            "netcdf_files": [str(path) for path in self.netcdf_files],
            "kmz_files": [str(path) for path in self.kmz_files],
        }


@dataclass(frozen=True)
class GridSample:
    speed_ms: float
    direction_deg: float
    row: int
    column: int
    x: float
    y: float
    spatial_offset_m: float


def _direction_error(model: float, observed: float) -> float:
    return abs((model - observed + 180.0) % 360.0 - 180.0)


def _fixed_observations(observations: Iterable[Observation], max_height_m: float = 200.0, min_obtain_rate: float = 0.0) -> list[Observation]:
    values: list[Observation] = []
    for record in observations:
        if record.source != "windmaster" or record.moving:
            continue
        height = record.height_m
        rate = record.metadata.get("raw_quality", {}).get("DataObtainRate")
        if not 0 < height <= max_height_m or (rate is not None and rate < min_obtain_rate):
            continue
        values.append(record)
    return values


def _select_fixed(target: datetime, observations: Iterable[Observation], window_seconds: int) -> list[Observation]:
    selected: dict[str, tuple[float, Observation]] = {}
    for item in observations:
        distance = abs((item.time_utc - target).total_seconds())
        if distance > window_seconds:
            continue
        previous = selected.get(item.station)
        if previous is None or distance < previous[0]:
            selected[item.station] = (distance, item)
    return sorted((value[1] for value in selected.values()), key=lambda item: item.station)


def _sample_grid(vel_path: Path, ang_path: Path, projection_path: Path, lat: float, lon: float) -> GridSample | None:
    speed = read_ascii_grid(vel_path)
    direction = read_ascii_grid(ang_path)
    crs = projection_path.read_text(encoding="utf-8", errors="replace")
    transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    x, y = transformer.transform(lon, lat)
    left = speed.xll - (0.5 * speed.cellsize if speed.x_centered else 0.0)
    top = speed.yll + (speed.nrows - (0.5 if speed.y_centered else 0.0)) * speed.cellsize
    column = math.floor((x - left) / speed.cellsize)
    row = math.floor((top - y) / speed.cellsize)
    if not (0 <= row < speed.nrows and 0 <= column < speed.ncols):
        return None
    speed_value = float(speed.values[row, column])
    direction_value = float(direction.values[row, column])
    if not math.isfinite(speed_value) or not math.isfinite(direction_value) or speed_value < 0:
        return None
    if math.isclose(speed_value, speed.nodata) or math.isclose(direction_value, direction.nodata):
        return None
    grid_x, grid_y = float(speed.x[column]), float(speed.y[row])
    true_direction = (direction_value + float(grid_north_bearing(crs, grid_x, grid_y))) % 360.0
    return GridSample(speed_value, true_direction, row, column, grid_x, grid_y, math.hypot(x - grid_x, y - grid_y))


def _validation_status(item: Observation, target: datetime, height: int, window: int, tolerance: float) -> str:
    if abs((item.time_utc - target).total_seconds()) > window:
        return "time_mismatch"
    if abs(item.height_m - height) > tolerance:
        return "height_mismatch"
    return "matched"


def _metrics(rows: list[dict[str, object]]) -> dict[str, object]:
    valid = [row for row in rows if row.get("status") == "ok"]
    return {
        "validation_rows": len(rows),
        "valid_validation_rows": len(valid),
        "validation_status_counts": dict(Counter(str(row["status"]) for row in rows)),
        "mean_abs_speed_error_ms": mean(abs(float(row["speed_error_ms"])) for row in valid) if valid else None,
        "speed_bias_ms": mean(float(row["speed_error_ms"]) for row in valid) if valid else None,
        "speed_rmse_ms": math.sqrt(mean(float(row["speed_error_ms"]) ** 2 for row in valid)) if valid else None,
        "mean_abs_direction_error_deg": mean(float(row["direction_error_deg"]) for row in valid) if valid else None,
    }


class TurbulenceBatchRunner:
    def __init__(self, config: AppConfig, progress_callback=None):
        self.config = config
        self.progress_callback = progress_callback
        self.windninja = WindNinjaRunner(config.windninja_exe)

    def _progress(self, message: str) -> None:
        print(message, flush=True)
        if self.progress_callback is not None:
            self.progress_callback(message)

    @windows_safe_output
    def run(
        self,
        input_dir: Path,
        output_dir: Path,
        heights: list[int],
        resolutions: list[int],
        buffer_km: float,
        manual_bounds: GeoBounds | None,
        generate_kmz: bool,
        time_range: tuple[datetime, datetime] | None = None,
        max_cases: int | None = 1,
        validation_window_seconds: int = 120,
        selected_product: str | None = None,
        non_neutral_stability: bool = False,
        background_max_height_m: float = 200.0,
        validation_height_tolerance_m: float = 10.0,
        dem_override: Path | None = None,
        instrument_policy: InstrumentPolicy | None = None,
        reference_height_m: float = 50.0,
        roughness_m: float = 0.1,
        displacement_height_m: float = 0.0,
        allow_profile_extrapolation: bool = False,
    ) -> TurbulenceBatchResult:
        if not heights or not resolutions:
            raise ValueError("Turbulence mode requires at least one height and resolution")
        if any(value <= 0 for value in (*heights, *resolutions)):
            raise ValueError("Heights and resolutions must be positive")
        if (not math.isfinite(background_max_height_m) or background_max_height_m <= 0
                or not math.isfinite(validation_height_tolerance_m) or validation_height_tolerance_m < 0
                or validation_window_seconds < 0 or (max_cases is not None and max_cases <= 0)):
            raise ValueError("Invalid turbulence background or validation limits")
        if non_neutral_stability:
            raise ValueError("WindNinja momentum turbulence mode does not support non-neutral stability")
        output_dir = output_dir.expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        policy = instrument_policy or InstrumentPolicy(
            windmaster_direction_reference=WindDirectionReference.FROM_TRUE_NORTH,
            uav_direction_reference=WindDirectionReference.FROM_TRUE_NORTH,
            windmaster_speed_unit=WindSpeedUnit.METERS_PER_SECOND,
            uav_speed_unit=WindSpeedUnit.METERS_PER_SECOND,
            windmaster_height_reference=RadarHeightReference.AGL,
            windmaster_is_geographic_wind=True,
            windmaster_motion_compensated=True,
            uav_is_geographic_wind=True,
            uav_motion_compensated=True,
        )
        catalog = load_instrument_catalog(input_dir, selected_product=selected_product, policy=policy)
        source_config = self.config.sources.get("windmaster")
        min_rate = source_config.min_obtain_rate if source_config is not None else 0.0
        points = [(record.lat, record.lon) for record in catalog.records]
        required_bounds = manual_bounds or bounds_from_points(points, buffer_km)
        if dem_override is not None and not DemManager.covers(dem_override, required_bounds):
            raise ValueError("Selected DEM does not cover the requested turbulence domain")
        dem = DemManager(self.config.dem).get_dem(
            [dem_override] if dem_override is not None else [], catalog.records, buffer_km, manual_bounds
        )
        dem = solver_safe_dem(dem, output_dir)
        normalized = normalize_catalog(catalog, dem, policy)
        if normalized.blocking_reasons:
            raise ValueError("Instrument definitions require selection or confirmation: " + "; ".join(normalized.blocking_reasons))
        fixed = _fixed_observations(normalized.observations, background_max_height_m, min_rate)
        validation = [item for item in normalized.observations if item.source == "uav" and item.moving and item.height_m > 0]
        if not fixed:
            raise ValueError("No fixed WindMaster observations available for turbulence mode")
        if not validation:
            raise ValueError("No UAV validation observations available for turbulence mode")
        start, end = time_range if time_range else (min(item.time_utc for item in fixed), max(item.time_utc for item in fixed))
        targets = sorted({TimeAxis.normalize(item.time_utc) for item in fixed if start <= item.time_utc <= end})
        if max_cases is not None:
            targets = targets[:max_cases]
        if not targets:
            raise ValueError("No WindMaster target times remain in the requested range")
        station_writer = StationFileWriter(self.config.temperature, self.config.windninja.station_radius_of_influence_km)
        station_lists = StationListWriter()
        config_writer = WindNinjaConfigWriter()
        netcdf_writer = Wind3DNetcdfWriter(self.config.output.netcdf_compression)
        kmz_writer = KmzWriter()
        rows: list[dict[str, object]] = []
        netcdf_files: list[Path] = []
        kmz_files: list[Path] = []
        case_reports: list[dict[str, object]] = []
        total = len(targets) * len(heights) * len(resolutions)
        completed = 0
        for target in targets:
            driver = _select_fixed(target, fixed, validation_window_seconds)
            if not driver:
                continue
            profile = build_station_profiles(
                driver, reference_height_m=reference_height_m, roughness_m=roughness_m,
                displacement_height_m=displacement_height_m,
                allow_log_extrapolation=allow_profile_extrapolation,
            )
            if not profile.stations:
                raise ValueError(f"No usable station profiles at {reference_height_m:g} m above vegetation: {profile.as_dict()}")
            checks = [item for item in validation if abs((item.time_utc - target).total_seconds()) <= validation_window_seconds]
            for height in heights:
                for resolution in resolutions:
                    completed += 1
                    label = f"[{completed}/{total}] {target:%Y-%m-%dT%H:%M:%SZ} height={height}m resolution={resolution}m"
                    self._progress(f"[wind3d-turbulence] {label} solving RANS")
                    case_dir = output_dir / "cases" / TimeAxis.format_time_for_filename(target) / f"h{height:03d}m" / f"r{resolution:04d}m"
                    station_dir = case_dir / "stations"
                    files = [station_writer.write(item, station_dir / f"{safe_station_name(item.station)}.csv", target, False) for item in profile.stations]
                    station_list = station_lists.write(files, case_dir / "stations_list.csv")
                    background_speed = profile.background_speed_ms
                    background_direction = profile.background_direction_deg
                    background_height = reference_height_m
                    (case_dir / "station_profiles.json").write_text(json.dumps(profile.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
                    config_file = config_writer.write(
                        case_dir / "windninja.cfg",
                        dem,
                        station_list,
                        case_dir,
                        target,
                        height,
                        resolution,
                        self.config.windninja.vegetation,
                        self.config.windninja.num_threads,
                        generate_kmz,
                        diurnal_winds=self.config.windninja.diurnal_winds,
                        non_neutral_stability=non_neutral_stability,
                        alpha_stability=self.config.windninja.alpha_stability,
                        input_wind_height_m=background_height,
                        output_buffer_clipping_pct=self.config.windninja.output_buffer_clipping_pct,
                        turbulence_output=True,
                        input_speed_ms=background_speed,
                        input_direction_deg=background_direction,
                    )
                    result = self.windninja.run(config_file, case_dir)
                    if result.kmz is None:
                        raise FileNotFoundError(f"Momentum solver produced no native turbulence KMZ in {case_dir}")
                    nc_path = output_dir / "nc" / make_netcdf_filename(target, resolution, height)
                    netcdf_writer.write(result.vel_asc, result.ang_asc, result.projection, nc_path, target, height, resolution, solver_mode="turbulence_rans_domain_average")
                    netcdf_files.append(nc_path)
                    kmz_path = None
                    if generate_kmz:
                        kmz_path = output_dir / "kmz" / f"{nc_path.stem}.kmz"
                        kmz_writer.write(nc_path, kmz_path, result.kmz)
                        kmz_files.append(kmz_path)
                    turbulence_files = [
                        path.name
                        for path in case_dir.rglob("*")
                        if path.is_file() and any(token in path.name.lower() for token in ("turbulence", "fluctuation", "foam", "k_", "epsilon", "nut"))
                    ]
                    case_rows: list[dict[str, object]] = []
                    for item in checks:
                        status = _validation_status(item, target, height, validation_window_seconds, validation_height_tolerance_m)
                        sampled = (_sample_grid(result.vel_asc, result.ang_asc, result.projection, item.lat, item.lon)
                                   if status == "matched" else None)
                        if status == "matched":
                            status = "ok" if sampled is not None else "outside_or_invalid_grid"
                        row: dict[str, object] = {
                            "target_time_utc": target.isoformat().replace("+00:00", "Z"),
                            "height_m": height,
                            "resolution_m": resolution,
                            "validation_station": item.station,
                            "validation_time_utc": item.time_utc.isoformat().replace("+00:00", "Z"),
                            "observed_time_raw": item.metadata.get("time_raw"),
                            "observed_time_source_field": item.metadata.get("time_source_field"),
                            "observed_time_source_timezone_offset_hours": item.metadata.get("time_source_timezone_offset_hours"),
                            "observed_speed_ms": item.speed_ms,
                            "observed_direction_deg": item.direction_deg,
                            "observed_height_m": item.height_m,
                            "observed_agl_m": item.metadata.get("instrument_agl_m"),
                            "height_method": item.metadata.get("height_method"),
                            "raw_gps_altitude_m": item.metadata.get("raw_gps_altitude_m"),
                            "raw_relative_height_m": item.metadata.get("raw_relative_height_m"),
                            "terrain_elevation_m": item.metadata.get("dem_elevation_m"),
                            "canopy_height_m": policy.canopy_height_m,
                            "height_offset_m": item.height_m - height,
                            "time_offset_seconds": (item.time_utc - target).total_seconds(),
                            "latitude": item.lat,
                            "longitude": item.lon,
                            "status": status,
                            "turbulence_files": ";".join(turbulence_files),
                        }
                        if sampled is not None:
                            model_speed, model_direction = sampled.speed_ms, sampled.direction_deg
                            row.update({
                                "model_speed_ms": model_speed,
                                "model_direction_deg": model_direction,
                                "speed_error_ms": model_speed - item.speed_ms,
                                "direction_error_deg": _direction_error(model_direction, item.direction_deg),
                                "sample_grid_row": sampled.row,
                                "sample_grid_column": sampled.column,
                                "sample_grid_x_m": sampled.x,
                                "sample_grid_y_m": sampled.y,
                                "spatial_offset_m": sampled.spatial_offset_m,
                                "sampling_method": "containing grid cell (piecewise constant)",
                            })
                        rows.append(row)
                        case_rows.append(row)
                    case_reports.append({
                        "target_time_utc": target.isoformat().replace("+00:00", "Z"),
                        "height_m": height,
                        "resolution_m": resolution,
                        "background_observation_count": len(driver),
                        "background_heights_m": sorted({item.height_m for item in driver}),
                        "background_speed_ms": background_speed,
                        "background_direction_deg": background_direction,
                        "background_height_m": background_height,
                        "station_profiles": profile.as_dict(),
                        "background_max_time_offset_seconds": max(abs((item.time_utc - target).total_seconds()) for item in driver),
                        "native_turbulence_kmz": str(result.kmz),
                        "time_mismatch_count": len(validation) - len(checks),
                        **_metrics(case_rows),
                    })
        validation_csv = output_dir / "validation.csv"
        validation_csv.parent.mkdir(parents=True, exist_ok=True)
        fields = ["target_time_utc", "height_m", "resolution_m", "validation_station", "validation_time_utc", "observed_time_raw", "observed_time_source_field", "observed_time_source_timezone_offset_hours",
                  "observed_speed_ms", "observed_direction_deg", "observed_height_m", "height_offset_m",
                  "time_offset_seconds", "latitude", "longitude", "status", "turbulence_files",
                  "model_speed_ms", "model_direction_deg", "speed_error_ms", "direction_error_deg",
                  "sample_grid_row", "sample_grid_column", "sample_grid_x_m", "sample_grid_y_m",
                  "spatial_offset_m", "sampling_method", "observed_agl_m", "height_method",
                  "raw_gps_altitude_m", "raw_relative_height_m", "terrain_elevation_m", "canopy_height_m"]
        with validation_csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        report = {
            "mode": "turbulence_rans_domain_average",
            "initialization_method": "domainAverageInitialization",
            "background_aggregation": "interpolate u/v within each station profile at common vegetation-top height; equal physical-station vector mean",
            "profile_reference_height_m": reference_height_m,
            "profile_roughness_m": roughness_m,
            "profile_roughness_scope": "station-profile extrapolation only; native RANS roughness follows configured vegetation",
            "profile_displacement_height_m": displacement_height_m,
            "allow_profile_extrapolation": allow_profile_extrapolation,
            "background_source": "windmaster_fixed",
            "validation_source": "uav_moving_independent",
            "selected_product": catalog.selected_product,
            "non_neutral_stability": non_neutral_stability,
            "diurnal_winds": self.config.windninja.diurnal_winds,
            "background_max_height_m": background_max_height_m,
            "background_min_obtain_rate": min_rate,
            "validation_window_seconds": validation_window_seconds,
            "validation_height_tolerance_m": validation_height_tolerance_m,
            "validation_quantity": "mean horizontal wind, not turbulence",
            "uav_height_reference": policy.uav_height_reference.value,
            "instrument_policy": asdict(policy),
            "model_height_reference": "above vegetation top",
            "height_matching_assumption": "all observations converted from explicit reference to AGL then AGL minus canopy; compared at vegetation-top reference",
            "instrument_assumptions": {
                "wind_direction": "meteorological from direction, true north",
                "wind_speed_unit": "m/s",
                "geographic_wind_and_motion_compensation": "assumed confirmed for both instruments",
                "windmaster_height": policy.windmaster_height_reference.value,
                "canopy_height_m": policy.canopy_height_m,
            },
            "native_turbulence_quantity": "column maximum sqrt(2*k/3), not turbulence at output height",
            "instrument_rejections": dict(catalog.rejected),
            "normalization_rejections": dict(normalized.rejected),
            "bounds": required_bounds.as_dict(),
            "dem_file": str(dem),
            "cases": len(targets),
            "completed": len(netcdf_files),
            **_metrics(rows),
            "case_reports": case_reports,
            "netcdf_count": len(netcdf_files),
            "kmz_count": len(kmz_files),
        }
        report_json = output_dir / "turbulence_report.json"
        report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return TurbulenceBatchResult(output_dir, len(targets), len(netcdf_files), validation_csv, report_json,
                                     tuple(netcdf_files), tuple(kmz_files), tuple(fixed), tuple(validation))
