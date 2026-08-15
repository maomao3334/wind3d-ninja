from __future__ import annotations

import csv
import json
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from ..config.models import AppConfig
from ..dem.manager import DemManager
from ..manifest.builder import ManifestBuilder
from ..observations.coordinator import complete_coordinates
from ..observations.models import Observation
from ..postprocess.kmz_writer import KmzWriter
from ..postprocess.netcdf_writer import Wind3DNetcdfWriter, make_netcdf_filename
from ..stations.list_file import StationListWriter
from ..stations.writer import StationFileWriter, safe_station_name
from ..timeplan.axis import TimeAxis
from ..timeplan.selector import ObservationSelector
from ..utils.geometry import GeoBounds
from ..utils.validation import observation_summary, validate_observations
from ..windninja.config_writer import WindNinjaConfigWriter
from ..windninja.runner import WindNinjaRunner
from .inspector import InputInspector


@dataclass(frozen=True)
class PipelineRunResult:
    output_dir: Path
    manifest: Path
    netcdf_files: tuple[Path, ...]
    kmz_files: tuple[Path, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "output_dir": str(self.output_dir),
            "manifest": str(self.manifest),
            "netcdf_files": [str(path) for path in self.netcdf_files],
            "kmz_files": [str(path) for path in self.kmz_files],
        }


class PipelineRunner:
    def __init__(self, config: AppConfig, windninja_runner: WindNinjaRunner | None = None):
        self.config = config
        self.windninja_runner = windninja_runner or WindNinjaRunner(config.windninja_exe)

    def run(
        self,
        input_dir: Path,
        output_dir: Path,
        output_heights: list[int],
        mesh_resolutions_m: int | Iterable[int],
        buffer_km: float,
        manual_bounds: GeoBounds | None,
        generate_kmz: bool,
        dem_override: Path | None = None,
        selected_times: Iterable[datetime] | None = None,
        time_range: tuple[datetime, datetime] | None = None,
    ) -> PipelineRunResult:
        resolutions = (
            [mesh_resolutions_m]
            if isinstance(mesh_resolutions_m, int)
            else list(dict.fromkeys(int(value) for value in mesh_resolutions_m))
        )
        if not resolutions or any(value <= 0 for value in resolutions):
            raise ValueError("At least one positive mesh resolution is required")
        output_dir = output_dir.expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        manifest = ManifestBuilder(output_dir / "manifest.json")
        try:
            self._progress(f"[wind3d] Scanning input: {input_dir.resolve()}")
            loaded = InputInspector(self.config).load(input_dir)
            requested = (
                {TimeAxis.normalize(value) for value in selected_times}
                if selected_times is not None
                else None
            )
            normalized_range = (
                (TimeAxis.normalize(time_range[0]), TimeAxis.normalize(time_range[1]))
                if time_range is not None
                else None
            )
            if normalized_range is not None and normalized_range[0] > normalized_range[1]:
                raise ValueError("Time range start must not be later than end")

            def time_selected(value: datetime) -> bool:
                normalized = TimeAxis.normalize(value)
                if requested is not None and normalized not in requested:
                    return False
                if normalized_range is not None:
                    return normalized_range[0] <= normalized <= normalized_range[1]
                return True

            fixed_observations = [
                item
                for item in loaded.fixed_observations
                if time_selected(item.time_utc)
            ]
            filtered_uav_inputs = [
                (
                    reader,
                    [
                        record
                        for record in records
                        if time_selected(record.time_utc)
                    ],
                )
                for reader, records in loaded.uav_inputs
            ]
            raw_uav = [record for _, records in filtered_uav_inputs for record in records]
            located_records = [*fixed_observations, *raw_uav]
            self._progress(f"[wind3d] Resolving DEM with buffer={buffer_km:g} km")
            dem_manager = DemManager(self.config.dem)
            explicit_dem = dem_override.expanduser().resolve() if dem_override is not None else None
            if explicit_dem is not None and manual_bounds is None and dem_manager.covers_points(explicit_dem, located_records):
                dem_path = explicit_dem
            else:
                dem_candidates = list(loaded.scan.dem)
                if explicit_dem is not None:
                    dem_candidates.insert(0, explicit_dem)
                dem_path = dem_manager.get_dem(
                    dem_candidates,
                    located_records,
                    buffer_km,
                    manual_bounds,
                )
            uav_observations: list[Observation] = []
            for reader, records in filtered_uav_inputs:
                if not records:
                    continue
                uav_observations.extend(reader.read(records, dem_path=dem_path))
            observations = validate_observations(
                complete_coordinates([*fixed_observations, *uav_observations])
            )
            times = TimeAxis.extract_unique_times(observations)
            if requested is not None:
                times = [value for value in times if value in requested]
            if normalized_range is not None:
                times = [
                    value
                    for value in times
                    if normalized_range[0] <= value <= normalized_range[1]
                ]
            if not times:
                raise ValueError("No target times remain after filtering")
            total_runs = len(times) * len(output_heights) * len(resolutions)
            self._progress(
                f"[wind3d] Prepared {len(observations)} observations, {len(times)} times, "
                f"{len(output_heights)} heights, {len(resolutions)} resolutions; "
                f"total runs={total_runs}"
            )
            dem_output = output_dir / "dem" / dem_path.name
            dem_output.parent.mkdir(parents=True, exist_ok=True)
            if dem_output.resolve() != dem_path.resolve():
                shutil.copy2(dem_path, dem_output)
            self._write_observations(observations, output_dir / "observations.csv")
            manifest.set("input_dir", input_dir.resolve())
            manifest.set("dem", dem_path.resolve())
            manifest.set("observation_summary", observation_summary(observations))
            manifest.set("times_utc", times)
            manifest.set("time_range_utc", normalized_range)
            manifest.set("heights_m", output_heights)
            manifest.set("mesh_resolutions_m", resolutions)
            manifest.set("buffer_km", buffer_km)
            manifest.set(
                "assumptions",
                {
                    "temperature_c": self.config.temperature.configured_assumption_c,
                    "temperature_status": "configured_model_assumption_not_observed",
                    "cloud_cover_pct": self.config.temperature.cloud_cover_assumption_pct,
                },
            )
            for kind, paths in loaded.scan.as_dict().items():
                for value in paths:
                    manifest.add_input(Path(value), kind)
            manifest.add_input(dem_path, "dem_used")
            selector = ObservationSelector(self.config.quality_rules)
            station_writer = StationFileWriter(self.config.temperature)
            station_list_writer = StationListWriter()
            config_writer = WindNinjaConfigWriter()
            netcdf_writer = Wind3DNetcdfWriter(self.config.output.netcdf_compression)
            kmz_writer = KmzWriter()
            netcdf_files: list[Path] = []
            kmz_files: list[Path] = []
            run_index = 0
            for target_time in times:
                selected = selector.select_for_time(target_time, observations)
                if not selected:
                    continue
                time_key = TimeAxis.format_time_for_filename(target_time)
                for height in output_heights:
                    height_key = f"h{height:03d}m"
                    station_dir = output_dir / "stations" / time_key / height_key
                    station_files: list[Path] = []
                    for index, observation in enumerate(selected):
                        filename = f"{safe_station_name(observation.station)}_{index:04d}.csv"
                        station_files.append(
                            station_writer.write(observation, station_dir / filename, target_time)
                        )
                    station_list = station_list_writer.write(
                        station_files,
                        station_dir / "stations_list.csv",
                    )
                    manifest.add_output(station_list, "stations")
                    for mesh_resolution_m in resolutions:
                        run_index += 1
                        run_label = (
                            f"[{run_index}/{total_runs}] "
                            f"{target_time:%Y-%m-%dT%H:%M:%SZ} | "
                            f"height={height}m | resolution={mesh_resolution_m}m"
                        )
                        self._progress(f"{run_label} | starting WindNinja")
                        resolution_key = f"r{mesh_resolution_m:04d}m"
                        work_dir = output_dir / "windninja" / time_key / height_key / resolution_key
                        runtime_station_dir = work_dir / "stations"
                        runtime_station_files: list[Path] = []
                        for index, observation in enumerate(selected):
                            filename = f"{safe_station_name(observation.station)}_{index:04d}.csv"
                            runtime_station_files.append(
                                station_writer.write(
                                    observation,
                                    runtime_station_dir / filename,
                                    target_time,
                                    False,
                                )
                            )
                        runtime_station_list = station_list_writer.write(
                            runtime_station_files,
                            work_dir / "runtime_stations_list.csv",
                        )
                        config_file = config_writer.write(
                            work_dir / "windninja.cfg",
                            dem_path,
                            runtime_station_list,
                            work_dir,
                            target_time,
                            height,
                            mesh_resolution_m,
                            self.config.windninja.vegetation,
                            self.config.windninja.num_threads,
                            generate_kmz,
                        )
                        wind_result = self.windninja_runner.run(config_file, work_dir)
                        self._progress(f"{run_label} | WindNinja complete; writing NetCDF")
                        nc_path = output_dir / "nc" / make_netcdf_filename(
                            target_time,
                            mesh_resolution_m,
                            height,
                        )
                        netcdf_writer.write(
                            wind_result.vel_asc,
                            wind_result.ang_asc,
                            wind_result.projection,
                            nc_path,
                            target_time,
                            height,
                            mesh_resolution_m,
                        )
                        netcdf_files.append(nc_path)
                        manifest.add_output(runtime_station_list, "windninja_runtime_stations")
                        manifest.add_output(config_file, "windninja_config")
                        manifest.add_output(wind_result.log_file, "windninja_log")
                        manifest.add_output(wind_result.vel_asc, "wind_speed_ascii")
                        manifest.add_output(wind_result.ang_asc, "wind_direction_ascii")
                        manifest.add_output(nc_path, "netcdf")
                        kmz_path: Path | None = None
                        if generate_kmz:
                            kmz_path = output_dir / "kmz" / f"{nc_path.stem}.kmz"
                            kmz_writer.write(nc_path, kmz_path, wind_result.kmz)
                            kmz_files.append(kmz_path)
                            manifest.add_output(kmz_path, "kmz")
                        manifest.add_run(
                            {
                                "time_utc": target_time,
                                "height_m": height,
                                "mesh_resolution_m": mesh_resolution_m,
                                "observation_count": len(selected),
                                "command": wind_result.command,
                                "netcdf": nc_path,
                                "kmz": kmz_path,
                            }
                        )
                        self._progress(
                            f"{run_label} | complete | nc={nc_path.name}"
                            + (f" | kmz={kmz_path.name}" if kmz_path is not None else "")
                        )
            if not netcdf_files:
                raise RuntimeError("Pipeline completed without producing NetCDF files")
            manifest_path = manifest.write()
            return PipelineRunResult(
                output_dir,
                manifest_path,
                tuple(netcdf_files),
                tuple(kmz_files),
            )
        except Exception as error:
            self._progress(f"[wind3d] ERROR: {type(error).__name__}: {error}")
            manifest.add_error(error)
            manifest.write()
            raise

    @staticmethod
    def _progress(message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    @staticmethod
    def _write_observations(observations: Iterable[Observation], output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        fields = [
            "source",
            "station",
            "time_utc",
            "lat",
            "lon",
            "height_m",
            "speed_ms",
            "direction_deg",
            "temperature_c",
            "moving",
            "metadata",
        ]
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for observation in observations:
                row = observation.as_dict()
                row["metadata"] = json.dumps(row["metadata"], ensure_ascii=False, sort_keys=True)
                writer.writerow(row)
