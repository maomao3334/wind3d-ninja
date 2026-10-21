from __future__ import annotations

import csv
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree
from zipfile import ZipFile

import netCDF4
import numpy as np
import pytest
from pyproj import CRS, Transformer

from wind3d_ninja.config.models import AppConfig
from wind3d_ninja.observations.models import Observation
from wind3d_ninja.pipeline.assimilation import AssimilationBatchRunner
from wind3d_ninja.pipeline.turbulence import TurbulenceBatchRunner


TARGET = datetime(2026, 1, 1, tzinfo=timezone.utc)
CRS_GRID = CRS.from_epsg(32648)
X = np.arange(500000.0, 501001.0, 100.0)
Y = np.arange(3001000.0, 2999999.0, -100.0)
TO_LONLAT = Transformer.from_crs(CRS_GRID, 4326, always_xy=True)


def _observation(station, x, y, u, *, source="fixed_station", height=20, offset=0):
    lon, lat = TO_LONLAT.transform(x, y)
    return Observation(
        source=source, station=station, time_utc=TARGET + timedelta(seconds=offset),
        lat=lat, lon=lon, height_m=height, speed_ms=abs(u),
        direction_deg=270 if u >= 0 else 90, moving=source == "uav",
        metadata={"wind_sigma_ms": 0.1},
    )


def _write_background(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with netCDF4.Dataset(path, "w") as dataset:
        for name, size in (("height", 1), ("lat", len(Y)), ("lon", len(X))):
            dataset.createDimension(name, size)
        dataset.createVariable("x", "f8", ("lon",))[:] = X
        dataset.createVariable("y", "f8", ("lat",))[:] = Y
        dataset.createVariable("crs", "i4").crs_wkt = CRS_GRID.to_wkt()
        for name, value in (("U_wind", 0), ("V_wind", 1)):
            variable = dataset.createVariable(name, "f4", ("height", "lat", "lon"), fill_value=-9999)
            variable.units = "m s-1"
            field = np.full((1, len(Y), len(X)), value, dtype=float)
            field[0, 0, 0] = -9999
            variable[:] = field
        dataset.target_time_utc = TARGET.isoformat()
        dataset.output_height_m = 20
        dataset.mesh_resolution_m = 100
        dataset.solver_mode = "turbulence"


@pytest.fixture
def background_stub(tmp_path, monkeypatch):
    state = SimpleNamespace(
        fixed=[_observation("west", X[2], Y[5], 4), _observation("east", X[8], Y[5], -4)],
        validation=[
            _observation("flight-west", X[2], Y[5], 4, source="uav"),
            _observation("flight-east", X[8], Y[5], -4, source="uav"),
            _observation("flight-high", X[5], Y[5], 1000, source="uav", height=100),
            _observation("flight-outside", X[-1] + 100, Y[5], 1000, source="uav"),
            _observation("flight-late", X[5], Y[5], 1000, source="uav", offset=121),
        ], calls=[],
    )

    def run(_self, input_dir, output_dir, heights, resolutions, buffer_km, manual_bounds, generate_kmz, **options):
        state.calls.append((input_dir, output_dir, options))
        path = output_dir / "nc" / "Wind3D_test.nc"
        _write_background(path)
        report = output_dir / "report.json"
        report.write_text(json.dumps({
            "instrument_policy": {"canopy_height_m": 10},
            "validation_window_seconds": 120, "validation_height_tolerance_m": 1,
            "profile_roughness_m": 0.1, "profile_displacement_height_m": 0,
            "allow_profile_extrapolation": False,
        }), encoding="utf-8")
        return SimpleNamespace(netcdf_files=(path,), kmz_files=(), report_json=report,
                               fixed_observations=state.fixed, validation_observations=state.validation)

    monkeypatch.setattr(TurbulenceBatchRunner, "run", run)
    state.runner = AssimilationBatchRunner(AppConfig(Path("unused.exe"), {}, {}))
    state.run = lambda name="analysis", **options: state.runner.run(
        tmp_path / "input", tmp_path / name, [20], [100], 1, None, True,
        horizontal_length_scale_m=120, **options,
    )
    return state


def _read_fields(path):
    with netCDF4.Dataset(path) as dataset:
        return tuple(np.ma.filled(dataset[name][0], np.nan) for name in ("U_wind", "V_wind"))


def _report(result):
    return json.loads(result.report_json.read_text(encoding="utf-8"))


def test_opposite_station_innovations_are_local_and_uav_is_held_out(background_stub):
    state = background_stub
    result = state.run()
    u, v = _read_fields(result.netcdf_files[0])
    assert result.completed == 1
    assert u[5, 2] > 3.9
    assert u[5, 8] < -3.9
    assert abs(u[5, 5]) < 0.01
    assert np.isnan(u[0, 0]) and np.isnan(v[0, 0])
    report = _report(result)
    case = report["case_reports"][0]
    assert case["wind_constraints"]["used_count"] == 2
    assert sorted(row["observed_u_ms"] for row in case["per_station_wind"]) == pytest.approx([-4, 4])
    assert all("flight" not in row["station_id"] for row in case["per_station_wind"])
    assert report["coupled_to_rans"] is False
    with netCDF4.Dataset(result.netcdf_files[0]) as dataset:
        assert dataset.background_solver_mode == "turbulence"
        assert dataset.assimilation_coupled_to_rans == "false"
        assert "TKE_analysis" not in dataset.variables
        np.testing.assert_allclose(dataset["U_wind_background"][0, 5], 0)
        np.testing.assert_allclose(dataset["V_wind_background"][0, 5], 1)

    state.validation = [replace(item, speed_ms=99, direction_deg=180) for item in state.validation]
    altered = state.run("altered-validation")
    for actual, expected in zip(_read_fields(altered.netcdf_files[0]), (u, v)):
        np.testing.assert_allclose(actual, expected, equal_nan=True)
    assert _report(altered)["mean_abs_speed_error_ms"] != report["mean_abs_speed_error_ms"]


def test_background_and_analysis_metrics_use_identical_heldout_subset(background_stub):
    result = background_stub.run()
    with result.validation_csv.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert {row["validation_station"] for row in rows} == {
        "flight-west", "flight-east", "flight-high", "flight-outside",
    }
    valid = [row for row in rows if row["status"] == "ok"]
    assert len(valid) == 2
    assert all(row["role"] == "held_out_uav" for row in rows)
    report = _report(result)
    for metrics in (report, report["case_reports"][0]):
        background = metrics["background_metrics"]
        assert metrics["valid_validation_rows"] == background["valid_validation_rows"] == 2
        assert metrics["validation_status_counts"] == background["validation_status_counts"] == {
            "ok": 2, "height_mismatch": 1, "outside_or_invalid_grid": 1,
        }
        for prefix, values in (("", metrics), ("background_", background)):
            errors = np.array([float(row[prefix + "speed_error_ms"]) for row in valid])
            assert values["mean_abs_speed_error_ms"] == pytest.approx(np.mean(abs(errors)))
            assert values["speed_bias_ms"] == pytest.approx(np.mean(errors))
            assert values["speed_rmse_ms"] == pytest.approx(np.sqrt(np.mean(errors ** 2)))
        assert background["mean_abs_speed_error_ms"] == pytest.approx(3)
        assert metrics["mean_abs_speed_error_ms"] < 0.1


def _write_station_csv(path, *, include_k):
    fields = ["station_id", "time_utc", "latitude", "longitude", "height_m", "height_reference",
              "u_ms", "v_ms", "wind_sigma_ms"]
    if include_k:
        fields += ["sigma_u_ms", "sigma_v_ms", "sigma_w_ms", "k_sigma_m2s2"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for station, x, u in (("west", X[2], 4), ("east", X[8], -4)):
            lon, lat = TO_LONLAT.transform(x, Y[5])
            row = dict(station_id=station, time_utc=TARGET.isoformat(), latitude=lat, longitude=lon,
                       height_m=30, height_reference="agl", u_ms=u, v_ms=0, wind_sigma_ms=0.1)
            if include_k:
                row.update(sigma_u_ms=1, sigma_v_ms=2, sigma_w_ms=3, k_sigma_m2s2=0.1)
            writer.writerow(row)


@pytest.mark.parametrize("include_k", [True, False])
def test_real_csv_tke_requires_compatible_component_statistics(tmp_path, background_stub, include_k):
    station_csv = tmp_path / "stations.csv"
    _write_station_csv(station_csv, include_k=include_k)
    background_stub.fixed = []
    result = background_stub.run(station_observations=station_csv)
    case = _report(result)["case_reports"][0]
    assert len(case["per_station_wind"]) == 2
    assert any("csv:west" in row["station_id"] for row in case["per_station_wind"])
    assert any("csv:east" in row["station_id"] for row in case["per_station_wind"])
    with netCDF4.Dataset(result.netcdf_files[0]) as dataset:
        assert ("TKE_analysis" in dataset.variables) is include_k
        if include_k:
            k = dataset["TKE_analysis"][0]
            assert k.mask[0, 0]
            np.testing.assert_allclose(k.compressed(), 7)
            assert "not native RANS" in dataset["TKE_analysis"].prior_definition
    turbulence = case["turbulence_analysis"]
    assert turbulence["used_count"] == (2 if include_k else 0)
    if include_k:
        assert turbulence["constant_prior_m2s2"] == pytest.approx(7)
        assert all(row["analysis_at_output_height_m2s2"] == pytest.approx(7)
                   for row in turbulence["per_station"])


def test_analysis_kmz_arrows_show_posterior_mean_wind(background_stub):
    result = background_stub.run()
    u, v = _read_fields(result.netcdf_files[0])
    assert len(result.kmz_files) == 1
    with ZipFile(result.kmz_files[0]) as archive:
        document = ElementTree.fromstring(archive.read("doc.kml"))
    ns = {"k": "http://www.opengis.net/kml/2.2"}
    placemarks = document.findall(".//k:Placemark", ns)
    assert len(placemarks) == int(np.isfinite(u).sum())
    west_index = int(np.isfinite(u).ravel()[:5 * len(X) + 2].sum())
    west = placemarks[west_index]
    speed = np.hypot(u[5, 2], v[5, 2])
    direction = np.degrees(np.arctan2(-u[5, 2], -v[5, 2])) % 360
    assert west.find("k:name", ns).text == f"{speed:.2f} m/s, from {direction:.1f} deg"
    start = west.find("k:LineString/k:coordinates", ns).text.split()[0]
    lon, lat = TO_LONLAT.transform(X[2], Y[5])
    assert start == f"{lon:.8f},{lat:.8f},0"
    assert "mean wind" in document.find(".//k:Document/k:description", ns).text


def test_zero_matching_uav_rows_reports_null_metrics(background_stub):
    background_stub.validation = [
        _observation("flight-high", X[2], Y[5], 4, source="uav", height=100),
        _observation("flight-late", X[2], Y[5], 4, source="uav", offset=121),
    ]
    result = background_stub.run()
    report = _report(result)
    assert result.completed == 1
    for metrics in (report, report["background_metrics"], report["case_reports"][0],
                    report["case_reports"][0]["background_metrics"]):
        assert metrics["valid_validation_rows"] == 0
        assert metrics["mean_abs_speed_error_ms"] is None
        assert metrics["speed_rmse_ms"] is None
        assert metrics["mean_abs_direction_error_deg"] is None


@pytest.mark.parametrize("invalid", ["missing_columns", "uav_source"])
def test_invalid_csv_is_rejected_before_background_solver(tmp_path, background_stub, invalid):
    path = tmp_path / "bad-stations.csv"
    if invalid == "missing_columns":
        path.write_text("station_id,speed_ms\nwest,4\n", encoding="utf-8")
        error = "missing required columns"
    else:
        path.write_text(
            "station_id,time_utc,latitude,longitude,height_m,height_reference,u_ms,v_ms,source\n"
            "west,2026-01-01T00:00:00Z,27,105,20,vegetation_top,4,0,uav\n", encoding="utf-8",
        )
        error = "uav observations cannot"
    with pytest.raises(ValueError, match=error):
        background_stub.run(station_observations=path)
    assert background_stub.calls == []
