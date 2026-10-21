from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import math
from pyproj import CRS, Transformer

from wind3d_ninja.observations.models import Observation
from wind3d_ninja.pipeline.turbulence import (
    _fixed_observations, _metrics, _sample_grid, _validation_status,
)
from wind3d_ninja.postprocess.netcdf_writer import read_ascii_grid
from wind3d_ninja.windninja.config_writer import WindNinjaConfigWriter


TARGET = datetime(2024, 4, 3, 8, 6, tzinfo=timezone.utc)


def observation(height=10, offset=0):
    return Observation("uav", "sample", TARGET + timedelta(seconds=offset), 25.0, 102.0, height, 3.0, 359.0, moving=True)


def test_validation_rejects_wrong_height_and_time():
    assert _validation_status(observation(20, 120), TARGET, 10, 120, 10) == "matched"
    assert _validation_status(observation(20.1), TARGET, 10, 120, 10) == "height_mismatch"
    assert _validation_status(observation(offset=121), TARGET, 10, 120, 10) == "time_mismatch"
    assert _metrics([{"status": "height_mismatch"}])["mean_abs_speed_error_ms"] is None
    assert _metrics([])["mean_abs_direction_error_deg"] is None


def test_background_excludes_high_negative_moving_and_low_quality_gates():
    def record(height, moving=False, rate=1):
        return SimpleNamespace(source="windmaster", moving=moving, height_m=height,
                               metadata={"raw_quality": {"DataObtainRate": rate}}, record_id=f"gate{height}",
                               time_utc=TARGET, lat=25.0, lon=102.0, wind_speed=3.0,
                               wind_direction_deg=90.0, product="sins_level2_ave01min")
    records = [record(28), record(199), record(5000), record(-28),
               record(56, moving=True), record(85, rate=0.5)]
    selected = _fixed_observations(records, 200, 0.8)
    assert [item.height_m for item in selected] == [28, 199]


@pytest.mark.parametrize("centered", [False, True])
def test_grid_sampling_preserves_north_south_rows(tmp_path: Path, centered: bool):
    key = "center" if centered else "corner"
    lower = 0.5 if centered else 0
    header = f"ncols 2\nnrows 2\nxll{key} {lower}\nyll{key} {lower}\ncellsize 1\nNODATA_value -9999\n"
    speed, direction, projection = [tmp_path / name for name in ("v.asc", "a.asc", "v.prj")]
    speed.write_text(header + "1 2\n3 4\n")
    direction.write_text(header + "90 180\n270 0\n")
    projection.write_text(CRS.from_epsg(4326).to_wkt())
    north = _sample_grid(speed, direction, projection, 1.5, 0.5)
    south = _sample_grid(speed, direction, projection, 0.5, 1.5)
    assert (north.speed_ms, north.direction_deg, north.row, north.column) == (1, 90, 0, 0)
    assert (south.speed_ms, south.direction_deg, south.row, south.column) == (4, 0, 1, 1)
    assert north.spatial_offset_m == 0
    assert _sample_grid(speed, direction, projection, 0.5, -0.01) is None


def test_single_column_ascii_shape(tmp_path: Path):
    path = tmp_path / "single.asc"
    path.write_text("ncols 1\nnrows 3\nxllcorner 0\nyllcorner 0\ncellsize 1\nNODATA_value -9999\n1\n2\n3\n")
    assert read_ascii_grid(path).values.shape == (3, 1)


def test_projected_grid_sampling_reports_true_north_direction(tmp_path):
    transform = Transformer.from_crs(4326, 32648, always_xy=True)
    x, y = transform.transform(102.3716, 25.0256)
    nx, ny = transform.transform(102.3716, 25.025601)
    grid_from_north = math.degrees(math.atan2(nx - x, ny - y))
    header = f"ncols 1\nnrows 1\nxllcenter {x}\nyllcenter {y}\ncellsize 200\nNODATA_value -9999\n"
    speed, direction, projection = [tmp_path / name for name in ("v.asc", "a.asc", "v.prj")]
    speed.write_text(header + "5\n")
    direction.write_text(header + f"{grid_from_north}\n")
    projection.write_text(CRS.from_epsg(32648).to_wkt())
    result = _sample_grid(speed, direction, projection, 25.0256, 102.3716)
    assert result.speed_ms == 5
    assert min(result.direction_deg, 360 - result.direction_deg) < 1e-5


def test_ascii_rejects_wrong_row_shape_with_same_value_count(tmp_path: Path):
    path = tmp_path / "wrong.asc"
    path.write_text("ncols 1\nnrows 4\nxllcorner 0\nyllcorner 0\ncellsize 1\nNODATA_value -9999\n1 2\n3 4\n")
    with pytest.raises(ValueError, match="Grid shape"):
        read_ascii_grid(path)


def test_turbulence_keeps_native_output_when_export_disabled(tmp_path: Path):
    kwargs = dict(turbulence_output=True, input_speed_ms=3, input_direction_deg=90,
                  input_wind_height_m=28)
    writer = WindNinjaConfigWriter()
    args = (tmp_path / "wind.cfg", tmp_path / "dem.tif", tmp_path / "stations.csv",
            tmp_path, TARGET, 10, 200, "grass", 1, False)
    config = writer.write(*args, **kwargs).read_text()
    assert "write_goog_output = true" in config
    assert "initialization_method = domainAverageInitialization" in config
    with pytest.raises(ValueError, match="non-neutral"):
        writer.write(*args, **kwargs, non_neutral_stability=True)
