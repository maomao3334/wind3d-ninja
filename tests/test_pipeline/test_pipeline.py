from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import netCDF4 as nc
import numpy as np
import rasterio
import pytest
from pyproj import CRS
from rasterio.transform import from_origin

from wind3d_ninja.config.loader import load_config
from wind3d_ninja.dem.manager import DemManager
from wind3d_ninja.pipeline.planner import PipelinePlanner
from wind3d_ninja.pipeline.runner import PipelineRunner
from wind3d_ninja.pipeline.inspector import InputInspector
from wind3d_ninja.postprocess.kmz_writer import KmzWriter
from wind3d_ninja.utils.geometry import GeoBounds
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
            kml = "<kml><Document><name>WindNinja</name>"
            if "turbulence_output_flag = true" in config_file.read_text(encoding="utf-8"):
                kml += "<Folder><name>Column Max Velocity Fluctuations</name></Folder>"
            archive.writestr("doc.kml", kml + "</Document></kml>")
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


def test_generic_station_csv_adapter(tmp_path: Path) -> None:
    (tmp_path / "station.csv").write_text(
        "Station,DateTime,Latitude,Longitude,Height_ft,WindSpeed_kmh,WindDirection\n"
        "S1,2024-04-03 16:16:00,25.0255,102.3720,32.8084,36,180\n",
        encoding="utf-8",
    )
    from wind3d_ninja.readers.generic_station import GenericStationReader

    observations = GenericStationReader(
        tmp_path / "station.csv", load_config().sources["generic_station"]
    ).read()
    assert len(observations) == 1
    assert observations[0].height_m == pytest.approx(10.0, abs=1e-3)
    assert observations[0].speed_ms == pytest.approx(10.0, abs=1e-3)
    assert observations[0].time_utc.strftime("%H:%M") == "08:16"


def test_generic_station_csv_adapter_reads_wide_height_columns(tmp_path: Path) -> None:
    (tmp_path / "wide_station.csv").write_text(
        "Date_time,Latitude,Longitude,28m WindSpeed,28m WindDirection,56m WindSpeed,56m WindDirection\n"
        "2024-04-03 16:16:00,25.0255,102.3720,3.0,180,4.0,190\n",
        encoding="utf-8",
    )
    from wind3d_ninja.readers.generic_station import GenericStationReader

    observations = GenericStationReader(
        tmp_path / "wide_station.csv", load_config().sources["generic_station"]
    ).read()

    assert [item.height_m for item in observations] == [28.0, 56.0]
    assert [item.speed_ms for item in observations] == [3.0, 4.0]


def test_auxiliary_csv_without_wind_columns_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "windmaster_auxiliary.csv"
    path.write_text(
        "record,laser_status,signal\n1,ok,0.92\n",
        encoding="utf-8",
    )
    from wind3d_ninja.readers.generic_station import GenericStationReader

    observations = GenericStationReader(
        path, load_config().sources["generic_station"]
    ).read()
    assert observations == []


def test_undecodable_auxiliary_text_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "vendor-log.txt"
    path.write_bytes(b"\xff\xfe\x00\x81\x80\x82")
    from wind3d_ninja.readers.generic_station import GenericStationReader

    observations = GenericStationReader(
        path, load_config().sources["generic_station"]
    ).read()

    assert observations == []


def test_generic_station_csv_without_height_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "auxiliary.csv"
    path.write_text(
        "Date_time,Latitude,Longitude,WindSpeed,WindDirection\n"
        "2024-04-03 16:16:00,25.0,102.0,3.0,180\n",
        encoding="utf-8",
    )
    from wind3d_ninja.readers.generic_station import GenericStationReader

    observations = GenericStationReader(
        path, load_config().sources["generic_station"]
    ).read()

    assert observations == []


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


def test_turbulence_runner_uses_dem_only_inputs_and_records_native_product(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "bad_observations.csv").write_text("not,valid,wind,data\n", encoding="utf-8")
    with rasterio.open(
        tmp_path / "terrain.tif",
        "w",
        driver="GTiff",
        width=40,
        height=40,
        count=1,
        dtype="float32",
        crs="EPSG:32648",
        transform=from_origin(230000, 2760000, 200, 200),
        nodata=-9999.0,
    ) as dataset:
        dataset.write(np.full((40, 40), 100.0, dtype=np.float32), 1)

    def fail_if_observations_are_loaded(self, input_dir):
        raise AssertionError("turbulence mode must not load observations")

    monkeypatch.setattr(InputInspector, "load", fail_if_observations_are_loaded)
    result = PipelineRunner(load_config(), FakeWindNinjaRunner()).run(
        tmp_path,
        tmp_path / "output",
        [10],
        [200],
        5.0,
        None,
        True,
        solver_mode="turbulence",
        background_speed_ms=8.0,
        background_direction_deg=270.0,
        background_height_m=20.0,
        simulation_time=datetime(2024, 4, 3, 8, 16, tzinfo=timezone.utc),
    )

    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    config = next((tmp_path / "output" / "windninja").rglob("windninja.cfg")).read_text(encoding="utf-8")
    assert len(result.turbulence_kmz_files) == 1
    assert all(path.is_file() for path in result.turbulence_kmz_files)
    assert not (tmp_path / "output" / "observations.csv").exists()
    assert manifest["solver_mode"] == "turbulence"
    assert any(item["kind"] == "turbulence_kmz_native" for item in manifest["outputs"])
    assert "momentum_flag = true" in config
    assert "turbulence_output_flag = true" in config
    assert "wx_station_filename" not in config
    assert f"elevation_file = {(tmp_path / 'output' / 'dem' / 'terrain.tif').resolve()}" in config


def test_turbulence_kmz_requires_native_fluctuation_layer(tmp_path: Path) -> None:
    source = tmp_path / "ordinary.kmz"
    with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("doc.kml", "<kml><Document><name>Wind field</name></Document></kml>")

    with pytest.raises(RuntimeError, match="native turbulence layer"):
        KmzWriter().write_turbulence(tmp_path / "target.kmz", source)
    with pytest.raises(FileNotFoundError, match="native turbulence KMZ"):
        KmzWriter().write_turbulence(tmp_path / "target.kmz", None)


def test_turbulence_planner_checks_local_dem_against_manual_bounds(tmp_path: Path, monkeypatch) -> None:
    dem = tmp_path / "terrain.tif"
    dem.write_bytes(b"fixture")
    captured: dict[str, object] = {}

    def fake_get_dem(self, candidates, observations, buffer_km, manual_bounds=None):
        captured["candidates"] = list(candidates)
        captured["observations"] = list(observations)
        captured["manual_bounds"] = manual_bounds
        return dem

    monkeypatch.setattr(DemManager, "get_dem", fake_get_dem)
    bounds = GeoBounds(25.0, 25.1, 102.3, 102.4)
    result = PipelinePlanner(load_config()).plan(
        tmp_path,
        [10],
        [200],
        5.0,
        bounds,
        solver_mode="turbulence",
        background_speed_ms=8.0,
        background_direction_deg=270.0,
        background_height_m=20.0,
        simulation_time=datetime(2024, 4, 3, 8, 16, tzinfo=timezone.utc),
    )

    assert result["dem_path"] == dem
    assert captured["observations"] == []
    assert captured["manual_bounds"] == bounds
