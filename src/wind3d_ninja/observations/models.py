from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class Observation:
    source: str
    station: str
    time_utc: datetime
    lat: float | None
    lon: float | None
    height_m: float
    speed_ms: float
    direction_deg: float
    temperature_c: float | None = None
    moving: bool = False
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)

    def __post_init__(self) -> None:
        value = self.time_utc
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc)
        object.__setattr__(self, "time_utc", value)
        object.__setattr__(self, "direction_deg", float(self.direction_deg) % 360.0)
        if self.speed_ms < 0:
            raise ValueError("Wind speed cannot be negative")
        if self.height_m < 0:
            raise ValueError("Observation height cannot be negative")
        if self.lat is not None and not -90 <= self.lat <= 90:
            raise ValueError(f"Invalid latitude: {self.lat}")
        if self.lon is not None and not -180 <= self.lon <= 180:
            raise ValueError(f"Invalid longitude: {self.lon}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "station": self.station,
            "time_utc": self.time_utc.isoformat().replace("+00:00", "Z"),
            "lat": self.lat,
            "lon": self.lon,
            "height_m": self.height_m,
            "speed_ms": self.speed_ms,
            "direction_deg": self.direction_deg,
            "temperature_c": self.temperature_c,
            "moving": self.moving,
            "metadata": self.metadata,
        }
