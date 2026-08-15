from __future__ import annotations

import json
import zipfile
from pathlib import Path

import netCDF4 as nc
import numpy as np
import rasterio
from pyproj import CRS
from rasterio.transform import from_origin

from wind3d_ninja.config.loader import load_config
from wind3d_ninja.dem.manager import DemManager
from wind3d_ninja.pipeline.planner import PipelinePlanner
from wind3d_ninja.pipeline.runner import PipelineRunner
from wind3d_ninja.windninja.runner import WindNinjaResult


class FakeWindNinjaRunner:
    def run(self, config_file: Path, work_dir: Path) -> WindNinjaResult:
        speed = work_dir / "fixture_vel.asc"
        direction = work_dir / "fixture_ang.asc"
        projection = work_dir / "fixture_vel.prj"
        kmz = work_dir / "fixture.kmz"
        log = work_dir / "windninja.log"
        content = (
            "ncols 2\n"
            "nrows 2\n"
            "xllcorner 230000\n"
            "yllcorner 2760000\n"
            "cellsize 200\n"
            "NODATA_value -9999\n"
        )
        speed.write_text(content + "3 3\n3 3\n", encoding="utf-8")
        direction.write_text(content + "180 180\n180 180\n", encoding="utf-8")
        projection.write_text(CRS.from_epsg(32648).to_wkt(), encoding="utf-8")
        with zipfile.ZipFile(kmz, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("doc.kml", "<kml><Document><name>WindNinja</name></Document></kml>")
        log.write_text("ok\n", encoding="utf-8")
        return WindNinjaResult(speed, direction, projection, kmz, config_file, log, ("fake",))


def create_inputs(root: Path) -> None:
    profile = "\n".join(
        [
            "HeaderInfo,Latitude:25.025510,longtitude:102.372006,Timezone:UTC-8",
            "Date_time,113m WindSpeed,113m WindDirection,113m DataObtainRate",
            "20240403 16:16:00,3.1,172.5,0.95",
        ]
    )
    with zipfile.ZipFile(root / "WindMaster.zip", "w") as archive:
        archive.writestr("day/level2/sample_Ave01min_test.csv", profile.encode("gbk"))
    (root / "sample.dat").write_text(
        "GPS LAT=9999.00 GPS LONG=9999.00 T=0.0 H=30 P=300\n"
        "16:16:31 2024-04-03\n"
        "height speed direction vertical\n"
        "10 357 214 54\n",
        encoding="gbk",
    )
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
    (root / "UAV.xls").write_text("\t".join(headers) + "\n" + "\t".join(values) + "\n", encoding="gb18030")
    with rasterio.open(
        root / "terrain.tif",
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


def test_pipeline_fixture(tmp_path: Path, capsys) -> None:
    create_inputs(tmp_path)
    output = tmp_path / "output"
    result = PipelineRunner(load_config(), FakeWindNinjaRunner()).run(
        tmp_path,
        output,
        [10, 50],
        [100, 200],
        5.0,
        None,
        True,
    )
    assert len(result.netcdf_files) == 4
    assert len(result.kmz_files) == 4
    assert all(zipfile.is_zipfile(path) for path in result.kmz_files)
    assert result.manifest.is_file()
    assert {path.name for path in result.netcdf_files} == {
        "Wind3D_20240403T081600Z_100m_h010m.nc",
        "Wind3D_20240403T081600Z_200m_h010m.nc",
        "Wind3D_20240403T081600Z_100m_h050m.nc",
        "Wind3D_20240403T081600Z_200m_h050m.nc",
    }
    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    assert manifest["mesh_resolutions_m"] == [100, 200]
    assert {item["mesh_resolution_m"] for item in manifest["runs"]} == {100, 200}
    with nc.Dataset(result.netcdf_files[0]) as dataset:
        assert dataset.variables["height"][0] == 10
        assert set(dataset.variables) >= {"U_wind", "V_wind"}
        assert "wind_speed" not in dataset.variables
        assert "wind_from_direction" not in dataset.variables
    progress = capsys.readouterr().err
    assert "[1/4]" in progress
    assert "[4/4]" in progress


def test_planner_resolves_dem_before_uav_conversion(tmp_path: Path, monkeypatch) -> None:
    create_inputs(tmp_path)
    dem_path = tmp_path / "terrain.tif"
    captured: dict[str, object] = {}

    def fake_get_dem(self, scanned_dems, observations, buffer_km, manual_bounds=None):
        captured["scanned_dems"] = list(scanned_dems)
        captured["observation_count"] = len(list(observations))
        captured["buffer_km"] = buffer_km
        return dem_path

    monkeypatch.setattr(DemManager, "get_dem", fake_get_dem)
    result = PipelinePlanner(load_config()).plan(
        tmp_path,
        [10],
        [200],
        10.0,
        None,
    )

    assert result["dem_path"] == dem_path
    assert result["uav_metadata_count"] == 1
    assert result["uav_observation_count"] == 1
    assert captured["observation_count"] == 3
    assert captured["buffer_km"] == 10.0
