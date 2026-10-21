from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import numpy as np
import rasterio
from rasterio.transform import from_origin

from wind3d_ninja.observations.instruments import (
    InstrumentPolicy,
    RadarHeightReference,
    UavHeightReference,
    WindDirectionReference,
    WindSpeedUnit,
    load_instrument_catalog,
    normalize_catalog,
)


def _profile(*, product: str, speed: str = "3", direction: str = "90", rate: str = "1") -> str:
    return "\n".join(
        [
            "HeaderInfo,Latitude:30.0,longtitude:120.0,Timezone:UTC-8",
            "Date_time,Latitude,Longitude,Altitude,28m WindSpeed,28m WindDirection,28m MeanSNR(dB),28m DataObtainRate,28m StdDev,28m ZWind,28m ZWind StdDev",
            f"20240403 21:00:00,30.0,120.0,150,{speed},{direction},10,{rate},0.4,0.1,0.2",
        ]
    )


def _write_archive(path: Path) -> None:
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "day/level2/DroneInsAve01min_20240403.csv",
            _profile(product="drone").encode("gb18030"),
        )
        archive.writestr(
            "day/level2/SinsAve01min_20240403.csv",
            _profile(product="sins", speed="4").encode("gb18030"),
        )
        archive.writestr("day/level0/ignore.DAT", b"must never be parsed")


def _write_uav(path: Path, *, device_time: str = "2024-04-03 21:00:00") -> None:
    headers = [f"column_{index}" for index in range(22)]
    values = ["x"] * 22
    values[3] = device_time
    values[4] = "30.0"
    values[5] = "120.0"
    values[6] = "150"
    values[7] = "20"
    values[11] = "4"
    values[12] = "180"
    values[21] = "2024-04-03 21:00:02"
    path.write_text("\t".join(headers) + "\n" + "\t".join(values) + "\n", encoding="gb18030")


def _write_dem(path: Path) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=4,
        height=4,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(119.0, 31.0, 1.0, 1.0),
    ) as dataset:
        dataset.write(np.full((4, 4), 100.0, dtype=np.float32), 1)


def test_catalog_selects_one_product_never_loads_dat_and_uses_beijing(tmp_path: Path) -> None:
    _write_archive(tmp_path / "WindMaster.zip")
    (tmp_path / "legacy.dat").write_text("not an instrument export", encoding="utf-8")

    catalog = load_instrument_catalog(tmp_path)

    assert catalog.selected_product == "sins_level2_ave01min"
    assert set(catalog.products) == {"droneins_level2_ave01min", "sins_level2_ave01min"}
    assert {record.product for record in catalog.records} == {"sins_level2_ave01min"}
    assert catalog.provenance["dat_loaded"] is False
    assert catalog.records[0].time_utc.isoformat() == "2024-04-03T13:00:00+00:00"
    assert catalog.records[0].record_id == "windmaster:sins_level2_ave01min:30.000000:120.000000:28m"
    selected = load_instrument_catalog(tmp_path, selected_product="sins_level2_ave01min")
    assert {record.product for record in selected.records} == {"sins_level2_ave01min"}
    assert selected.records[0].wind_speed == 4


def test_catalog_reports_sentinel_and_physical_rejections(tmp_path: Path) -> None:
    with ZipFile(tmp_path / "WindMaster.zip", "w") as archive:
        archive.writestr(
            "day/level2/DroneInsAve01min_20240403.csv",
            "\n".join(
                [
                    "HeaderInfo,Latitude:30.0,longtitude:120.0",
                    "Date_time,28m WindSpeed,28m WindDirection,28m DataObtainRate",
                    "20240403 21:00:00,999,90,1",
                    "20240403 21:01:00,71,90,1",
                    "20240403 21:02:00,3,361,1",
                    "20240403 21:03:00,3,90,1.2",
                ]
            ).encode("gb18030"),
        )

    catalog = load_instrument_catalog(tmp_path)

    assert not catalog.records
    assert catalog.rejected["windmaster_suspected_missing_999"] == 1
    assert catalog.rejected["windmaster_wind_speed_out_of_range"] == 1
    assert catalog.rejected["windmaster_wind_direction_out_of_range"] == 1
    assert catalog.rejected["windmaster_obtain_rate_out_of_range"] == 1


def test_catalog_does_not_merge_sins_scalar_and_vector_products(tmp_path):
    path = tmp_path / "WindMaster.zip"
    with ZipFile(path, "w") as archive:
        archive.writestr("day/level2/SinsAve01min_20240403.csv",
                         _profile(product="sins", direction="90").encode("gb18030"))
        archive.writestr("day/level2/SinsVectorAve01min_20240403.csv",
                         _profile(product="sins_vector", direction="270").encode("gb18030"))
    catalog = load_instrument_catalog(tmp_path)
    assert catalog.selected_product == "sins_level2_ave01min"
    assert len(catalog.records) == 1
    assert catalog.records[0].wind_direction_deg == 90
    vector = load_instrument_catalog(tmp_path, selected_product="sins_vector_level2_ave01min")
    assert len(vector.records) == 1
    assert vector.records[0].wind_direction_deg == 270

def test_unknown_conventions_block_then_confirmed_vertical_offset_normalizes(tmp_path: Path) -> None:
    _write_archive(tmp_path / "WindMaster.zip")
    dem_path = tmp_path / "dem.tif"
    _write_dem(dem_path)
    catalog = load_instrument_catalog(tmp_path)

    blocked = normalize_catalog(catalog, dem_path, InstrumentPolicy())
    assert not blocked.observations
    assert "windmaster_wind_direction_reference_unknown" in blocked.blocking_reasons
    assert "windmaster_wind_speed_unit_unknown" in blocked.blocking_reasons
    assert "windmaster_motion_compensation_unconfirmed" in blocked.blocking_reasons

    height_unknown = normalize_catalog(
        catalog,
        dem_path,
        InstrumentPolicy(
            windmaster_direction_reference=WindDirectionReference.FROM_TRUE_NORTH,
            windmaster_speed_unit=WindSpeedUnit.METERS_PER_SECOND,
            windmaster_is_geographic_wind=True,
            windmaster_motion_compensated=True,
        ),
    )
    assert "windmaster_height_reference_unknown" in height_unknown.blocking_reasons

    allowed = normalize_catalog(
        catalog,
        dem_path,
        InstrumentPolicy(
            windmaster_direction_reference=WindDirectionReference.FROM_TRUE_NORTH,
            windmaster_speed_unit=WindSpeedUnit.METERS_PER_SECOND,
            windmaster_height_reference=RadarHeightReference.PLATFORM_VERTICAL_OFFSET,
            windmaster_is_geographic_wind=True,
            windmaster_motion_compensated=True,
            canopy_height_m=2,
        ),
    )
    assert len(allowed.observations) == 1
    assert allowed.observations[0].height_m == 76
    assert allowed.observations[0].metadata["instrument_agl_m"] == 78
    assert allowed.observations[0].metadata["height_above_vegetation_m"] == 76
    assert allowed.observations[0].metadata["raw_statistics"] == {
        "StdDev": 0.4,
        "ZWind": 0.1,
        "ZWind StdDev": 0.2,
    }


def test_uav_anomalous_device_date_is_rejected_and_relative_agl_requires_confirmation(tmp_path: Path) -> None:
    uav_path = tmp_path / "UAV.xls"
    _write_uav(uav_path, device_time="2015-10-21 21:00:00")
    bad_catalog = load_instrument_catalog(tmp_path)
    assert not bad_catalog.records
    assert bad_catalog.rejected["uav_anomalous_device_date"] == 1

    _write_uav(uav_path)
    catalog = load_instrument_catalog(tmp_path)
    blocked = normalize_catalog(catalog, None, InstrumentPolicy())
    assert "uav_wind_not_confirmed_geographic" in blocked.blocking_reasons
    assert "uav_motion_compensation_unconfirmed" in blocked.blocking_reasons

    policy = InstrumentPolicy(
        uav_direction_reference=WindDirectionReference.FROM_TRUE_NORTH,
        uav_speed_unit=WindSpeedUnit.METERS_PER_SECOND,
        uav_is_geographic_wind=True,
        uav_motion_compensated=True,
        uav_height_reference=UavHeightReference.RELATIVE_AGL,
        uav_relative_agl_confirmed=True,
    )
    normalized = normalize_catalog(catalog, None, policy)
    assert len(normalized.observations) == 1
    assert normalized.observations[0].height_m == 20
    assert normalized.observations[0].time_utc.isoformat() == "2024-04-03T13:00:00+00:00"
