from __future__ import annotations

import re
from pathlib import Path
from zipfile import ZipFile

from ..observations.models import Observation
from ..utils.timezone import parse_datetime_utc
from .base import BaseReader, decode_bytes, frame_to_observations, read_text_frame


HEIGHT_COLUMN = re.compile(r"^(\d+)m\s+(.+)$")
COORDINATE = re.compile(r"(?:Latitude|longtitude|Longitude)\s*:\s*(-?\d+(?:\.\d+)?)", re.I)


class WindMasterReader(BaseReader):
    def read(self):
        observations = []
        with ZipFile(self.path) as archive:
            members = [
                name
                for name in archive.namelist()
                if name.endswith(".csv")
                and "/level2/" in name.replace("\\", "/")
                and "_Ave01min_" in Path(name).name
                and not any(value in Path(name).name for value in ("Sins", "Drone", "BackScatter", "HWSWindShear"))
            ]
            if not members:
                members = [
                    name
                    for name in archive.namelist()
                    if not name.endswith("/") and Path(name).suffix.lower() in {".csv", ".txt", ".dat"}
                ]
            if not members:
                raise ValueError(f"WindMaster archive has no readable table: {self.path}")
            for member in members:
                text = decode_bytes(archive.read(member))
                parsed = self._parse_profile(text)
                if parsed:
                    observations.extend(parsed)
                else:
                    frame = read_text_frame(text)
                    observations.extend(
                        frame_to_observations(frame, "windmaster", self.config, self.path, False)
                    )
        return sorted(observations, key=lambda item: (item.time_utc, item.height_m, item.station))

    def _parse_profile(self, text: str) -> list[Observation]:
        lines = text.splitlines()
        if len(lines) < 3:
            return []
        coordinates = COORDINATE.findall(lines[0])
        if len(coordinates) < 2:
            return []
        latitude, longitude = float(coordinates[0]), float(coordinates[1])
        columns = [value.strip() for value in lines[1].split(",")]
        by_height: dict[int, dict[str, int]] = {}
        for index, column in enumerate(columns):
            match = HEIGHT_COLUMN.match(column)
            if match:
                by_height.setdefault(int(match.group(1)), {})[match.group(2).strip()] = index
        if not by_height:
            return []
        maximum = self.config.max_height_m
        heights = [value for value in sorted(by_height) if maximum is None or value <= maximum]
        output: list[Observation] = []
        for row_index, line in enumerate(lines[2:], start=3):
            fields = line.split(",")
            if not fields or not fields[0].strip():
                continue
            try:
                time_utc = parse_datetime_utc(fields[0].strip(), self.config.tz_offset_hours)
            except (IndexError, TypeError, ValueError):
                continue
            for height in heights:
                indices = by_height[height]
                try:
                    speed = float(fields[indices["WindSpeed"]])
                    direction = float(fields[indices["WindDirection"]])
                except (IndexError, KeyError, ValueError):
                    continue
                obtain_rate = None
                if "DataObtainRate" in indices:
                    try:
                        obtain_rate = float(fields[indices["DataObtainRate"]])
                    except (IndexError, ValueError):
                        obtain_rate = None
                if obtain_rate is not None and obtain_rate < self.config.min_obtain_rate:
                    continue
                if speed < 0 or speed > 100:
                    continue
                output.append(
                    Observation(
                        source="windmaster",
                        station=f"{self.config.station_prefix}_{height:04d}m",
                        time_utc=time_utc,
                        lat=latitude,
                        lon=longitude,
                        height_m=float(height),
                        speed_ms=speed,
                        direction_deg=direction,
                        metadata={
                            "input_file": str(self.path),
                            "row": str(row_index),
                            "data_obtain_rate": obtain_rate,
                        },
                    )
                )
        return output
