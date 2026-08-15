from datetime import datetime, timezone
from pathlib import Path

import netCDF4 as nc
import numpy as np
from pyproj import CRS

from wind3d_ninja.postprocess.netcdf_writer import Wind3DNetcdfWriter


def write_ascii(path: Path, values: np.ndarray) -> None:
    path.write_text(
        "ncols 2\n"
        "nrows 2\n"
        "xllcorner 230000\n"
        "yllcorner 2760000\n"
        "cellsize 200\n"
        "NODATA_value -9999\n"
        + "\n".join(" ".join(str(value) for value in row) for row in values)
        + "\n",
        encoding="utf-8",
    )


def test_netcdf_writer(tmp_path: Path) -> None:
    speed = tmp_path / "wind_vel.asc"
    direction = tmp_path / "wind_ang.asc"
    projection = tmp_path / "wind_vel.prj"
    output = tmp_path / "wind.nc"
    write_ascii(speed, np.array([[1.0, 2.0], [3.0, 4.0]]))
    write_ascii(direction, np.full((2, 2), 180.0))
    projection.write_text(CRS.from_epsg(32648).to_wkt(), encoding="utf-8")
    Wind3DNetcdfWriter().write(
        speed,
        direction,
        projection,
        output,
        datetime(2024, 4, 3, 8, 16, tzinfo=timezone.utc),
        10,
        200,
    )
    with nc.Dataset(output) as dataset:
        assert dataset.dimensions["height"].size == 1
        assert dataset.dimensions["lat"].size == 2
        assert dataset.dimensions["lon"].size == 2
        assert dataset.variables["lat"].dimensions == ("lat",)
        assert dataset.variables["lon"].dimensions == ("lon",)
        assert dataset.variables["U_wind"].shape == (1, 2, 2)
        assert np.nanmax(np.abs(dataset.variables["U_wind"][0])) < 1e-5
        assert np.all(dataset.variables["V_wind"][0] > 0)
        assert "wind_speed" not in dataset.variables
        assert "wind_from_direction" not in dataset.variables
