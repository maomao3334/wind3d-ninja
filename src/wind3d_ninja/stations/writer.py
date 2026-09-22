from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path

from ..config.models import TemperatureConfig
from ..observations.models import Observation
from ..timeplan.axis import TimeAxis


HEADER = [
    "Station_Name",
    "Coord_Sys(PROJCS,GEOGCS)",
    "Datum(WGS84,NAD83,NAD27)",
    "Lat/YCoord",
    "Lon/XCoord",
    "Height",
    "Height_Units(meters,feet)",
    "Speed",
    "Speed_Units(mph,kph,mps,kts)",
    "Direction(degrees)",
    "Temperature",
    "Temperature_Units(F,C)",
    "Cloud_Cover(%)",
    "Radius_of_Influence",
    "Radius_of_Influence_Units(miles,feet,meters,km)",
    "date_time",
]


def safe_station_name(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_.-]+", "_", value.strip())
    return cleaned.strip("_") or "station"


class StationFileWriter:
    def __init__(self, temperature: TemperatureConfig, radius_of_influence_km: float = -1.0):
        self.temperature = temperature
        self.radius_of_influence_km = radius_of_influence_km

    def row(
        self,
        observation: Observation,
        target_time: datetime,
        include_datetime: bool = True,
    ) -> list[str]:
        if observation.lat is None or observation.lon is None:
            raise ValueError(f"Station has no coordinates: {observation.station}")
        source_temperature = observation.temperature_c
        low, high = self.temperature.valid_range_c
        use_source = (
            self.temperature.trust_source
            and source_temperature is not None
            and low <= source_temperature <= high
        )
        temperature = source_temperature if use_source else self.temperature.configured_assumption_c
        return [
            safe_station_name(observation.station),
            "GEOGCS",
            "WGS84",
            f"{observation.lat:.6f}",
            f"{observation.lon:.6f}",
            f"{observation.height_m:.2f}",
            "meters",
            f"{observation.speed_ms:.3f}",
            "mps",
            f"{observation.direction_deg:.2f}",
            f"{temperature:.1f}",
            "C",
            f"{self.temperature.cloud_cover_assumption_pct:.1f}",
            f"{self.radius_of_influence_km:g}",
            "km",
            TimeAxis.format_time_for_station(target_time) if include_datetime else "",
        ]

    def write(
        self,
        observation: Observation,
        output_path: Path,
        target_time: datetime,
        include_datetime: bool = True,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(HEADER)
            writer.writerow(self.row(observation, target_time, include_datetime))
        return output_path
