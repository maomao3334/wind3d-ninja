from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import json
import math

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from wind3d_ninja.observations.instruments import (
    InstrumentCatalog, InstrumentPolicy, RadarHeightReference, RawInstrumentRecord,
    UavHeightReference, WindDirectionReference, WindSpeedUnit, normalize_catalog,
)
from wind3d_ninja.observations.models import Observation
from wind3d_ninja.observations.profiles import build_station_profiles


TIME = datetime(2024, 4, 3, 13, tzinfo=timezone.utc)


def observation(height=10.0, speed=4.0, direction=0.0, lon=120.0, **metadata):
    return Observation(
        source="windmaster", station=f"gate:{height}:{lon}", time_utc=TIME,
        lat=30.0, lon=lon, height_m=height, speed_ms=speed, direction_deg=direction,
        metadata={"product": "profile", **metadata},
    )


def uav_record(**values):
    record = RawInstrumentRecord(
        record_id="uav:test", source="uav", product="uav_export", time_utc=TIME,
        lat=30.0, lon=120.0, wind_speed=4.0, wind_direction_deg=90.0,
        speed_unit=WindSpeedUnit.METERS_PER_SECOND,
        platform_altitude_m=150.0, relative_height_m=20.0,
    )
    return replace(record, **values)


def policy(reference, **values):
    return InstrumentPolicy(
        uav_direction_reference=WindDirectionReference.FROM_TRUE_NORTH,
        uav_speed_unit=WindSpeedUnit.METERS_PER_SECOND, uav_is_geographic_wind=True,
        uav_motion_compensated=True, uav_height_reference=reference, **values,
    )


def normalize(record, dem, selected_policy):
    catalog = InstrumentCatalog((record,), {}, None, {}, {})
    return normalize_catalog(catalog, dem, selected_policy)


@pytest.fixture
def dem(tmp_path: Path):
    path = tmp_path / "terrain.tif"
    with rasterio.open(
        path, "w", driver="GTiff", width=4, height=4, count=1, dtype="float32",
        crs="EPSG:4326", transform=from_origin(119.0, 31.0, 1.0, 1.0),
    ) as dataset:
        terrain = np.full((4, 4), 100.0, dtype=np.float32)
        terrain[0, 0] = 120.0
        dataset.write(terrain, 1)
    return path


def test_direction_wraparound_is_vector_interpolation():
    result = build_station_profiles(
        [observation(10, 4, 350), observation(30, 4, 10)], reference_height_m=20,
    )
    assert result.station_count == 1
    assert min(result.background_direction_deg, 360 - result.background_direction_deg) < 1e-10
    assert result.background_speed_ms == pytest.approx(4 * math.cos(math.radians(10)))
    assert result.stations[0].height_m == 20
    assert result.diagnostics[0]["method"] == "linear_uv_interpolation"


def test_radar_vertical_gate_adds_platform_agl_without_second_pitch_projection(dem):
    record = replace(uav_record(), source="windmaster", gate_position_m=28,
                     platform_altitude_m=None, relative_height_m=None)
    selected = policy(UavHeightReference.UNKNOWN,
                      windmaster_direction_reference=WindDirectionReference.FROM_TRUE_NORTH,
                      windmaster_speed_unit=WindSpeedUnit.METERS_PER_SECOND,
                      windmaster_is_geographic_wind=True, windmaster_motion_compensated=True,
                      windmaster_height_reference=RadarHeightReference.PLATFORM_VERTICAL_OFFSET,
                      windmaster_platform_agl_m=3, canopy_height_m=2)
    result = normalize(record, dem, selected)
    assert not result.blocking_reasons
    assert result.observations[0].height_m == 29
    assert result.observations[0].metadata["height_method"] == "platform_agl_plus_vertical_gate"
    assert result.observations[0].metadata["radar_platform_agl_m"] == 3
    blocked = normalize(record, dem, replace(selected, windmaster_platform_agl_m=None))
    assert "windmaster_missing_platform_altitude_or_agl" in blocked.blocking_reasons


@pytest.mark.parametrize("height", [-1, float("nan"), float("inf")])
def test_radar_platform_agl_must_be_finite_nonnegative(height):
    with pytest.raises(ValueError, match="windmaster_platform_agl_m"):
        InstrumentPolicy(windmaster_platform_agl_m=height)


def test_equal_station_weighting_is_independent_of_gate_count():
    records = [observation(height, 2, 90) for height in (10, 20, 30)]
    records.append(observation(20, 10, 270, lon=120.001))
    result = build_station_profiles(records, reference_height_m=20)
    assert result.station_count == 2
    assert result.background_speed_ms == pytest.approx(4)
    assert result.background_direction_deg == pytest.approx(270)
    assert {station.height_m for station in result.stations} == {20}
    json.dumps(result.as_dict(), allow_nan=False)


def test_known_neutral_log_profile_extrapolates_only_when_requested():
    roughness = 0.2
    records = [observation(h, 1.5 * math.log(h / roughness), 45) for h in (10, 20, 40)]
    blocked = build_station_profiles(records, reference_height_m=60, roughness_m=roughness)
    assert blocked.station_count == 0
    assert blocked.rejected["reference_height_outside_observed_profile"] == 1
    result = build_station_profiles(
        records, reference_height_m=60, roughness_m=roughness, allow_log_extrapolation=True,
    )
    assert result.background_speed_ms == pytest.approx(1.5 * math.log(60 / roughness))
    assert result.background_direction_deg == pytest.approx(45)
    assert result.diagnostics[0]["extrapolated"] is True
    assert result.diagnostics[0]["fit_residual_ms"] == pytest.approx(0, abs=1e-12)


def test_log_profile_uses_above_vegetation_plus_canopy_minus_displacement():
    records = [observation(h, 2 * math.log((h + 10 - 6) / 0.5), 180,
                           canopy_height_m=10) for h in (10, 20)]
    result = build_station_profiles(
        records, reference_height_m=40, roughness_m=0.5,
        displacement_height_m=6, allow_log_extrapolation=True,
    )
    assert result.background_speed_ms == pytest.approx(2 * math.log(44 / 0.5))
    assert result.stations[0].metadata["instrument_agl_m"] == 50


def test_component_interpolation_takes_precedence_over_log_fit():
    records = [observation(10, 2, 90), observation(20, 20, 180)]
    result = build_station_profiles(records, reference_height_m=15, allow_log_extrapolation=True)
    assert result.background_speed_ms == pytest.approx(math.hypot(1, 10))
    assert result.diagnostics[0]["extrapolated"] is False


def test_small_gps_jitter_groups_gates_but_keeps_nearby_sites_separate():
    records = [observation(10, lon=120.0), observation(30, lon=120.00001),
               observation(20, lon=120.0001)]
    result = build_station_profiles(records, reference_height_m=20, location_tolerance_m=2)
    assert result.station_count == 2
    assert sorted(item["gate_count"] for item in result.diagnostics) == [1, 2]


def test_explicit_group_key_and_product_keep_distinct_physical_sites():
    records = [observation(20, physical_site="A"), observation(20, physical_site="B"),
               observation(20, physical_site="A", product="other")]
    result = build_station_profiles(records, reference_height_m=20, group_key="physical_site")
    assert result.station_count == 3


@pytest.mark.parametrize("kwargs", [
    {"reference_height_m": float("nan")}, {"reference_height_m": 0},
    {"reference_height_m": 10, "roughness_m": 0},
    {"reference_height_m": 10, "roughness_m": float("inf")},
    {"reference_height_m": 10, "location_tolerance_m": -1},
])
def test_profile_parameters_require_finite_physical_values(kwargs):
    with pytest.raises(ValueError):
        build_station_profiles([], **kwargs)


def test_extrapolation_rejects_log_domain_and_nonfinite_observations():
    bad = observation(20, speed=float("inf"))
    result = build_station_profiles(
        [observation(0.5), bad], reference_height_m=2, roughness_m=1,
        allow_log_extrapolation=True,
    )
    assert result.station_count == 0
    assert result.rejected["invalid_profile_observation"] == 1
    assert result.rejected["neutral_log_height_not_above_roughness_plane"] == 1


def test_aircraft_gps_amsl_does_not_double_count_relative_height(dem):
    result = normalize(uav_record(), dem, policy(
        UavHeightReference.GPS_ALTITUDE_MINUS_DEM, canopy_height_m=2,
    ))
    record = result.observations[0]
    assert record.height_m == 48
    assert record.metadata["instrument_agl_m"] == 50
    assert record.metadata["raw_gps_altitude_m"] == 150
    assert record.metadata["raw_relative_height_m"] == 20
    assert record.metadata["terrain_altitude_m"] == 100


def test_relative_to_launch_uses_local_terrain_and_launch_ground_altitude(dem):
    result = normalize(uav_record(), dem, policy(
        UavHeightReference.RELATIVE_TO_LAUNCH, uav_launch_ground_altitude_m=120,
        canopy_height_m=3,
    ))
    assert result.observations[0].height_m == 37
    assert result.observations[0].metadata["launch_ground_altitude_m"] == 120


def test_relative_to_launch_can_sample_confirmed_launch_position(dem):
    result = normalize(uav_record(), dem, policy(
        UavHeightReference.RELATIVE_TO_LAUNCH,
        uav_launch_lat=30.5, uav_launch_lon=119.5, uav_launch_location_confirmed=True,
    ))
    assert result.observations[0].height_m == 40
    assert result.observations[0].metadata["launch_ground_altitude_m"] == 120


def test_unknown_launch_and_legacy_altitude_semantics_block_normalization(dem):
    unknown = normalize(uav_record(), dem, policy(UavHeightReference.UNKNOWN))
    assert "uav_height_reference_unknown" in unknown.blocking_reasons
    no_launch = normalize(uav_record(), dem, policy(UavHeightReference.RELATIVE_TO_LAUNCH))
    assert "uav_launch_ground_altitude_or_confirmed_location_required" in no_launch.blocking_reasons
    legacy = normalize(uav_record(), dem, policy(UavHeightReference.GPS_PLUS_RELATIVE_MINUS_DEM))
    assert "uav_legacy_gps_is_launch_altitude_unconfirmed" in legacy.blocking_reasons
    confirmed = normalize(uav_record(), dem, policy(
        UavHeightReference.GPS_PLUS_RELATIVE_MINUS_DEM,
        uav_legacy_gps_is_launch_altitude_confirmed=True,
    ))
    assert confirmed.observations[0].height_m == 70


@pytest.mark.parametrize("reference", [
    UavHeightReference.GPS_ALTITUDE_MINUS_DEM, UavHeightReference.RELATIVE_AGL,
])
def test_dem_outside_extent_cannot_silently_sample_zero(dem, reference):
    result = normalize(uav_record(lon=125), dem, policy(
        reference, uav_relative_agl_confirmed=True,
    ))
    assert not result.observations
    assert result.rejected["uav_invalid_dem_sample"] == 1


def test_direction_to_is_really_converted_to_from_and_raw_value_is_preserved(dem):
    selected_policy = replace(
        policy(UavHeightReference.GPS_ALTITUDE_MINUS_DEM),
        uav_direction_reference=WindDirectionReference.TO_TRUE_NORTH,
    )
    result = normalize(uav_record(), dem, selected_policy)
    assert result.observations[0].direction_deg == 270
    assert result.observations[0].metadata["original_wind_direction_deg"] == 90
    assert result.observations[0].metadata["wind_direction_reference"] == "from_true_north"


@pytest.mark.parametrize("canopy", [float("nan"), float("inf"), -1])
def test_canopy_must_be_finite_and_nonnegative(canopy):
    with pytest.raises(ValueError):
        InstrumentPolicy(canopy_height_m=canopy)


@pytest.mark.parametrize("field,value", [
    ("platform_altitude_m", float("nan")), ("wind_speed", float("inf")),
    ("relative_height_m", float("nan")),
])
def test_invalid_measurements_do_not_reach_solver(dem, field, value):
    reference = (UavHeightReference.RELATIVE_TO_LAUNCH if field == "relative_height_m"
                 else UavHeightReference.GPS_ALTITUDE_MINUS_DEM)
    result = normalize(uav_record(**{field: value}), dem, policy(
        reference, uav_launch_ground_altitude_m=120,
    ))
    assert not result.observations


def test_windmaster_to_direction_and_platform_gate_remain_supported(dem):
    record = replace(
        uav_record(), source="windmaster", gate_position_m=28,
        wind_direction_deg=350, relative_height_m=None,
    )
    selected_policy = InstrumentPolicy(
        windmaster_direction_reference=WindDirectionReference.TO_TRUE_NORTH,
        windmaster_speed_unit=WindSpeedUnit.METERS_PER_SECOND,
        windmaster_height_reference=RadarHeightReference.PLATFORM_VERTICAL_OFFSET,
        windmaster_is_geographic_wind=True, windmaster_motion_compensated=True,
    )
    result = normalize(record, dem, selected_policy)
    assert result.observations[0].height_m == 78
    assert result.observations[0].direction_deg == 170


def test_irrelevant_nonfinite_relative_height_is_also_rejected(dem):
    result = normalize(uav_record(relative_height_m=float("nan")), dem, policy(
        UavHeightReference.GPS_ALTITUDE_MINUS_DEM,
    ))
    assert not result.observations
    assert result.rejected["uav_invalid_height_fields"] == 1


def test_opposing_station_vectors_cancel_without_mean_speed_bias():
    result = build_station_profiles(
        [observation(20, 5, 90), observation(20, 5, 270, lon=120.001)],
        reference_height_m=20,
    )
    assert result.background_speed_ms == 0
    assert result.background_direction_deg == 0


@pytest.mark.parametrize("sampled_value", [100.0, None])
def test_dem_coordinate_cache_includes_missing_samples_and_is_local(monkeypatch, sampled_value):
    from wind3d_ninja.observations import instruments

    calls = []

    def sample(path, lat, lon):
        calls.append((lat, lon))
        return sampled_value

    monkeypatch.setattr(instruments, "_sample_dem", sample)
    catalog = InstrumentCatalog((uav_record(), uav_record(record_id="second")), {}, None, {}, {})
    selected_policy = policy(UavHeightReference.RELATIVE_TO_LAUNCH,
                             uav_launch_lat=30.0, uav_launch_lon=120.0,
                             uav_launch_location_confirmed=True)
    first = normalize_catalog(catalog, Path("unused.tif"), selected_policy)
    assert calls == [(30.0, 120.0)]
    assert len(first.observations) == (2 if sampled_value is not None else 0)
    second = normalize_catalog(catalog, Path("unused.tif"), selected_policy)
    assert calls == [(30.0, 120.0), (30.0, 120.0)]
    assert first == second
