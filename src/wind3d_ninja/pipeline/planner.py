from __future__ import annotations

import sys
from dataclasses import asdict
from datetime import datetime
from math import isfinite
from pathlib import Path

from ..config.models import AppConfig
from ..dem.manager import DemManager
from ..discovery.scanner import DataScanner
from ..observations.models import Observation
from ..observations.instruments import InstrumentPolicy
from ..postprocess.netcdf_writer import make_netcdf_filename
from ..observations.coordinator import complete_coordinates
from ..timeplan.axis import TimeAxis
from ..utils.geometry import GeoBounds, bounds_from_points
from ..utils.validation import validate_observations
from .inspector import InputInspector


class PipelinePlanner:
    def __init__(self, config: AppConfig):
        self.config = config

    def plan(
        self,
        input_dir: Path,
        heights: list[int],
        mesh_resolutions_m: list[int],
        buffer_km: float,
        manual_bounds: GeoBounds | None,
        time_range: tuple[datetime, datetime] | None = None,
        dem_override: Path | None = None,
        solver_mode: str = "stations",
        background_speed_ms: float | None = None,
        background_direction_deg: float | None = None,
        background_height_m: float | None = None,
        simulation_time: datetime | None = None,
        instrument_policy: InstrumentPolicy | None = None,
    ) -> dict[str, object]:
        if solver_mode == "turbulence":
            scan = DataScanner(input_dir).scan()
            if time_range is not None:
                raise ValueError("Turbulence mode uses --simulation-time and cannot use --time-range")
            self._validate_turbulence_inputs(
                background_speed_ms, background_direction_deg, background_height_m, simulation_time
            )
            dem_manager = DemManager(self.config.dem)
            if manual_bounds is not None:
                candidates = [dem_override, *scan.dem] if dem_override is not None else scan.dem
                dem_path = dem_manager.get_dem(candidates, [], buffer_km, manual_bounds)
            elif dem_override is not None or scan.dem:
                dem_path = dem_manager.get_local_dem(scan.dem, dem_override)
            else:
                raise ValueError("Turbulence mode requires a DEM file or --bounds to download one")
            products = [
                make_netcdf_filename(simulation_time, mesh_resolution_m, height)
                for height in heights
                for mesh_resolution_m in mesh_resolutions_m
            ]
            return {
                "input_dir": str(input_dir.resolve()),
                "files": scan.as_dict(),
                "solver_mode": "turbulence",
                "dem_path": dem_path,
                "times_utc": [simulation_time],
                "heights_m": heights,
                "mesh_resolutions_m": mesh_resolutions_m,
                "background_wind": {
                    "speed_ms": background_speed_ms,
                    "direction_from_deg": background_direction_deg,
                    "height_m": background_height_m,
                },
                "run_count": len(products),
                "products": products,
                "turbulence_product": "WindNinja native KMZ",
                "instrument_time_policy": None,
            }
        if solver_mode != "stations":
            raise ValueError("solver_mode must be 'stations' or 'turbulence'")
        inspector = InputInspector(self.config, instrument_policy)
        loaded = inspector.load(input_dir)
        start_time, end_time = (
            (TimeAxis.normalize(time_range[0]), TimeAxis.normalize(time_range[1]))
            if time_range is not None
            else (None, None)
        )
        if start_time is not None and start_time > end_time:
            raise ValueError("Time range start must not be later than end")

        def selected(value: datetime) -> bool:
            normalized = TimeAxis.normalize(value)
            return start_time is None or start_time <= normalized <= end_time

        fixed_observations = [
            item for item in loaded.fixed_observations if selected(item.time_utc)
        ]
        raw_uav = [
            record
            for _, records in loaded.uav_inputs
            for record in records
            if selected(record.time_utc)
        ]
        located_records = [*fixed_observations, *raw_uav]
        if not located_records:
            raise ValueError("No observation times fall inside the requested time range")
        points = [
            (float(item.lat), float(item.lon))
            for item in located_records
            if item.lat is not None and item.lon is not None
        ]
        required_bounds = manual_bounds or bounds_from_points(points, buffer_km)
        self._progress(f"[wind3d] Planning: resolving DEM with buffer={buffer_km:g} km")
        dem_candidates = list(loaded.scan.dem)
        if dem_override is not None:
            dem_candidates.insert(0, dem_override.expanduser().resolve())
        dem_path = DemManager(self.config.dem).get_dem(
            dem_candidates,
            located_records,
            buffer_km,
            manual_bounds,
        )
        self._progress(f"[wind3d] Planning: DEM ready ({dem_path.name})")
        uav_observations: list[Observation] = []
        for reader, records in loaded.uav_inputs:
            selected_records = [record for record in records if selected(record.time_utc)]
            if selected_records:
                uav_observations.extend(reader.read(selected_records, dem_path=dem_path))
        observations = validate_observations(
            complete_coordinates([*fixed_observations, *uav_observations])
        )
        times = TimeAxis.extract_unique_times(observations)
        if not times:
            raise ValueError("No valid observation times remain after DEM/UAV filtering")
        products = [
            make_netcdf_filename(target_time, mesh_resolution_m, height)
            for target_time in times
            for height in heights
            for mesh_resolution_m in mesh_resolutions_m
        ]
        return {
            "input_dir": str(input_dir.resolve()),
            "files": loaded.scan.as_dict(),
            "fixed_observation_count": len(fixed_observations),
            "uav_metadata_count": len(raw_uav),
            "uav_observation_count": len(uav_observations),
            "observation_count": len(observations),
            "dem_path": dem_path,
            "times_utc": times,
            "time_range_utc": time_range,
            "instrument_time_policy": asdict(inspector.effective_instrument_policy().time_policy),
            "heights_m": heights,
            "mesh_resolutions_m": mesh_resolutions_m,
            "bounds": required_bounds.as_dict(),
            "run_count": len(products),
            "products": products,
        }

    @staticmethod
    def _progress(message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    @staticmethod
    def _validate_turbulence_inputs(
        speed: float | None,
        direction: float | None,
        height: float | None,
        simulation_time: datetime | None,
    ) -> None:
        if simulation_time is None or simulation_time.tzinfo is None or simulation_time.utcoffset() is None:
            raise ValueError("Turbulence mode requires a timezone-aware --simulation-time")
        values = (speed, direction, height)
        if any(value is None or not isfinite(value) for value in values):
            raise ValueError("Turbulence mode requires finite background speed, direction, and height")
        if speed <= 0 or height <= 0:
            raise ValueError("Turbulence background speed and height must be greater than zero")
        if not 0 <= direction < 360:
            raise ValueError("Turbulence background direction must be in [0, 360)")
