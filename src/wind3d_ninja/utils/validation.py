from __future__ import annotations

from collections import Counter
from typing import Iterable

from ..observations.models import Observation


def validate_observations(observations: Iterable[Observation]) -> list[Observation]:
    values = list(observations)
    if not values:
        raise ValueError("No usable observations were read")
    for obs in values:
        if obs.lat is None or obs.lon is None:
            raise ValueError(f"Observation lacks coordinates: {obs.station}")
    return values


def observation_summary(observations: Iterable[Observation]) -> dict[str, object]:
    values = list(observations)
    by_source = Counter(obs.source for obs in values)
    return {
        "count": len(values),
        "by_source": dict(sorted(by_source.items())),
        "time_start_utc": min((obs.time_utc for obs in values), default=None),
        "time_end_utc": max((obs.time_utc for obs in values), default=None),
    }
