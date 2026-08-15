from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer

from ..config.models import SourceConfig
from ..observations.models import Observation
from ..utils.timezone import parse_datetime_utc
from .base import as_float, find_column


@dataclass(frozen=True)
class RawUavRecord:
    station: str
    flight_id: str
    time_utc: datetime
    lat: float
    lon: float
    gps_altitude_m: float
    relative_height_m: float
    takeoff_elevation_m: float | None
    speed_ms: float
    direction_deg: float
    temperature_c: float | None
    row: str


class UavReader:
    def __init__(self, path: Path, config: SourceConfig):
        self.path = path
        self.config = config
        self._raw: list[RawUavRecord] | None = None

    def read_metadata(self) -> list[RawUavRecord]:
        if self._raw is not None:
            return list(self._raw)
        try:
            frame = pd.read_excel(self.path, sheet_name=0).dropna(how="all")
        except ValueError:
            frame = pd.read_csv(
                self.path,
                sep="\t",
                encoding="gb18030",
                engine="python",
            ).dropna(how="all")
        positional = frame.shape[1] >= 22
        time_col = frame.columns[21] if positional else find_column(frame, "time")
        lat_col = frame.columns[4] if positional else find_column(frame, "lat")
        lon_col = frame.columns[5] if positional else find_column(frame, "lon")
        speed_col = frame.columns[11] if positional else find_column(frame, "speed")
        direction_col = frame.columns[12] if positional else find_column(frame, "direction")
        altitude_col = frame.columns[6] if positional else find_column(frame, "gps_altitude")
        relative_height_col = frame.columns[7] if positional else find_column(frame, "relative_height")
        temp_col = frame.columns[8] if positional else find_column(frame, "temperature", required=False)
        station_col = frame.columns[1] if positional else find_column(frame, "station", required=False)
        flight_keys = (
            frame[station_col].map(
                lambda value: self.path.stem if pd.isna(value) else str(value).strip()
            )
            if station_col is not None
            else pd.Series(self.path.stem, index=frame.index)
        )
        records: list[RawUavRecord] = []
        for index, row in frame.iterrows():
            try:
                time_utc = parse_datetime_utc(row[time_col], self.config.tz_offset_hours)
                lat = as_float(row[lat_col], "latitude")
                lon = as_float(row[lon_col], "longitude")
                altitude = as_float(row[altitude_col], "GPS altitude")
                relative_height = as_float(row[relative_height_col], "relative height")
                speed = as_float(row[speed_col], "wind speed")
                direction = as_float(row[direction_col], "wind direction")
            except (TypeError, ValueError):
                continue
            temperature = None
            if temp_col is not None and not pd.isna(row[temp_col]):
                temperature = as_float(row[temp_col], "temperature")
            flight_id = str(flight_keys.loc[index])
            station = f"{self.config.station_prefix}_{flight_id}_{int(index):05d}"
            records.append(
                RawUavRecord(
                    station=station,
                    flight_id=flight_id,
                    time_utc=time_utc,
                    lat=lat,
                    lon=lon,
                    gps_altitude_m=altitude,
                    relative_height_m=relative_height,
                    takeoff_elevation_m=None,
                    speed_ms=speed,
                    direction_deg=direction,
                    temperature_c=temperature,
                    row=str(index),
                )
            )
        self._raw = records
        return list(records)

    def read(
        self,
        records: Iterable[RawUavRecord] | None = None,
        *,
        dem_path: Path,
    ) -> list[Observation]:
        """Convert parsed UAV records to observations.

        ``dem_path`` is required because the runner resolves one terrain DEM
        for the complete run.  AGL is calculated from the UAV GPS altitude,
        relative height, and the sampled DEM elevation.
        """
        output: list[Observation] = []
        with rasterio.open(dem_path) as dem_dataset:
            if dem_dataset.crs is None:
                raise ValueError(f"DEM has no CRS: {dem_path}")
            transformer = Transformer.from_crs(
                "EPSG:4326",
                dem_dataset.crs,
                always_xy=True,
            )
            for raw in records if records is not None else self.read_metadata():
                x, y = transformer.transform(raw.lon, raw.lat)
                dem_elevation = float(next(dem_dataset.sample([(x, y)]))[0])
                if not np.isfinite(dem_elevation) or (
                    dem_dataset.nodata is not None
                    and np.isclose(dem_elevation, dem_dataset.nodata)
                ):
                    continue
                agl_raw = raw.gps_altitude_m + raw.relative_height_m - dem_elevation
                if agl_raw < self.config.agl_threshold_drop_m:
                    continue
                agl = (
                    self.config.agl_threshold_fix_m
                    if agl_raw <= 0.0
                    else agl_raw
                )
                relative_plus_gps = raw.gps_altitude_m + raw.relative_height_m
                metadata: dict[str, Any] = {
                    "input_file": str(self.path),
                    "row": raw.row,
                    "flight_id": raw.flight_id,
                    "current_altitude_m": raw.gps_altitude_m,
                    "gps_altitude_m": raw.gps_altitude_m,
                    "relative_height_m": raw.relative_height_m,
                    "relative_plus_gps_altitude_m": relative_plus_gps,
                    "dem_elevation_m": dem_elevation,
                    "agl_raw_m": agl_raw,
                    "height_method": "gps_current_altitude_plus_relative_height_minus_dem_elevation",
                }
                if raw.takeoff_elevation_m is not None:
                    metadata["takeoff_elevation_m"] = raw.takeoff_elevation_m
                output.append(
                    Observation(
                        source="uav",
                        station=raw.station,
                        time_utc=raw.time_utc,
                        lat=raw.lat,
                        lon=raw.lon,
                        height_m=agl,
                        speed_ms=raw.speed_ms,
                        direction_deg=raw.direction_deg,
                        temperature_c=raw.temperature_c,
                        moving=True,
                        metadata=metadata,
                    )
                )
        return sorted(output, key=lambda item: (item.time_utc, item.station))
