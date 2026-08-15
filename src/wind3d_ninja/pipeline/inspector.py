from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config.models import AppConfig
from ..discovery.scanner import DataScanner, ScanResult
from ..observations.coordinator import complete_coordinates
from ..observations.models import Observation
from ..readers import DatReader, RawUavRecord, UavReader, WindMasterReader
from ..timeplan.axis import TimeAxis
from ..utils.validation import observation_summary


@dataclass(frozen=True)
class LoadedInputs:
    scan: ScanResult
    fixed_observations: tuple[Observation, ...]
    uav_inputs: tuple[tuple[UavReader, tuple[RawUavRecord, ...]], ...]


class InputInspector:
    def __init__(self, config: AppConfig):
        self.config = config

    def load(self, input_dir: Path) -> LoadedInputs:
        scan = DataScanner(input_dir).scan()
        fixed: list[Observation] = []
        for path in scan.windmaster:
            fixed.extend(WindMasterReader(path, self.config.sources["windmaster"]).read())
        for path in scan.dat:
            fixed.extend(DatReader(path, self.config.sources["dat"]).read())
        fixed = complete_coordinates(fixed) if fixed else []
        uav_inputs: list[tuple[UavReader, tuple[RawUavRecord, ...]]] = []
        for path in scan.uav:
            reader = UavReader(path, self.config.sources["uav"])
            uav_inputs.append((reader, tuple(reader.read_metadata())))
        return LoadedInputs(scan, tuple(fixed), tuple(uav_inputs))

    def inspect(self, input_dir: Path) -> dict[str, object]:
        loaded = self.load(input_dir)
        raw_uav = [record for _, records in loaded.uav_inputs for record in records]
        fixed_summary = observation_summary(loaded.fixed_observations)
        times = sorted(
            {TimeAxis.normalize(item.time_utc) for item in loaded.fixed_observations}
            | {TimeAxis.normalize(item.time_utc) for item in raw_uav}
        )
        return {
            "input_dir": str(input_dir.resolve()),
            "files": loaded.scan.as_dict(),
            "fixed_observations": fixed_summary,
            "uav_metadata_count": len(raw_uav),
            "time_count": len(times),
            "time_start_utc": times[0] if times else None,
            "time_end_utc": times[-1] if times else None,
        }
