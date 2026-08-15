from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from wind3d_ninja.config.models import SourceConfig
from wind3d_ninja.readers import DatReader, UavReader, WindMasterReader


def test_supported_readers(tmp_path: Path) -> None:
    archive_path = tmp_path / "WindMaster.zip"
    profile = "\n".join(
        [
            "HeaderInfo,Latitude:25.025510,longtitude:102.372006,Timezone:UTC-8",
            "Date_time,113m WindSpeed,113m WindDirection,113m DataObtainRate",
            "20240403 16:16:00,3.1,172.5,0.95",
        ]
    )
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("day/level2/sample_Ave01min_test.csv", profile.encode("gbk"))
    windmaster = WindMasterReader(
        archive_path,
        SourceConfig(station_prefix="WM", max_height_m=200, min_obtain_rate=0.8),
    ).read()
    assert len(windmaster) == 1
    assert windmaster[0].height_m == 113
    dat_path = tmp_path / "sample.dat"
    dat_path.write_text(
        "GPS LAT=9999.00 GPS LONG=9999.00 T=0.0 H=30 P=300\n"
        "16:16:31 2024-04-03\n"
        "height speed direction vertical\n"
        "10 357 214 54\n",
        encoding="gbk",
    )
    dat = DatReader(
        dat_path,
        SourceConfig(station_prefix="DAT", speed_divisor=100, max_height_m=200),
    ).read()
    assert len(dat) == 1
    assert dat[0].lat is None
    assert dat[0].speed_ms == 3.57
    dem_path = tmp_path / "terrain.tif"
    with rasterio.open(
        dem_path,
        "w",
        driver="GTiff",
        width=40,
        height=40,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(102.2, 25.2, 0.01, 0.01),
        nodata=-9999.0,
    ) as dataset:
        dataset.write(np.full((40, 40), 100.0, dtype=np.float32), 1)
    uav_path = tmp_path / "UAV.xls"
    headers = [f"c{index}" for index in range(22)]
    values = ["x"] * 22
    values[1] = "FLY1"
    values[4] = "25.0256"
    values[5] = "102.3718"
    values[6] = "101.0"
    values[7] = "1.0"
    values[8] = "22.0"
    values[11] = "1.4"
    values[12] = "286"
    values[21] = "2024-04-03 16:16:02"
    uav_path.write_text("\t".join(headers) + "\n" + "\t".join(values) + "\n", encoding="gb18030")
    uav_reader = UavReader(
        uav_path,
        SourceConfig(station_prefix="UAV", agl_threshold_drop_m=-4, agl_threshold_fix_m=0.1),
    )
    uav = uav_reader.read(dem_path=dem_path)
    assert len(uav) == 1
    assert uav[0].height_m == 2.0
    assert uav[0].metadata["relative_height_m"] == 1.0
    assert "takeoff_elevation_m" not in uav[0].metadata
    assert uav[0].metadata["current_altitude_m"] == 101.0
    assert uav[0].metadata["dem_elevation_m"] == 100.0
    assert uav[0].metadata["relative_plus_gps_altitude_m"] == 102.0


def test_uav_dem_agl_and_thresholds(tmp_path: Path) -> None:
    dem_path = tmp_path / "terrain.tif"
    with rasterio.open(
        dem_path,
        "w",
        driver="GTiff",
        width=40,
        height=40,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(102.2, 25.2, 0.01, 0.01),
        nodata=-9999.0,
    ) as dataset:
        dataset.write(np.full((40, 40), 100.0, dtype=np.float32), 1)

    headers = [f"c{index}" for index in range(22)]

    def row(altitude: float, relative_height: float, time_value: str) -> str:
        values = ["x"] * 22
        values[1] = "FLY1"
        values[4] = "25.0256"
        values[5] = "102.3718"
        values[6] = str(altitude)
        values[7] = str(relative_height)
        values[8] = "22.0"
        values[11] = "1.4"
        values[12] = "286"
        values[21] = time_value
        return "\t".join(values)

    uav_path = tmp_path / "UAV_thresholds.xls"
    uav_path.write_text(
        "\t".join(headers)
        + "\n"
        + "\n".join(
            [
                row(100.0, 0.0, "2024-04-03 16:16:00"),
                row(96.0, 0.0, "2024-04-03 16:17:00"),
                row(95.0, 0.0, "2024-04-03 16:18:00"),
                row(99.0, 0.0, "2024-04-03 16:19:00"),
                row(100.05, 0.0, "2024-04-03 16:20:00"),
                row(108.0, 10.0, "2024-04-03 16:21:00"),
            ]
        )
        + "\n",
        encoding="gb18030",
    )
    reader = UavReader(
        uav_path,
        SourceConfig(station_prefix="UAV", agl_threshold_drop_m=-4, agl_threshold_fix_m=0.1),
    )
    observations = reader.read(dem_path=dem_path)

    assert [item.height_m for item in observations] == pytest.approx([0.1, 0.1, 0.1, 0.05, 18.0])
    assert all("takeoff_elevation_m" not in item.metadata for item in observations)
    assert all(
        item.metadata["height_method"]
        == "gps_current_altitude_plus_relative_height_minus_dem_elevation"
        for item in observations
    )
