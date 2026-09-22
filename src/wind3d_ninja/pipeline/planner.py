from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from ..config.models import AppConfig
from ..dem.manager import DemManager
from ..observations.models import Observation
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
    ) -> dict[str, object]:
        loaded = InputInspector(self.config).load(input_dir)
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
            "heights_m": heights,
            "mesh_resolutions_m": mesh_resolutions_m,
            "bounds": required_bounds.as_dict(),
            "run_count": len(products),
            "products": products,
        }

    @staticmethod
    def _progress(message: str) -> None:
        print(message, file=sys.stderr, flush=True)
