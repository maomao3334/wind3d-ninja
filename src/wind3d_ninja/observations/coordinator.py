from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from ..timeplan.axis import TimeAxis
from .models import Observation


def complete_coordinates(observations: Iterable[Observation]) -> list[Observation]:
    items = list(observations)
    fixed_by_time = {
        TimeAxis.normalize(obs.time_utc): (obs.lat, obs.lon)
        for obs in items
        if obs.source == "windmaster" and obs.lat is not None and obs.lon is not None
    }
    completed: list[Observation] = []
    for obs in items:
        if obs.lat is None or obs.lon is None:
            fixed = fixed_by_time.get(TimeAxis.normalize(obs.time_utc))
            if obs.source == "dat" and fixed is None:
                continue
            if fixed is None:
                raise ValueError(f"Missing coordinates for {obs.source}:{obs.station}")
            completed.append(replace(obs, lat=fixed[0], lon=fixed[1]))
        else:
            completed.append(obs)
    return completed
