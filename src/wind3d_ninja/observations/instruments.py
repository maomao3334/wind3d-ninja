from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from math import isfinite
from pathlib import Path
import re
from io import StringIO
from typing import Any
from zipfile import BadZipFile, ZipFile

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer

from ..discovery.scanner import DataScanner
from ..observations.models import Observation


UTC = timezone.utc
BEIJING = timezone(timedelta(hours=8))
MISSING_CODE = 999.0
_GATE_COLUMN = re.compile(r"^(-?\d+(?:\.\d+)?)m\s+(.+)$")
_HEADER_COORDINATE = re.compile(
    r"(?:Latitude|Longitude|longtitude)\s*:\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE
)


class WindDirectionReference(str, Enum):
    UNKNOWN = "unknown"
    FROM_TRUE_NORTH = "from_true_north"
    TO_TRUE_NORTH = "to_true_north"


class WindSpeedUnit(str, Enum):
    UNKNOWN = "unknown"
    METERS_PER_SECOND = "m/s"


class RadarHeightReference(str, Enum):
    UNKNOWN = "unknown"
    AGL = "agl"
    PLATFORM_VERTICAL_OFFSET = "platform_vertical_offset"


class UavHeightReference(str, Enum):
    UNKNOWN = "unknown"
    GPS_ALTITUDE_MINUS_DEM = "gps_altitude_minus_dem"
    RELATIVE_TO_LAUNCH = "relative_to_launch"
    GPS_PLUS_RELATIVE_MINUS_DEM = "gps_plus_relative_minus_dem"
    RELATIVE_AGL = "relative_agl"


@dataclass(frozen=True)
class InstrumentTimePolicy:
    uav_time_field: str = "device_sampling_time"
    uav_timezone_offset_hours: float = 8.0
    windmaster_time_field: str = "sampling_time"
    windmaster_timezone_offset_hours: float = 8.0
    dat_timezone_offset_hours: float = 8.0

    def __post_init__(self) -> None:
        for value in (self.uav_timezone_offset_hours, self.windmaster_timezone_offset_hours, self.dat_timezone_offset_hours):
            if not isfinite(value) or not -24 <= value <= 24:
                raise ValueError("instrument time-zone offsets must be finite hours in [-24, 24]")


@dataclass(frozen=True)
class InstrumentPolicy:
    windmaster_direction_reference: WindDirectionReference = WindDirectionReference.UNKNOWN
    uav_direction_reference: WindDirectionReference = WindDirectionReference.UNKNOWN
    windmaster_speed_unit: WindSpeedUnit = WindSpeedUnit.UNKNOWN
    uav_speed_unit: WindSpeedUnit = WindSpeedUnit.UNKNOWN
    windmaster_height_reference: RadarHeightReference = RadarHeightReference.UNKNOWN
    windmaster_platform_agl_m: float | None = None
    windmaster_is_geographic_wind: bool = False
    windmaster_motion_compensated: bool = False
    uav_is_geographic_wind: bool = False
    uav_motion_compensated: bool = False
    uav_height_reference: UavHeightReference = UavHeightReference.UNKNOWN
    uav_relative_agl_confirmed: bool = False
    uav_legacy_gps_is_launch_altitude_confirmed: bool = False
    uav_launch_ground_altitude_m: float | None = None
    uav_launch_lat: float | None = None
    uav_launch_lon: float | None = None
    uav_launch_location_confirmed: bool = False
    canopy_height_m: float = 0.0
    canopy_height_assumption: bool = True
    valid_year_range: tuple[int, int] = (2020, 2100)
    time_policy: InstrumentTimePolicy = field(default_factory=InstrumentTimePolicy)

    def __post_init__(self) -> None:
        if not isfinite(self.canopy_height_m) or self.canopy_height_m < 0:
            raise ValueError("canopy_height_m must be finite and non-negative")
        if self.windmaster_platform_agl_m is not None and (
            not isfinite(self.windmaster_platform_agl_m) or self.windmaster_platform_agl_m < 0
        ):
            raise ValueError("windmaster_platform_agl_m must be finite and non-negative")
        if self.uav_launch_ground_altitude_m is not None and not isfinite(self.uav_launch_ground_altitude_m):
            raise ValueError("uav_launch_ground_altitude_m must be finite")
        if self.uav_launch_location_confirmed and not _valid_coordinates(self.uav_launch_lat, self.uav_launch_lon):
            raise ValueError("Confirmed UAV launch location requires valid latitude and longitude")
        start, end = self.valid_year_range
        if start > end:
            raise ValueError("valid_year_range start must not exceed end")


@dataclass(frozen=True)
class RawInstrumentRecord:
    record_id: str
    source: str
    product: str
    time_utc: datetime
    lat: float
    lon: float
    wind_speed: float
    wind_direction_deg: float
    speed_unit: WindSpeedUnit
    gate_position_m: float | None = None
    platform_altitude_m: float | None = None
    relative_height_m: float | None = None
    moving: bool = False
    quality: Mapping[str, float | None] = field(default_factory=dict)
    raw_statistics: Mapping[str, float | None] = field(default_factory=dict)
    metadata: Mapping[str, str | float | bool | None] = field(default_factory=dict)


@dataclass(frozen=True)
class InstrumentCatalog:
    records: tuple[RawInstrumentRecord, ...]
    products: Mapping[str, int]
    selected_product: str | None
    rejected: Mapping[str, int]
    provenance: Mapping[str, str | int | bool | None]


@dataclass(frozen=True)
class NormalizationResult:
    observations: tuple[Observation, ...]
    rejected: Mapping[str, int]
    provenance: Mapping[str, str | float | bool | None]
    blocking_reasons: tuple[str, ...]


def _add_rejection(rejections: Counter[str], reason: str) -> None:
    rejections[reason] += 1


def _as_number(value: str) -> float | None:
    try:
        number = float(value.strip())
    except (AttributeError, ValueError):
        return None
    return number if isfinite(number) else None


def _is_missing_code(value: float | None) -> bool:
    return value is not None and np.isclose(value, MISSING_CODE)


def _valid_coordinates(lat: float | None, lon: float | None) -> bool:
    return lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180


def parse_instrument_time_utc(value: Any, timezone_offset_hours: float) -> datetime | None:
    text = str(value).strip()
    if not text:
        return None
    try:
        explicit = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if explicit.tzinfo is not None:
            return explicit.astimezone(UTC)
    except ValueError:
        pass
    for fmt in ("%Y%m%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(
                tzinfo=timezone(timedelta(hours=timezone_offset_hours))
            ).astimezone(UTC)
        except ValueError:
            continue
    return None


def _time_metadata(raw: str, field: str, timezone_offset_hours: float, time_utc: datetime) -> dict[str, str | float]:
    return {
        "time_raw": raw,
        "time_source_field": field,
        "time_source_timezone_offset_hours": timezone_offset_hours,
        "time_utc": time_utc.isoformat().replace("+00:00", "Z"),
    }


def _product_for_member(name: str) -> str | None:
    normalized = name.replace("\\", "/").lower()
    file_name = Path(normalized).name
    if not file_name.endswith(".csv"):
        return None
    if "/level2/" in normalized and "ave01min" in file_name:
        if "droneins" in file_name or "dorneins" in file_name:
            return "droneins_level2_ave01min"
        if "sins" in file_name:
            if "vector" in file_name:
                return "sins_vector_level2_ave01min"
            return "sins_level2_ave01min"
        if "vector" in file_name:
            return "vector_level2_ave01min"
        if "backscatter" not in file_name and "windshear" not in file_name:
            return "windmaster_level2_ave01min"
    if "/level1/" in normalized and "wind" in file_name:
        if "droneins" in file_name or "dorneins" in file_name:
            return "dorneins_level1_sec"
        if "sins" in file_name:
            return "sins_level1_sec"
        if "winddbs" in file_name:
            return "winddbs_level1_sec"
    return None


def _default_product(products: Mapping[str, int]) -> str | None:
    preferred = (
        "sins_level2_ave01min",
        "windmaster_level2_ave01min",
        "droneins_level2_ave01min",
        "dorneins_level1_sec",
        "sins_level1_sec",
        "winddbs_level1_sec",
        "vector_level2_ave01min",
        "sins_vector_level2_ave01min",
    )
    return next((product for product in preferred if product in products), None)


def _header_coordinates(header: str) -> tuple[float | None, float | None]:
    values = [_as_number(value) for value in _HEADER_COORDINATE.findall(header)]
    if len(values) < 2:
        return None, None
    return values[0], values[1]


def _field_index(columns: list[str], wanted: str) -> int | None:
    wanted_normalized = wanted.replace(" ", "").lower()
    for index, column in enumerate(columns):
        if column.replace(" ", "").lower() == wanted_normalized:
            return index
    return None


def _parse_windmaster_member(
    *,
    archive_name: str,
    member: str,
    product: str,
    payload: bytes,
    rejections: Counter[str],
    time_policy: InstrumentTimePolicy,
) -> list[RawInstrumentRecord]:
    text = payload.decode("gb18030", errors="replace")
    lines = text.splitlines()
    if len(lines) < 3:
        _add_rejection(rejections, "windmaster_short_table")
        return []
    fallback_lat, fallback_lon = _header_coordinates(lines[0])
    columns = [column.strip() for column in lines[1].split(",")]
    time_index = 0 if time_policy.windmaster_time_field == "sampling_time" else _field_index(columns, time_policy.windmaster_time_field)
    if time_index is None:
        _add_rejection(rejections, "windmaster_selected_time_field_unavailable")
        return []
    lat_index = _field_index(columns, "Latitude")
    lon_index = _field_index(columns, "Longitude")
    altitude_index = _field_index(columns, "Altitude")
    gates: dict[float, dict[str, int]] = defaultdict(dict)
    for index, column in enumerate(columns):
        match = _GATE_COLUMN.match(column)
        if match:
            gates[float(match.group(1))][match.group(2).strip()] = index
    if not gates:
        _add_rejection(rejections, "windmaster_no_gate_columns")
        return []

    records: list[RawInstrumentRecord] = []
    for row_number, line in enumerate(lines[2:], start=3):
        if not line.strip():
            continue
        values = [value.strip() for value in line.split(",")]
        if not values:
            continue
        raw_time = values[time_index] if time_index < len(values) else ""
        time_utc = parse_instrument_time_utc(raw_time, time_policy.windmaster_timezone_offset_hours)
        if time_utc is None:
            _add_rejection(rejections, "windmaster_invalid_time")
            continue
        lat = _as_number(values[lat_index]) if lat_index is not None and lat_index < len(values) else fallback_lat
        lon = _as_number(values[lon_index]) if lon_index is not None and lon_index < len(values) else fallback_lon
        if not _valid_coordinates(lat, lon):
            _add_rejection(rejections, "windmaster_invalid_coordinates")
            continue
        platform_altitude = (
            _as_number(values[altitude_index])
            if altitude_index is not None and altitude_index < len(values)
            else None
        )
        for gate_position, indices in sorted(gates.items()):
            speed_index = indices.get("WindSpeed")
            direction_index = indices.get("WindDirection")
            if speed_index is None or direction_index is None or max(speed_index, direction_index) >= len(values):
                _add_rejection(rejections, "windmaster_missing_wind_fields")
                continue
            speed = _as_number(values[speed_index])
            direction = _as_number(values[direction_index])
            if _is_missing_code(speed) or _is_missing_code(direction):
                _add_rejection(rejections, "windmaster_suspected_missing_999")
                continue
            if speed is None or not 0 <= speed <= 70:
                _add_rejection(rejections, "windmaster_wind_speed_out_of_range")
                continue
            if direction is None or not 0 <= direction <= 360:
                _add_rejection(rejections, "windmaster_wind_direction_out_of_range")
                continue
            quality: dict[str, float | None] = {}
            statistics: dict[str, float | None] = {}
            bad_999 = False
            for field_name in ("MeanSNR(dB)", "SNR(dB)", "DataObtainRate"):
                index = indices.get(field_name)
                value = _as_number(values[index]) if index is not None and index < len(values) else None
                if _is_missing_code(value):
                    bad_999 = True
                quality[field_name] = value
            for field_name in ("StdDev", "ZWind", "ZWind StdDev"):
                index = indices.get(field_name)
                value = _as_number(values[index]) if index is not None and index < len(values) else None
                if _is_missing_code(value):
                    bad_999 = True
                statistics[field_name] = value
            obtain_rate = quality.get("DataObtainRate")
            if bad_999:
                _add_rejection(rejections, "windmaster_suspected_missing_999")
                continue
            if obtain_rate is not None and not 0 <= obtain_rate <= 1:
                _add_rejection(rejections, "windmaster_obtain_rate_out_of_range")
                continue
            record_id = f"windmaster:{product}:{lat:.6f}:{lon:.6f}:{gate_position:g}m"
            records.append(
                RawInstrumentRecord(
                    record_id=record_id,
                    source="windmaster",
                    product=product,
                    time_utc=time_utc,
                    lat=lat,
                    lon=lon,
                    wind_speed=speed,
                    wind_direction_deg=direction,
                    speed_unit=WindSpeedUnit.UNKNOWN,
                    gate_position_m=gate_position,
                    platform_altitude_m=platform_altitude,
                    moving=product.startswith(("droneins", "dorneins")),
                    quality=quality,
                    raw_statistics=statistics,
                    metadata={
                        "input_name": Path(archive_name).name,
                        "member": member,
                        "row": float(row_number),
                        **_time_metadata(raw_time, time_policy.windmaster_time_field, time_policy.windmaster_timezone_offset_hours, time_utc),
                    },
                )
            )
    return records


def _load_windmaster(
    archive_path: Path,
    selected_product: str | None,
    products: Counter[str],
    rejections: Counter[str],
    time_policy: InstrumentTimePolicy,
) -> tuple[list[RawInstrumentRecord], str | None]:
    try:
        with ZipFile(archive_path) as archive:
            members_by_product: dict[str, list[str]] = defaultdict(list)
            for member in archive.namelist():
                product = _product_for_member(member)
                if product is not None:
                    members_by_product[product].append(member)
            for product, members in members_by_product.items():
                products[product] += len(members)
            chosen = selected_product or _default_product(products)
            if chosen is None or chosen not in members_by_product:
                return [], None
            records: list[RawInstrumentRecord] = []
            for member in sorted(members_by_product[chosen]):
                records.extend(
                    _parse_windmaster_member(
                        archive_name=archive_path.name,
                        member=member,
                        product=chosen,
                        payload=archive.read(member),
                        rejections=rejections,
                        time_policy=time_policy,
                    )
                )
            return records, chosen
    except (BadZipFile, OSError):
        _add_rejection(rejections, "windmaster_unreadable_archive")
        return [], None


def _load_uav(path: Path, rejections: Counter[str], policy: InstrumentPolicy) -> list[RawInstrumentRecord]:
    try:
        lines = path.read_text(encoding="gb18030", errors="replace").splitlines()
    except OSError:
        _add_rejection(rejections, "uav_unreadable_file")
        return []
    if len(lines) < 2:
        _add_rejection(rejections, "uav_short_table")
        return []
    records: list[RawInstrumentRecord] = []
    headers = lines[0].split("\t")
    field_indices = {
        "device_sampling_time": 3,
        "pc_collection_time": 21,
    }
    selected_index = field_indices.get(policy.time_policy.uav_time_field)
    if selected_index is None or selected_index >= len(headers):
        _add_rejection(rejections, "uav_selected_time_field_unavailable")
        return []
    for row_number, line in enumerate(lines[1:], start=2):
        fields = line.split("\t")
        if not line.strip() or len(fields) < 22:
            _add_rejection(rejections, "uav_short_row")
            continue
        if selected_index >= len(fields) or not fields[selected_index].strip():
            _add_rejection(rejections, "uav_selected_time_field_missing")
            continue
        raw_time = fields[selected_index]
        device_time = parse_instrument_time_utc(raw_time, policy.time_policy.uav_timezone_offset_hours)
        if device_time is None:
            _add_rejection(rejections, "uav_selected_time_field_invalid")
            continue
        if not policy.valid_year_range[0] <= device_time.year <= policy.valid_year_range[1]:
            _add_rejection(
                rejections,
                "uav_anomalous_device_date"
                if policy.time_policy.uav_time_field == "device_sampling_time"
                else "uav_anomalous_selected_date",
            )
            continue
        lat, lon, altitude, relative_height, speed, direction = (
            _as_number(fields[index]) for index in (4, 5, 6, 7, 11, 12)
        )
        if not _valid_coordinates(lat, lon):
            _add_rejection(rejections, "uav_invalid_coordinates")
            continue
        if any(_is_missing_code(value) for value in (altitude, relative_height, speed, direction)):
            _add_rejection(rejections, "uav_suspected_missing_999")
            continue
        if altitude is None or relative_height is None:
            _add_rejection(rejections, "uav_invalid_height")
            continue
        if speed is None or not 0 <= speed <= 50:
            _add_rejection(rejections, "uav_wind_speed_out_of_range")
            continue
        if direction is None or not 0 <= direction <= 360:
            _add_rejection(rejections, "uav_wind_direction_out_of_range")
            continue
        record_id = f"uav:uav_export:{lat:.6f}:{lon:.6f}:row{row_number}"
        records.append(
            RawInstrumentRecord(
                record_id=record_id,
                source="uav",
                product="uav_export",
                time_utc=device_time,
                lat=lat,
                lon=lon,
                wind_speed=speed,
                wind_direction_deg=direction,
                speed_unit=WindSpeedUnit.UNKNOWN,
                platform_altitude_m=altitude,
                relative_height_m=relative_height,
                moving=True,
                metadata={
                    "flight_id": fields[1],
                    "raw_altitude_column": headers[6],
                    "raw_relative_height_column": headers[7],
                    "input_name": path.name,
                    "row": float(row_number),
                    **_time_metadata(raw_time, policy.time_policy.uav_time_field, policy.time_policy.uav_timezone_offset_hours, device_time),
                },
            )
        )
    return records


def load_instrument_catalog(
    input_dir: Path,
    config: Any | None = None,
    selected_product: str | None = None,
    policy: InstrumentPolicy | None = None,
) -> InstrumentCatalog:
    del config
    policy = policy or InstrumentPolicy()
    root = Path(input_dir)
    paths = [root] if root.is_file() else sorted(path for path in root.iterdir() if path.is_file())
    rejections: Counter[str] = Counter()
    products: Counter[str] = Counter()
    records: list[RawInstrumentRecord] = []
    archives = [path for path in paths if path.suffix.lower() == ".zip"]
    if len(archives) > 1:
        _add_rejection(rejections, "windmaster_multiple_archives")
    chosen_product: str | None = None
    for archive in archives[:1]:
        archive_records, archive_product = _load_windmaster(archive, selected_product, products, rejections, policy.time_policy)
        records.extend(archive_records)
        chosen_product = archive_product
    if selected_product is not None and archives and chosen_product is None:
        raise ValueError(f"Selected WindMaster product is unavailable: {selected_product}")
    for path in (path for path in paths if path.suffix.lower() == ".xls"):
        records.extend(_load_uav(path, rejections, policy))
    records.sort(key=lambda item: (item.time_utc, item.source, item.product, item.record_id))
    return InstrumentCatalog(
        records=tuple(records),
        products=dict(sorted(products.items())),
        selected_product=chosen_product,
        rejected=dict(sorted(rejections.items())),
        provenance={
            "time_policy": str(policy.time_policy),
            "dat_loaded": False,
            "windmaster_archive_count": len(archives),
            "selected_windmaster_product": chosen_product,
        },
    )


def _time_candidate(field: str, raw_field: str, values: list[str], offset: float, valid_year_range: tuple[int, int]) -> dict[str, Any]:
    parsed_by_raw = {
        value: parse_instrument_time_utc(value, offset)
        for value in set(values)
        if value.strip()
    }
    parsed = [parsed_by_raw.get(value) if value.strip() else None for value in values]
    valid = [value for value in parsed if value is not None and valid_year_range[0] <= value.year <= valid_year_range[1]]
    anomaly_count = sum(value is not None and not valid_year_range[0] <= value.year <= valid_year_range[1] for value in parsed)
    return {
        "field": field,
        "raw_field": raw_field,
        "raw_min": min((value for value in values if value.strip()), default=None),
        "raw_max": max((value for value in values if value.strip()), default=None),
        "utc_preview_min": min(valid).isoformat().replace("+00:00", "Z") if valid else None,
        "utc_preview_max": max(valid).isoformat().replace("+00:00", "Z") if valid else None,
        "value_count": len(values),
        "missing_count": sum(not value.strip() for value in values),
        "invalid_count": sum(bool(value.strip()) and parsed_value is None for value, parsed_value in zip(values, parsed)),
        "anomalies": {"year_out_of_range": anomaly_count},
        "utc_values": sorted({value.isoformat().replace("+00:00", "Z") for value in valid}),
    }


def _is_time_header(value: str) -> bool:
    normalized = value.strip().lower().replace("_", "").replace(" ", "")
    return normalized in {"datetime", "time", "samplingtime", "timestamp", "日期时间", "时间", "取样时间", "采集时间"}


def _generic_time_column(columns: Any) -> Any | None:
    aliases = {"time", "datetime", "timestamp", "datetime", "日期时间", "时间", "采集时间"}
    for column in columns:
        normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(column).strip().lower())
        if normalized in aliases or any(alias and alias in normalized for alias in aliases):
            return column
    return None


def scan_instrument_time_candidates(input_dir: Path, policy: InstrumentTimePolicy | None = None) -> dict[str, Any]:
    policy = policy or InstrumentTimePolicy()
    root = Path(input_dir)
    paths = [root] if root.is_file() else sorted(path for path in root.iterdir() if path.is_file())
    sources: dict[str, Any] = {"uav": {"candidates": []}, "windmaster": {"candidates": []}, "dat": {"candidates": []}, "generic_station": {"candidates": []}}
    for path in (path for path in paths if path.suffix.lower() == ".xls"):
        try:
            lines = path.read_text(encoding="gb18030", errors="replace").splitlines()
        except OSError:
            continue
        if not lines:
            continue
        headers = lines[0].split("\t")
        rows = [line.split("\t") for line in lines[1:] if line.strip()]
        for field, index in (("device_sampling_time", 3), ("pc_collection_time", 21)):
            raw_field = headers[index] if index < len(headers) else field
            values = [row[index] if index < len(row) else "" for row in rows]
            sources["uav"]["candidates"].append(_time_candidate(field, raw_field, values, policy.uav_timezone_offset_hours, (2020, 2100)))
    windmaster_values: dict[str, list[str]] = defaultdict(list)
    for path in (path for path in paths if path.suffix.lower() == ".zip"):
        try:
            with ZipFile(path) as archive:
                for member in archive.namelist():
                    if _product_for_member(member) is None:
                        continue
                    lines = archive.read(member).decode("gb18030", errors="replace").splitlines()
                    header_index = next(
                        (
                            index
                            for index, line in enumerate(lines)
                            if any(_is_time_header(value) for value in line.split(","))
                        ),
                        None,
                    )
                    if header_index is None:
                        continue
                    headers = [value.strip() for value in lines[header_index].split(",")]
                    rows = [line.split(",") for line in lines[header_index + 1:] if line.strip()]
                    for index, raw_field in enumerate(headers):
                        if not _is_time_header(raw_field):
                            continue
                        windmaster_values[raw_field].extend(row[index].strip() if index < len(row) else "" for row in rows)
        except (BadZipFile, OSError):
            continue
    for raw_field, values in windmaster_values.items():
        candidate = _time_candidate("sampling_time" if raw_field.lower().replace("_", "") in {"datetime", "time", "samplingtime"} else raw_field, raw_field, values, policy.windmaster_timezone_offset_hours, (2020, 2100))
        if candidate["value_count"] and candidate["invalid_count"] < candidate["value_count"]:
            sources["windmaster"]["candidates"].append(candidate)
    dat_values: list[str] = []
    for path in (path for path in paths if path.suffix.lower() == ".dat"):
        try:
            for line in path.read_text(encoding="gb18030", errors="replace").splitlines():
                match = re.match(r"^(\d{2}:\d{2}:\d{2})\s+(\d{4}-\d{2}-\d{2})\s*$", line)
                if match:
                    dat_values.append(f"{match.group(2)} {match.group(1)}")
        except OSError:
            continue
    if dat_values:
        sources["dat"]["candidates"].append(
            _time_candidate("sampling_time", "DAT profile timestamp", dat_values, policy.dat_timezone_offset_hours, (2020, 2100))
        )
    if root.is_dir():
        for path in DataScanner(root).scan().generic_station:
            frame = None
            try:
                payload = path.read_bytes()
                for encoding in ("utf-8-sig", "gb18030", "latin-1"):
                    try:
                        frame = pd.read_csv(StringIO(payload.decode(encoding)), sep=None, engine="python")
                        break
                    except (UnicodeDecodeError, ValueError):
                        continue
                if frame is None:
                    continue
                time_column = _generic_time_column(frame.columns)
                if time_column is None:
                    continue
                values = ["" if pd.isna(value) else str(value) for value in frame[time_column].tolist()]
                sources["generic_station"]["candidates"].append(
                    _time_candidate("sampling_time", str(time_column), values, policy.dat_timezone_offset_hours, (2020, 2100))
                )
            except (OSError, ValueError):
                continue
    for source, field in (("uav", policy.uav_time_field), ("windmaster", policy.windmaster_time_field), ("dat", "sampling_time"), ("generic_station", "sampling_time")):
        candidates = sources[source]["candidates"]
        selected = next((item for item in candidates if item["field"] == field), None)
        sources[source]["selected_field"] = field
        sources[source]["selected_utc_times"] = [] if selected is None else selected["utc_values"]
    return {"policy": {"uav_time_field": policy.uav_time_field, "uav_timezone_offset_hours": policy.uav_timezone_offset_hours, "windmaster_time_field": policy.windmaster_time_field, "windmaster_timezone_offset_hours": policy.windmaster_timezone_offset_hours, "dat_timezone_offset_hours": policy.dat_timezone_offset_hours}, "sources": sources}


def _sample_dem(dem_path: Path, lat: float, lon: float) -> float | None:
    with rasterio.open(dem_path) as dataset:
        if dataset.crs is None:
            raise ValueError("DEM has no CRS")
        transformer = Transformer.from_crs("EPSG:4326", dataset.crs, always_xy=True)
        x, y = transformer.transform(lon, lat)
        if not isfinite(x) or not isfinite(y):
            return None
        row, col = dataset.index(x, y)
        if not (0 <= row < dataset.height and 0 <= col < dataset.width):
            return None
        sample = next(dataset.sample([(x, y)], masked=True))[0]
        if np.ma.is_masked(sample):
            return None
        value = float(sample)
        if not isfinite(value) or (dataset.nodata is not None and np.isclose(value, dataset.nodata)):
            return None
        return value


def _policy_blocks(record: RawInstrumentRecord, policy: InstrumentPolicy) -> list[str]:
    if record.source == "windmaster":
        direction = policy.windmaster_direction_reference
        unit = policy.windmaster_speed_unit
        geographic = policy.windmaster_is_geographic_wind
        compensated = policy.windmaster_motion_compensated
    else:
        direction = policy.uav_direction_reference
        unit = policy.uav_speed_unit
        geographic = policy.uav_is_geographic_wind
        compensated = policy.uav_motion_compensated
    blocks: list[str] = []
    if direction is WindDirectionReference.UNKNOWN:
        blocks.append(f"{record.source}_wind_direction_reference_unknown")
    if unit is WindSpeedUnit.UNKNOWN:
        blocks.append(f"{record.source}_wind_speed_unit_unknown")
    if not geographic:
        blocks.append(f"{record.source}_wind_not_confirmed_geographic")
    if not compensated and record.source in {"windmaster", "uav"}:
        blocks.append(f"{record.source}_motion_compensation_unconfirmed")
    return blocks


def normalize_catalog(
    catalog: InstrumentCatalog,
    dem_path: Path | None,
    policy: InstrumentPolicy,
) -> NormalizationResult:
    rejections: Counter[str] = Counter()
    observations: list[Observation] = []
    blocking: set[str] = set()
    dem_samples: dict[tuple[float, float], float | None] = {}

    def sample_dem_at(lat: float, lon: float) -> float | None:
        coordinate = (lat, lon)
        if coordinate not in dem_samples:
            dem_samples[coordinate] = _sample_dem(Path(dem_path), lat, lon) if dem_path is not None else None
        return dem_samples[coordinate]

    cached_launch_elevation = policy.uav_launch_ground_altitude_m
    if cached_launch_elevation is None and policy.uav_height_reference is UavHeightReference.RELATIVE_TO_LAUNCH and policy.uav_launch_location_confirmed and dem_path is not None:
        cached_launch_elevation = sample_dem_at(policy.uav_launch_lat, policy.uav_launch_lon)
    for record in catalog.records:
        if not _valid_coordinates(record.lat, record.lon) or not all(isfinite(v) for v in (record.wind_speed, record.wind_direction_deg)) or record.wind_speed < 0:
            _add_rejection(rejections, f"{record.source}_invalid_physical_values")
            continue
        if any(value is not None and not isfinite(value) for value in (record.gate_position_m, record.platform_altitude_m, record.relative_height_m)):
            _add_rejection(rejections, f"{record.source}_invalid_height_fields")
            continue
        blocks = _policy_blocks(record, policy)
        if blocks:
            for reason in blocks:
                _add_rejection(rejections, reason)
                blocking.add(reason)
            continue
        dem_elevation: float | None = None
        launch_elevation: float | None = None
        if dem_path is not None:
            dem_elevation = sample_dem_at(record.lat, record.lon)
            if dem_elevation is None:
                _add_rejection(rejections, f"{record.source}_invalid_dem_sample")
                continue
        if record.source == "windmaster":
            if record.gate_position_m is None:
                _add_rejection(rejections, "windmaster_missing_gate_position")
                continue
            if policy.windmaster_height_reference is RadarHeightReference.UNKNOWN:
                reason = "windmaster_height_reference_unknown"
                _add_rejection(rejections, reason)
                blocking.add(reason)
                continue
            if policy.windmaster_height_reference is RadarHeightReference.AGL:
                instrument_agl = record.gate_position_m
                height_method = "confirmed_gate_agl"
            else:
                if policy.windmaster_platform_agl_m is not None:
                    instrument_agl = policy.windmaster_platform_agl_m + record.gate_position_m
                    height_method = "platform_agl_plus_vertical_gate"
                elif record.platform_altitude_m is None:
                    reason = "windmaster_missing_platform_altitude_or_agl"
                    _add_rejection(rejections, reason)
                    blocking.add(reason)
                    continue
                elif dem_path is None:
                    reason = "dem_required_for_platform_vertical_offset"
                    _add_rejection(rejections, reason)
                    blocking.add(reason)
                    continue
                else:
                    instrument_agl = record.platform_altitude_m + record.gate_position_m - dem_elevation
                    height_method = "confirmed_platform_vertical_offset"
        else:
            reference = policy.uav_height_reference
            if reference is UavHeightReference.UNKNOWN:
                reason = "uav_height_reference_unknown"
                _add_rejection(rejections, reason)
                blocking.add(reason)
                continue
            if reference is UavHeightReference.RELATIVE_AGL:
                if not policy.uav_relative_agl_confirmed:
                    reason = "uav_relative_agl_unconfirmed"
                    _add_rejection(rejections, reason)
                    blocking.add(reason)
                    continue
                if record.relative_height_m is None:
                    _add_rejection(rejections, "uav_missing_relative_height")
                    continue
                instrument_agl = record.relative_height_m
                height_method = "explicit_relative_agl_assumption"
            else:
                if dem_path is None:
                    reason = "dem_required_for_uav_height"
                    _add_rejection(rejections, reason)
                    blocking.add(reason)
                    continue
                if reference is UavHeightReference.GPS_ALTITUDE_MINUS_DEM:
                    if record.platform_altitude_m is None:
                        _add_rejection(rejections, "uav_missing_gps_altitude")
                        continue
                    instrument_agl = record.platform_altitude_m - dem_elevation
                    height_method = reference.value
                elif reference is UavHeightReference.RELATIVE_TO_LAUNCH:
                    if record.relative_height_m is None:
                        _add_rejection(rejections, "uav_missing_relative_height")
                        continue
                    launch_elevation = cached_launch_elevation
                    if launch_elevation is None:
                        if not policy.uav_launch_location_confirmed:
                            reason = "uav_launch_ground_altitude_or_confirmed_location_required"
                            _add_rejection(rejections, reason)
                            blocking.add(reason)
                            continue
                        if launch_elevation is None:
                            _add_rejection(rejections, "uav_invalid_launch_dem_sample")
                            continue
                    instrument_agl = launch_elevation + record.relative_height_m - dem_elevation
                    height_method = reference.value
                else:
                    if not policy.uav_legacy_gps_is_launch_altitude_confirmed:
                        reason = "uav_legacy_gps_is_launch_altitude_unconfirmed"
                        _add_rejection(rejections, reason)
                        blocking.add(reason)
                        continue
                    if record.platform_altitude_m is None or record.relative_height_m is None:
                        _add_rejection(rejections, "uav_missing_altitude_fields")
                        continue
                    instrument_agl = record.platform_altitude_m + record.relative_height_m - dem_elevation
                    height_method = "legacy_gps_launch_amsl_plus_relative_minus_dem"
        height_above_vegetation = instrument_agl - policy.canopy_height_m
        if not isfinite(height_above_vegetation) or height_above_vegetation <= 0:
            _add_rejection(rejections, f"{record.source}_nonpositive_height_above_vegetation")
            continue
        direction_reference = (policy.windmaster_direction_reference if record.source == "windmaster" else policy.uav_direction_reference)
        direction_from = (record.wind_direction_deg + (180.0 if direction_reference is WindDirectionReference.TO_TRUE_NORTH else 0.0)) % 360.0
        metadata: dict[str, Any] = {
            **dict(record.metadata),
            "original_wind_direction_deg": record.wind_direction_deg,
            "original_wind_direction_reference": direction_reference.value,
            "raw_gps_altitude_m": record.platform_altitude_m if record.source == "uav" else None,
            "raw_platform_altitude_m": record.platform_altitude_m,
            "raw_relative_height_m": record.relative_height_m,
            "raw_gate_position_m": record.gate_position_m,
            "radar_platform_agl_m": policy.windmaster_platform_agl_m if record.source == "windmaster" else None,
            "terrain_altitude_m": dem_elevation,
            "launch_ground_altitude_m": launch_elevation,
            "height_reference": "above_vegetation",
            "record_id": record.record_id,
            "product": record.product,
            "wind_direction_reference": WindDirectionReference.FROM_TRUE_NORTH.value,
            "wind_speed_unit": (
                policy.windmaster_speed_unit.value
                if record.source == "windmaster"
                else policy.uav_speed_unit.value
            ),
            "instrument_agl_m": instrument_agl,
            "height_above_vegetation_m": height_above_vegetation,
            "canopy_height_m": policy.canopy_height_m,
            "canopy_height_assumption": policy.canopy_height_assumption,
            "height_method": height_method,
            "raw_quality": dict(record.quality),
            "raw_statistics": dict(record.raw_statistics),
        }
        if dem_elevation is not None:
            metadata["dem_elevation_m"] = dem_elevation
        observations.append(
            Observation(
                source=record.source,
                station=record.record_id,
                time_utc=record.time_utc,
                lat=record.lat,
                lon=record.lon,
                height_m=height_above_vegetation,
                speed_ms=record.wind_speed,
                direction_deg=direction_from,
                moving=record.moving,
                metadata=metadata,
            )
        )
    observations.sort(key=lambda item: (item.time_utc, item.source, item.station))
    return NormalizationResult(
        observations=tuple(observations),
        rejected=dict(sorted(rejections.items())),
        provenance={
            "canopy_height_m": policy.canopy_height_m,
            "canopy_height_assumption": policy.canopy_height_assumption,
            "dem_used": dem_path is not None,
            "solver_safe_only": True,
        },
        blocking_reasons=tuple(sorted(blocking)),
    )
