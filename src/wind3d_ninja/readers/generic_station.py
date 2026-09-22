from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd

from ..config.models import SourceConfig
from ..observations.models import Observation
from ..utils.timezone import parse_datetime_utc
from .base import as_float, frame_to_observations, read_text_frame


def _unit(value: Any) -> str:
    return re.sub(r"[^a-z0-9/]", "", str(value).lower())


def _convert_speed(value: float, unit: str) -> float:
    unit = _unit(unit)
    if unit in {"km/h", "kmh", "kph"}:
        return value / 3.6
    if unit in {"mph"}:
        return value * 0.44704
    if unit in {"kt", "kts", "knot", "knots"}:
        return value * 0.514444
    return value


def _convert_height(value: float, unit: str) -> float:
    unit = _unit(unit)
    if unit in {"cm"}:
        return value / 100.0
    if unit in {"ft", "feet"}:
        return value * 0.3048
    return value


class GenericStationReader:
    """Read common CSV/TXT station tables without a vendor-specific reader."""

    def __init__(self, path: Path, config: SourceConfig):
        self.path = path
        self.config = config
        self.stats: dict[str, int] = {
            "rows": 0, "valid_rows": 0, "invalid_rows": 0,
            "invalid_time": 0, "invalid_coordinates": 0,
            "invalid_wind": 0,
        }

    def _frame(self) -> pd.DataFrame:
        payload = self.path.read_bytes()
        for encoding in ("utf-8-sig", "gb18030", "latin-1"):
            try:
                text = payload.decode(encoding)
                return read_text_frame(text)
            except (UnicodeDecodeError, ValueError):
                continue
        raise ValueError(f"Unable to decode generic station file: {self.path}")

    def read(self) -> list[Observation]:
        frame = self._frame().dropna(how="all")
        self.stats["rows"] = len(frame)
        if frame.empty:
            return []
        # WindMaster archives commonly contain auxiliary CSV/TXT exports
        # (status, backscatter, diagnostics) beside the actual station table.
        # A station adapter must identify those cheaply before invoking the
        # strict observation converters, otherwise a missing timestamp turns
        # a harmless auxiliary file into a hard pipeline failure.
        from .base import find_column

        if find_column(frame, "time", required=False) is None:
            self.stats["invalid_rows"] = self.stats["rows"]
            return []
        # Reuse the proven long/wide column detector for the common m/s + metre case.
        try:
            observations = self._read_with_units(frame)
        except ValueError:
            try:
                observations = frame_to_observations(
                    frame, "generic_station", self.config, self.path, False
                )
            except ValueError as error:
                # Auxiliary CSV/TXT files often sit beside a WindMaster archive.
                # They are not station tables and must not break inspect/plan.
                message = str(error)
                if message.startswith("Missing time column") or message.startswith(
                    "No wind speed/direction columns found"
                ):
                    self.stats["invalid_rows"] = self.stats["rows"]
                    return []
                raise
        filtered: list[Observation] = []
        for item in observations:
            if not (0 <= item.speed_ms <= 100) or not (0 <= item.direction_deg <= 360):
                self.stats["invalid_wind"] += 1
                continue
            self.stats["valid_rows"] += 1
            filtered.append(item)
        self.stats["invalid_rows"] = self.stats["rows"] - self.stats["valid_rows"]
        return filtered

    def _read_with_units(self, frame: pd.DataFrame) -> list[Observation]:
        from .base import find_column

        time_col = find_column(frame, "time")
        lat_col = find_column(frame, "lat")
        lon_col = find_column(frame, "lon")
        speed_col = find_column(frame, "speed")
        direction_col = find_column(frame, "direction")
        height_col = find_column(frame, "height")
        station_col = find_column(frame, "station", required=False)
        speed_unit_col = next((c for c in frame.columns if "speed" in str(c).lower() and "unit" in str(c).lower()), None)
        height_unit_col = next((c for c in frame.columns if "height" in str(c).lower() and "unit" in str(c).lower()), None)
        speed_name = str(speed_col).lower()
        height_name = str(height_col).lower()
        speed_default = "km/h" if any(token in speed_name for token in ("kmh", "km/h", "kph")) else "mph" if "mph" in speed_name else "kt" if any(token in speed_name for token in ("knot", "kts")) else "mps"
        height_default = "ft" if any(token in height_name for token in ("feet", "ft")) else "cm" if "cm" in height_name else "m"
        output: list[Observation] = []
        for index, row in frame.iterrows():
            try:
                time_utc = parse_datetime_utc(row[time_col], self.config.tz_offset_hours)
                lat, lon = as_float(row[lat_col], "latitude"), as_float(row[lon_col], "longitude")
                speed = _convert_speed(as_float(row[speed_col], "wind speed"), row[speed_unit_col] if speed_unit_col else speed_default)
                direction = as_float(row[direction_col], "wind direction")
                height = _convert_height(as_float(row[height_col], "height"), row[height_unit_col] if height_unit_col else height_default)
            except (TypeError, ValueError):
                self.stats["invalid_rows"] += 1
                continue
            station = str(row[station_col]).strip() if station_col is not None and not pd.isna(row[station_col]) else self.config.station_prefix
            output.append(Observation(
                source="generic_station", station=f"{station}_{int(round(height)):04d}m",
                time_utc=time_utc, lat=lat, lon=lon, height_m=height,
                speed_ms=speed, direction_deg=direction,
                metadata={"input_file": str(self.path), "row": str(index), "adapter": "generic_station"},
            ))
        return output
