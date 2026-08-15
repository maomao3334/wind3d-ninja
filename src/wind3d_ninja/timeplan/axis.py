from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from ..observations.models import Observation


class TimeAxis:
    @staticmethod
    def to_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @classmethod
    def normalize(cls, value: datetime) -> datetime:
        return cls.to_utc(value).replace(second=0, microsecond=0)

    @classmethod
    def extract_unique_times(cls, observations: Iterable[Observation]) -> list[datetime]:
        return sorted({cls.normalize(item.time_utc) for item in observations})

    @classmethod
    def format_time_for_filename(cls, value: datetime) -> str:
        return cls.normalize(value).strftime("%Y%m%dT%H%M%SZ")

    @classmethod
    def format_time_for_station(cls, value: datetime) -> str:
        return cls.normalize(value).strftime("%Y-%m-%dT%H:%M:00Z")
