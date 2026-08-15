from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from ..config.models import SourceConfig
from ..observations.models import Observation
from ..utils.timezone import parse_datetime_utc


ALIASES: dict[str, tuple[str, ...]] = {
    "relative_height": ("relativeheight", "relative_height", "heightabovehome", "agl_relative"),
    "time": ("time", "datetime", "timestamp", "date_time", "日期时间", "时间", "采集时间"),
    "lat": ("lat", "latitude", "纬度", "gpslat", "gps_lat"),
    "lon": ("lon", "lng", "longitude", "经度", "gpslon", "gps_lon"),
    "height": ("height", "height_m", "altitude", "agl", "高度", "测量高度"),
    "gps_altitude": ("gpsaltitude", "gps_altitude", "altitude", "海拔", "gps海拔", "高度"),
    "speed": ("speed", "speed_ms", "windspeed", "wind_speed", "风速", "水平风速"),
    "direction": ("direction", "direction_deg", "winddirection", "wind_direction", "风向"),
    "temperature": ("temperature", "temperature_c", "temp", "气温", "温度"),
    "station": ("station", "station_name", "name", "站名", "站点"),
}


def normalize_name(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value).strip().lower())


def find_column(frame: pd.DataFrame, field: str, required: bool = True) -> Any | None:
    normalized = {normalize_name(column): column for column in frame.columns}
    aliases = {normalize_name(alias) for alias in ALIASES[field]}
    for name, original in normalized.items():
        if name in aliases or any(alias and alias in name for alias in aliases):
            return original
    if required:
        raise ValueError(f"Missing {field} column; found {list(frame.columns)!r}")
    return None


def as_float(value: Any, field: str) -> float:
    number = float(value)
    if not np.isfinite(number):
        raise ValueError(f"{field} is not finite")
    return number


def _wide_height(column: Any, speed: bool) -> float | None:
    name = normalize_name(column)
    keywords = ("speed", "windspeed", "风速") if speed else ("direction", "winddirection", "风向")
    if not any(keyword in name for keyword in keywords):
        return None
    values = re.findall(r"\d+(?:\.\d+)?", name)
    return float(values[-1]) if values else None


def frame_to_observations(
    frame: pd.DataFrame,
    source: str,
    config: SourceConfig,
    path: Path,
    allow_missing_coordinates: bool,
) -> list[Observation]:
    frame = frame.dropna(how="all").copy()
    if frame.empty:
        return []
    time_col = find_column(frame, "time")
    lat_col = find_column(frame, "lat", required=False)
    lon_col = find_column(frame, "lon", required=False)
    station_col = find_column(frame, "station", required=False)
    temp_col = find_column(frame, "temperature", required=False)
    speed_col = find_column(frame, "speed", required=False)
    direction_col = find_column(frame, "direction", required=False)
    height_col = find_column(frame, "height", required=False)

    pairs: list[tuple[Any, Any, float | None]] = []
    if speed_col is not None and direction_col is not None:
        pairs.append((speed_col, direction_col, None))
    else:
        speeds = {height: col for col in frame.columns if (height := _wide_height(col, True)) is not None}
        directions = {height: col for col in frame.columns if (height := _wide_height(col, False)) is not None}
        pairs = [(speeds[height], directions[height], height) for height in sorted(speeds.keys() & directions.keys())]
    if not pairs:
        raise ValueError(f"No wind speed/direction columns found in {path}")

    output: list[Observation] = []
    for row_index, row in frame.iterrows():
        try:
            time_utc = parse_datetime_utc(row[time_col], config.tz_offset_hours)
        except (TypeError, ValueError):
            continue
        lat = None if lat_col is None or pd.isna(row[lat_col]) else as_float(row[lat_col], "latitude")
        lon = None if lon_col is None or pd.isna(row[lon_col]) else as_float(row[lon_col], "longitude")
        if not allow_missing_coordinates and (lat is None or lon is None):
            continue
        temperature = None if temp_col is None or pd.isna(row[temp_col]) else as_float(row[temp_col], "temperature")
        base_station = (
            str(row[station_col]).strip()
            if station_col is not None and not pd.isna(row[station_col])
            else config.station_prefix
        )
        for speed_name, direction_name, pair_height in pairs:
            try:
                speed = as_float(row[speed_name], "wind speed")
                direction = as_float(row[direction_name], "wind direction")
                height = pair_height if pair_height is not None else as_float(row[height_col], "height")
            except (TypeError, ValueError):
                continue
            station = f"{base_station}_{int(round(height)):04d}m"
            output.append(
                Observation(
                    source=source,
                    station=station,
                    time_utc=time_utc,
                    lat=lat,
                    lon=lon,
                    height_m=height,
                    speed_ms=speed,
                    direction_deg=direction,
                    temperature_c=temperature,
                    metadata={"input_file": str(path), "row": str(row_index)},
                )
            )
    return output


class BaseReader(ABC):
    def __init__(self, path: Path, config: SourceConfig):
        self.path = path
        self.config = config

    @abstractmethod
    def read(self) -> list[Observation]:
        raise NotImplementedError


def read_text_frame(text: str) -> pd.DataFrame:
    from io import StringIO

    last_error: Exception | None = None
    for separator in (None, r"[\t,;]+", r"\s+"):
        try:
            return pd.read_csv(StringIO(text), sep=separator, engine="python", comment="#")
        except Exception as error:
            last_error = error
    raise ValueError("Unable to parse delimited observation table") from last_error


def decode_bytes(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030", "latin-1"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("utf-8", payload, 0, 1, "unsupported observation encoding")
