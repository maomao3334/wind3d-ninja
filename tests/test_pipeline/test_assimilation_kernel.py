from __future__ import annotations

import numpy as np
import pytest

from wind3d_ninja.postprocess.assimilation import (
    StationConstraint,
    analyse_scalar,
    analyse_vector,
    apply_observation_stencil,
    load_station_csv,
    observation_stencil,
)


def _axes():
    return np.linspace(0.0, 1000.0, 11), np.linspace(1000.0, 0.0, 11)


def _station(station_id: str, x: float, y: float, u: float = 0.0, v: float = 0.0, **kwargs):
    return StationConstraint(station_id, x, y, 20.0, u, v, **kwargs)


def test_scalar_oi_opposite_innovations_stay_local_and_use_bilinear_h():
    x, y = _axes()
    background = np.zeros((len(y), len(x)))
    constraints = (_station("west", 200, 500), _station("east", 800, 500))
    result = analyse_scalar(
        background, x, y, constraints, values=(4.0, -4.0), observation_sigmas=(0.1, 0.1),
        length_scale_m=120.0, background_sigma=2.0, block_rows=2,
    )
    west = apply_observation_stencil(result.field, observation_stencil(x, y, 200, 500))
    east = apply_observation_stencil(result.field, observation_stencil(x, y, 800, 500))
    midpoint = apply_observation_stencil(result.field, observation_stencil(x, y, 500, 500))
    assert west > 3.9
    assert east < -3.9
    assert abs(midpoint) < 0.1
    assert result.per_station[0].analysis == pytest.approx(west)
    assert result.per_station[1].analysis == pytest.approx(east)


def test_vector_uses_one_covariance_system_and_rejects_heldout_uav():
    x, y = _axes()
    zero = np.zeros((len(y), len(x)))
    fixed = _station("fixed", 500, 500, u=3, v=-2, wind_sigma_ms=0.2)
    heldout = _station("flight", 600, 500, u=-99, v=99, source="uav")
    result = analyse_vector(zero, zero, x, y, (fixed, heldout), length_scale_m=150, background_sigma=2)
    assert result.used_station_ids == ("fixed",)
    assert result.excluded == {"uav_not_assimilation_source": 1}
    assert result.per_station[0].analysis_u_ms == pytest.approx(3, abs=0.1)
    assert result.per_station[0].analysis_v_ms == pytest.approx(-2, abs=0.1)


def test_no_valid_observation_and_nodata_preserve_background():
    x, y = _axes()
    background = np.full((len(y), len(x)), 5.0)
    background[5, 5] = -999.0
    nodata = np.zeros_like(background, dtype=bool)
    nodata[5, 5] = True
    result = analyse_scalar(
        background, x, y, (_station("outside", 1200, 0), _station("masked", 500, 500)),
        values=(1, 1), observation_sigmas=(1, 1), length_scale_m=100, background_sigma=1, nodata=nodata,
    )
    assert np.array_equal(result.field, background)
    assert result.excluded == {"outside_grid": 1, "observation_stencil_touches_nodata": 1}


def test_invalid_error_bar_and_duplicate_constraint_do_not_enter_system():
    x, y = _axes()
    background = np.zeros((len(y), len(x)))
    result = analyse_scalar(
        background, x, y, (_station("station", 100, 100), _station("station", 900, 100)),
        values=(2, -2), observation_sigmas=(0, 0.5), length_scale_m=100, background_sigma=1,
    )
    assert result.summary["used_count"] == 0
    assert result.excluded == {"invalid_observation_sigma": 1, "duplicate_station_constraint": 1}


def test_stencil_handles_single_row_and_exact_boundary():
    stencil = observation_stencil((0.0, 10.0), (5.0,), 10.0, 5.0)
    assert not stencil.outside
    assert np.array_equal(stencil.indices, np.array([1]))
    assert apply_observation_stencil(np.array([[1.0, 7.0]]), stencil) == 7.0


def test_station_csv_requires_explicit_units_calculates_k_without_deriving_wind_sigma(tmp_path):
    path = tmp_path / "stations.csv"
    path.write_text(
        "station_id,time_utc,latitude,longitude,height_m,height_reference,speed_ms,direction_deg,"
        "sigma_u_ms,sigma_v_ms,sigma_w_ms,mean_wind_stddev\n"
        "A,2026-01-01T00:00:00Z,30,120,20,agl,4,90,1,2,3,99\n",
        encoding="utf-8",
    )
    record = load_station_csv(path)[0]
    assert record.u_ms == pytest.approx(-4.0)
    assert record.v_ms == pytest.approx(0.0, abs=1e-12)
    assert record.k_m2s2 == pytest.approx(7.0)
    assert record.wind_sigma_ms == pytest.approx(1.0)

    duplicate = tmp_path / "duplicate.csv"
    duplicate.write_text(path.read_text(encoding="utf-8") +
                         "A,2026-01-01T00:00:00Z,30,120,20,agl,4,90,1,2,3,99\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate station_id/time_utc/height_m"):
        load_station_csv(duplicate)

    unknown_units = tmp_path / "unknown.csv"
    unknown_units.write_text(
        "station_id,time_utc,latitude,longitude,height_m,height_reference,speed,direction_deg\n"
        "A,2026-01-01T00:00:00Z,30,120,20,agl,4,90\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="units must be explicit"):
        load_station_csv(unknown_units)


def test_station_csv_rejects_zero_wind_sigma(tmp_path):
    path = tmp_path / "zero-wind-sigma.csv"
    path.write_text(
        "station_id,time_utc,latitude,longitude,height_m,height_reference,u_ms,v_ms,wind_sigma_ms\n"
        "A,2026-01-01T00:00:00Z,30,120,20,agl,4,0,0\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="wind_sigma_ms must be positive"):
        load_station_csv(path)


def test_station_csv_keeps_fixed_station_coordinates_within_two_metres(tmp_path):
    path = tmp_path / "station-locations.csv"
    path.write_text(
        "station_id,time_utc,latitude,longitude,height_m,height_reference,u_ms,v_ms\n"
        "A,2026-01-01T00:00:00Z,30,120,10,agl,4,0\n"
        "A,2026-01-01T00:00:00Z,30.00001,120,10,vegetation_top,4,0\n"
        "A,2026-01-01T01:00:00Z,30,120,20,agl,4,0\n",
        encoding="utf-8",
    )

    records = load_station_csv(path)
    assert len(records) == 3
    assert {record.height_reference for record in records} == {"agl", "vegetation_top"}

    path.write_text(
        path.read_text(encoding="utf-8")
        + "A,2026-01-01T02:00:00Z,30.00003,120,20,agl,4,0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="location must remain within 2 m; use a new station_id"):
        load_station_csv(path)
