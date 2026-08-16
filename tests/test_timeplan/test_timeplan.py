from datetime import datetime, timezone

from wind3d_ninja.config.models import QualityRule
from wind3d_ninja.observations.coordinator import complete_coordinates
from wind3d_ninja.observations.models import Observation
from wind3d_ninja.postprocess.netcdf_writer import make_netcdf_filename
from wind3d_ninja.timeplan.axis import TimeAxis
from wind3d_ninja.timeplan.selector import ObservationSelector
from wind3d_ninja.utils.timezone import parse_time_range_utc


def observation(source: str, height: float, minute: int, second: int = 30) -> Observation:
    return Observation(
        source=source,
        station=f"{source}_{height}",
        time_utc=datetime(2024, 4, 3, 8, minute, second, tzinfo=timezone.utc),
        lat=25.0,
        lon=102.0,
        height_m=height,
        speed_ms=3.0,
        direction_deg=180.0,
    )


def test_time_axis_selection_and_filename() -> None:
    values = [observation("windmaster", 28, 16), observation("windmaster", 113, 16), observation("dat", 10, 17)]
    times = TimeAxis.extract_unique_times(values)
    assert [value.minute for value in times] == [16, 17]
    assert values[0].time_utc.second == 30
    selector = ObservationSelector({"windmaster": QualityRule((113,), 120)})
    selected = selector.select_for_time(times[0], values)
    assert [item.height_m for item in selected] == [113]
    assert make_netcdf_filename(times[0], 200, 10) == "Wind3D_20240403T081600Z_200m_h010m.nc"


def test_parse_time_range_uses_offset_and_is_inclusive() -> None:
    value = parse_time_range_utc(
        ("2024-04-03 16:16", "2024-04-03 16:30"),
        8,
    )
    assert value == (
        datetime(2024, 4, 3, 8, 16, tzinfo=timezone.utc),
        datetime(2024, 4, 3, 8, 30, tzinfo=timezone.utc),
    )


def test_selector_uses_source_time_offset_window() -> None:
    values = [
        observation("windmaster", 113, 13, 59),
        observation("windmaster", 113, 14, 0),
        observation("windmaster", 113, 16, 30),
        observation("windmaster", 113, 18, 0),
        observation("windmaster", 113, 18, 1),
    ]
    selector = ObservationSelector({"windmaster": QualityRule((113,), 120)})
    selected = selector.select_for_time(
        datetime(2024, 4, 3, 8, 16, tzinfo=timezone.utc),
        values,
    )
    assert [(item.time_utc.minute, item.time_utc.second) for item in selected] == [(16, 30)]


def test_selector_keeps_one_nearest_record_per_station() -> None:
    values = [
        observation("windmaster", 113, 16, 0),
        observation("windmaster", 113, 16, 30),
        observation("dat", 10, 15, 0),
        observation("dat", 10, 16, 59),
    ]
    selector = ObservationSelector(
        {
            "windmaster": QualityRule((113,), 120),
            "dat": QualityRule((10,), 120),
        }
    )

    selected = selector.select_for_time(
        datetime(2024, 4, 3, 8, 16, tzinfo=timezone.utc),
        values,
    )

    assert [(item.source, item.time_utc.minute, item.time_utc.second) for item in selected] == [
        ("dat", 16, 59),
        ("windmaster", 16, 0),
    ]


def test_selector_without_window_matches_the_same_minute() -> None:
    values = [
        observation("uav", 20, 16, 59),
        observation("uav", 20, 17, 0),
    ]
    selected = ObservationSelector().select_for_time(
        datetime(2024, 4, 3, 8, 16, tzinfo=timezone.utc),
        values,
    )
    assert [(item.time_utc.minute, item.time_utc.second) for item in selected] == [(16, 59)]


def test_coordinate_completion_still_matches_by_minute() -> None:
    windmaster = observation("windmaster", 113, 16, 0)
    dat = Observation(
        source="dat",
        station="dat_10",
        time_utc=datetime(2024, 4, 3, 8, 16, 31, tzinfo=timezone.utc),
        lat=None,
        lon=None,
        height_m=10,
        speed_ms=3.0,
        direction_deg=180.0,
    )
    completed = complete_coordinates([windmaster, dat])
    assert completed[1].lat == windmaster.lat
    assert completed[1].lon == windmaster.lon
    assert completed[1].time_utc.second == 31
