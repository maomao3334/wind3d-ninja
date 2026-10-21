from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from ..config.models import AppConfig
from ..discovery.scanner import DataScanner, ScanResult
from ..observations.coordinator import complete_coordinates
from ..observations.instruments import InstrumentPolicy, InstrumentTimePolicy, load_instrument_catalog, scan_instrument_time_candidates
from ..observations.models import Observation
from ..readers import DatReader, GenericStationReader, RawUavRecord, UavReader, WindMasterReader
from ..timeplan.axis import TimeAxis
from ..utils.validation import observation_summary


@dataclass(frozen=True)
class LoadedInputs:
    scan: ScanResult
    fixed_observations: tuple[Observation, ...]
    uav_inputs: tuple[tuple[UavReader, tuple[RawUavRecord, ...]], ...]
    generic_observations: tuple[Observation, ...] = ()


class InputInspector:
    def __init__(self, config: AppConfig, instrument_policy: InstrumentPolicy | None = None):
        self.config = config
        self.instrument_policy = instrument_policy

    def _effective_policy(self) -> InstrumentPolicy:
        if self.instrument_policy is not None:
            return self.instrument_policy
        return InstrumentPolicy(time_policy=InstrumentTimePolicy(
            uav_time_field="pc_collection_time",
            uav_timezone_offset_hours=self.config.sources["uav"].tz_offset_hours,
            windmaster_timezone_offset_hours=self.config.sources["windmaster"].tz_offset_hours,
            dat_timezone_offset_hours=self.config.sources["dat"].tz_offset_hours,
        ))

    def effective_instrument_policy(self) -> InstrumentPolicy:
        return self._effective_policy()

    def load(self, input_dir: Path) -> LoadedInputs:
        policy = self._effective_policy()
        scan = DataScanner(input_dir).scan()
        catalog = load_instrument_catalog(input_dir, policy=policy) if (scan.windmaster or scan.uav) else None
        instrument_fixed = [
            Observation(
                source=record.source,
                station=record.record_id,
                time_utc=record.time_utc,
                lat=record.lat,
                lon=record.lon,
                height_m=abs(record.gate_position_m or record.relative_height_m or 0.0),
                speed_ms=record.wind_speed,
                direction_deg=record.wind_direction_deg,
                moving=record.moving,
                metadata=dict(record.metadata),
            )
            for record in catalog.records
            if record.source == "windmaster"
        ] if catalog is not None and catalog.records else []
        fixed = [item for item in instrument_fixed if item.height_m > 0]
        if catalog is None:
            for path in scan.windmaster:
                fixed.extend(WindMasterReader(path, replace(self.config.sources["windmaster"], tz_offset_hours=policy.time_policy.windmaster_timezone_offset_hours)).read())
        for path in scan.dat:
            fixed.extend(DatReader(path, replace(self.config.sources["dat"], tz_offset_hours=policy.time_policy.dat_timezone_offset_hours)).read())
        fixed = complete_coordinates(fixed) if fixed else []
        generic: list[Observation] = []
        generic_config = replace(self.config.sources.get("generic_station", self.config.sources["dat"]), tz_offset_hours=policy.time_policy.dat_timezone_offset_hours)
        for path in scan.generic_station:
            generic.extend(GenericStationReader(path, generic_config).read())
        fixed.extend(complete_coordinates(generic) if generic else [])
        uav_inputs: list[tuple[UavReader, tuple[RawUavRecord, ...]]] = []
        for path in scan.uav:
            reader = UavReader(path, self.config.sources["uav"], policy.time_policy)
            uav_inputs.append((reader, tuple(reader.read_metadata())))
        return LoadedInputs(scan, tuple(fixed), tuple(uav_inputs), tuple(generic))

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
            "generic_station_observations": observation_summary(loaded.generic_observations),
            "uav_metadata_count": len(raw_uav),
            "time_count": len(times),
            "time_start_utc": times[0] if times else None,
            "time_end_utc": times[-1] if times else None,
            "instrument_time_candidates": scan_instrument_time_candidates(input_dir, self._effective_policy().time_policy),
        }
