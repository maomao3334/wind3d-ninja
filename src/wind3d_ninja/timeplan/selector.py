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
            if rule and rule.preferred_heights_m:
                preferred = [
                    item
                    for item in source_items
                    if any(abs(item.height_m - height) < 0.01 for height in rule.preferred_heights_m)
                ]
                if preferred:
                    source_items = preferred
            selected.extend(source_items)
        return sorted(selected, key=lambda item: (item.source, item.station, item.height_m))
