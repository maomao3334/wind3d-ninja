from __future__ import annotations

import re
from datetime import datetime

from ..observations.models import Observation
from ..utils.timezone import parse_datetime_utc
from .base import BaseReader, decode_bytes, frame_to_observations, read_text_frame


GPS_LINE = re.compile(
    r"GPS\s+LAT=\s*(-?\d+(?:\.\d+)?)\s+GPS\s+LONG=\s*(-?\d+(?:\.\d+)?)(?:\s+T=\s*(-?\d+(?:\.\d+)?))?",
    re.I,
)
TIME_LINE = re.compile(r"^(\d{2}:\d{2}:\d{2})\s+(\d{4}-\d{2}-\d{2})\s*$")
DATA_LINE = re.compile(r"^\s*(\d+)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*$")


class DatReader(BaseReader):
    def read(self):
        text = decode_bytes(self.path.read_bytes())
        observations = self._parse_profile(text)
        if not observations:
            frame = read_text_frame(text)
            observations = frame_to_observations(frame, "dat", self.config, self.path, True)
        return sorted(observations, key=lambda item: (item.time_utc, item.height_m, item.station))

    def _parse_profile(self, text: str) -> list[Observation]:
        output: list[Observation] = []
        latitude: float | None = None
        longitude: float | None = None
        temperature: float | None = None
        current_time: datetime | None = None
        for row_index, line in enumerate(text.splitlines(), start=1):
            gps = GPS_LINE.search(line)
            if gps:
                raw_latitude = float(gps.group(1))
                raw_longitude = float(gps.group(2))
                latitude = raw_latitude if -90 <= raw_latitude <= 90 else None
                longitude = raw_longitude if -180 <= raw_longitude <= 180 else None
                temperature = float(gps.group(3)) if gps.group(3) is not None else None
                continue
            time_match = TIME_LINE.match(line)
            if time_match:
                current_time = parse_datetime_utc(
                    f"{time_match.group(2)} {time_match.group(1)}",
                    self.config.tz_offset_hours,
                )
                continue
            data = DATA_LINE.match(line)
            if data is None or current_time is None:
                continue
            height = float(data.group(1))
            if self.config.max_height_m is not None and height > self.config.max_height_m:
                continue
            speed = float(data.group(2)) / self.config.speed_divisor
            direction = float(data.group(3))
            if speed <= 0 or speed > 100:
                continue
            output.append(
                Observation(
                    source="dat",
                    station=f"{self.config.station_prefix}_{int(height):04d}m",
                    time_utc=current_time,
                    lat=latitude,
                    lon=longitude,
                    height_m=height,
                    speed_ms=speed,
                    direction_deg=direction,
                    temperature_c=temperature,
                    metadata={"input_file": str(self.path), "row": str(row_index)},
                )
            )
        return output
