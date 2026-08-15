from __future__ import annotations

from dataclasses import dataclass
from math import cos, radians
from typing import Iterable


@dataclass(frozen=True)
class GeoBounds:
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float

    def __post_init__(self) -> None:
        if self.min_lat >= self.max_lat or self.min_lon >= self.max_lon:
            raise ValueError("Bounds must have positive latitude and longitude spans")
        if not (-90 <= self.min_lat < self.max_lat <= 90):
            raise ValueError("Latitude bounds are invalid")
        if not (-180 <= self.min_lon < self.max_lon <= 180):
            raise ValueError("Longitude bounds are invalid")

    @property
    def center(self) -> tuple[float, float]:
        return ((self.min_lat + self.max_lat) / 2, (self.min_lon + self.max_lon) / 2)

    def as_dict(self) -> dict[str, float]:
        return {
            "min_lat": self.min_lat,
            "max_lat": self.max_lat,
            "min_lon": self.min_lon,
            "max_lon": self.max_lon,
        }


def bounds_from_points(points: Iterable[tuple[float, float]], buffer_km: float) -> GeoBounds:
    values = list(points)
    if not values:
        raise ValueError("Cannot calculate bounds without coordinates")
    latitudes = [value[0] for value in values]
    longitudes = [value[1] for value in values]
    center_lat = sum(latitudes) / len(latitudes)
    lat_buffer = buffer_km / 111.0
    lon_scale = max(0.05, cos(radians(center_lat)))
    lon_buffer = buffer_km / (111.0 * lon_scale)
    return GeoBounds(
        min(latitudes) - lat_buffer,
        max(latitudes) + lat_buffer,
        min(longitudes) - lon_buffer,
        max(longitudes) + lon_buffer,
    )


def parse_bounds(value: str | Iterable[float] | None) -> GeoBounds | None:
    if value is None:
        return None
    parts = (
        [float(item.strip()) for item in value.split(",")]
        if isinstance(value, str)
        else [float(item) for item in value]
    )
    if len(parts) != 4:
        raise ValueError("Bounds must be MIN_LAT MAX_LAT MIN_LON MAX_LON")
    return GeoBounds(*parts)
