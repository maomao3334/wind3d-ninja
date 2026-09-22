from __future__ import annotations

from datetime import datetime
from typing import Iterable

from ..config.models import QualityRule
from ..observations.models import Observation
from .axis import TimeAxis


class ObservationSelector:
    def __init__(self, quality_rules: dict[str, QualityRule] | None = None):
        self.quality_rules = quality_rules or {}

    def select_for_time(
        self,
        target_time: datetime,
        observations: Iterable[Observation],
    ) -> list[Observation]:
        target = TimeAxis.normalize(target_time)
        items = list(observations)
        selected: list[Observation] = []
        for source in sorted({item.source for item in items}):
            rule = self.quality_rules.get(source)
            max_offset_seconds = max(0, rule.max_time_offset_seconds) if rule else 0
            source_items = [
                item
                for item in items
                if item.source == source
                and (
                    abs(
                        (TimeAxis.to_utc(item.time_utc) - target).total_seconds()
                    )
                    <= max_offset_seconds
                    if max_offset_seconds > 0
                    else TimeAxis.normalize(item.time_utc) == target
                )
            ]
            if not source_items:
                continue
            # WindNinja's Recent_Station_File_List requires one file per
            # unique Station_Name.  Keep every available observation height,
            # but when a station has several records inside the time window,
            # keep the nearest record for that station.
            nearest_by_station: dict[str, tuple[float, datetime, float, Observation]] = {}
            for item in source_items:
                item_time = TimeAxis.to_utc(item.time_utc)
                distance_seconds = abs((item_time - target).total_seconds())
                candidate = (distance_seconds, item_time, item.height_m, item)
                previous = nearest_by_station.get(item.station)
                if previous is None or candidate[:3] < previous[:3]:
                    nearest_by_station[item.station] = candidate
            selected.extend(candidate[3] for candidate in nearest_by_station.values())
        return sorted(selected, key=lambda item: (item.source, item.station, item.height_m))
