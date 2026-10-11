from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd


def parse_datetime_utc(value: Any, tz_offset_hours: float) -> datetime:
    parsed = pd.to_datetime(value, errors="raise")
    dt = parsed.to_pydatetime() if hasattr(parsed, "to_pydatetime") else parsed
    if not isinstance(dt, datetime):
        raise ValueError(f"Unsupported timestamp: {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone(timedelta(hours=tz_offset_hours)))
    return dt.astimezone(timezone.utc)


def parse_time_range_utc(
    values: tuple[str, str] | None,
    tz_offset_hours: float,
) -> tuple[datetime, datetime] | None:
    if values is None:
        return None
    start = parse_datetime_utc(values[0], tz_offset_hours)
    end = parse_datetime_utc(values[1], tz_offset_hours)
    if start > end:
        raise ValueError("Time range start must not be later than end")
    return start, end


def parse_explicit_time_utc(value: str) -> datetime:
    try:
        normalized = value.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise ValueError(
            "Simulation time must be an ISO-8601 timestamp with a timezone, for example "
            "2024-04-03T08:16:00Z"
        ) from error
    if parsed.tzinfo is None:
        raise ValueError(
            "Simulation time must include a timezone, for example 2024-04-03T08:16:00Z"
        )
    return parsed.astimezone(timezone.utc)
