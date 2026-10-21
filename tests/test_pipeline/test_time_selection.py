from datetime import timezone
from pathlib import Path
from zipfile import ZipFile

from wind3d_ninja.config.models import AppConfig, SourceConfig
from wind3d_ninja.observations.instruments import (
    InstrumentPolicy,
    InstrumentTimePolicy,
    load_instrument_catalog,
    parse_instrument_time_utc,
    scan_instrument_time_candidates,
)
from wind3d_ninja.pipeline.inspector import InputInspector


def _uav(path: Path, device: str, pc: str) -> None:
    headers = [f"c{index}" for index in range(22)]
    values = ["x"] * 22
    values[1], values[3], values[4], values[5] = "F1", device, "30", "120"
    values[6], values[7], values[8], values[11], values[12], values[21] = "100", "10", "", "3", "180", pc
    path.write_text("\t".join(headers) + "\n" + "\t".join(values) + "\n", encoding="gb18030")


def _config(offset: float) -> AppConfig:
    sources = {name: SourceConfig(tz_offset_hours=offset) for name in ("uav", "windmaster", "dat", "generic_station")}
    return AppConfig(Path("windninja"), sources, {})


def test_standard_config_offset_and_explicit_policy_choose_different_uav_clocks(tmp_path: Path) -> None:
    _uav(tmp_path / "UAV.xls", "2024-04-03 16:06:00", "2024-04-03 21:46:36")
    default = InputInspector(_config(0)).load(tmp_path).uav_inputs[0][1][0]
    explicit_policy = InstrumentPolicy(time_policy=InstrumentTimePolicy(uav_timezone_offset_hours=8))
    chosen = InputInspector(_config(0), explicit_policy).load(tmp_path).uav_inputs[0][1][0]
    pc_policy = InstrumentPolicy(time_policy=InstrumentTimePolicy(uav_time_field="pc_collection_time", uav_timezone_offset_hours=8))
    pc = InputInspector(_config(0), pc_policy).load(tmp_path).uav_inputs[0][1][0]
    research_catalog = load_instrument_catalog(tmp_path, policy=pc_policy)
    assert default.time_utc.hour == 21
    assert chosen.time_utc.hour == 8
    assert chosen.time_metadata["time_source_field"] == "device_sampling_time"
    assert pc.time_utc.hour == 13
    assert pc.time_metadata["time_source_field"] == "pc_collection_time"
    assert research_catalog.records[0].time_utc == pc.time_utc


def test_explicit_offset_is_converted_once() -> None:
    parsed = parse_instrument_time_utc("2024-04-03T16:06:00+08:00", 8)
    assert parsed is not None
    assert parsed == parsed.replace(hour=8)
    assert parsed.tzinfo is timezone.utc


def test_missing_selected_uav_field_never_falls_back(tmp_path: Path) -> None:
    _uav(tmp_path / "UAV.xls", "", "2024-04-03 21:46:36")
    policy = InstrumentPolicy(time_policy=InstrumentTimePolicy(uav_time_field="device_sampling_time"))
    catalog = load_instrument_catalog(tmp_path, policy=policy)
    assert not catalog.records
    assert catalog.rejected["uav_selected_time_field_missing"] == 1


def test_scan_aggregates_zip_members_and_ignores_anomalous_year_for_preview(tmp_path: Path) -> None:
    _uav(tmp_path / "UAV.xls", "2015-10-21 21:00:00", "2024-04-03 21:46:36")
    table = "header,Latitude:30,Longitude:120\nDate_time,28m WindSpeed,28m WindDirection\n{time},3,180\n"
    with ZipFile(tmp_path / "WindMaster.zip", "w") as archive:
        archive.writestr("a/level2/Sins_Ave01min_a.csv", table.format(time="20240403 16:16:00").encode("gb18030"))
        archive.writestr("b/level2/Sins_Ave01min_b.csv", table.format(time="20240403 16:50:00").encode("gb18030"))
        archive.writestr("c/level2/Sins_Ave01min_c.csv", "Date_time,28m WindSpeed,28m WindDirection\n20240403 17:00:00,3,180\n".encode("gb18030"))
    scan = scan_instrument_time_candidates(tmp_path)
    uav = scan["sources"]["uav"]["candidates"]
    pc = next(item for item in uav if item["field"] == "pc_collection_time")
    windmaster = scan["sources"]["windmaster"]["candidates"][0]
    assert pc["utc_preview_min"] == "2024-04-03T13:46:36Z"
    assert windmaster["utc_preview_max"] == "2024-04-03T09:00:00Z"
    assert scan["sources"]["uav"]["candidates"][0]["anomalies"]["year_out_of_range"] == 1


def test_scan_generic_station_uses_actual_time_column_and_selected_offset(tmp_path: Path) -> None:
    (tmp_path / "station.csv").write_text(
        "Date_time,Latitude,Longitude,Height_m,WindSpeed,WindDirection\n"
        "2024-04-03 16:06:00,30,120,10,3,180\n"
        "2024-04-03T17:00:00Z,30,120,10,3,180\n",
        encoding="utf-8",
    )
    utc = scan_instrument_time_candidates(tmp_path, InstrumentTimePolicy(dat_timezone_offset_hours=0))
    plus_eight = scan_instrument_time_candidates(tmp_path, InstrumentTimePolicy(dat_timezone_offset_hours=8))
    utc_candidate = utc["sources"]["generic_station"]["candidates"][0]
    east_candidate = plus_eight["sources"]["generic_station"]["candidates"][0]
    assert utc_candidate["raw_field"] == "Date_time"
    assert utc["sources"]["generic_station"]["selected_utc_times"] == ["2024-04-03T16:06:00Z", "2024-04-03T17:00:00Z"]
    assert east_candidate["utc_preview_min"] == "2024-04-03T08:06:00Z"
    assert "2024-04-03T17:00:00Z" in plus_eight["sources"]["generic_station"]["selected_utc_times"]
