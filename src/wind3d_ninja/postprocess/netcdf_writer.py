from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import netCDF4 as nc
import numpy as np
from pyproj import CRS, Transformer


FILL_VALUE = np.float32(-9999.0)


@dataclass(frozen=True)
class AsciiGrid:
    values: np.ndarray
    ncols: int
    nrows: int
    xll: float
    yll: float
    cellsize: float
    nodata: float
    x_centered: bool
    y_centered: bool

    @property
    def x(self) -> np.ndarray:
        offset = 0.0 if self.x_centered else 0.5
        return self.xll + (np.arange(self.ncols, dtype=np.float64) + offset) * self.cellsize

    @property
    def y(self) -> np.ndarray:
        offset = 1.0 if self.y_centered else 0.5
        return self.yll + (self.nrows - np.arange(self.nrows, dtype=np.float64) - offset) * self.cellsize


def make_netcdf_filename(target_time: datetime, resolution_m: int, height_m: int) -> str:
    if resolution_m <= 0 or height_m <= 0:
        raise ValueError("Resolution and height must be positive integers")
    value = target_time.astimezone(timezone.utc)
    return f"Wind3D_{value:%Y%m%dT%H%M%SZ}_{resolution_m}m_h{height_m:03d}m.nc"


def read_ascii_grid(path: Path) -> AsciiGrid:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        header: dict[str, float] = {}
        for _ in range(6):
            fields = handle.readline().strip().split()
            if len(fields) != 2:
                raise ValueError(f"Invalid ESRI ASCII header: {path}")
            header[fields[0].lower()] = float(fields[1])
        values = np.loadtxt(handle, dtype=np.float64)
    ncols = int(header["ncols"])
    nrows = int(header["nrows"])
    values = np.atleast_2d(values)
    if values.shape != (nrows, ncols):
        raise ValueError(f"Grid shape {values.shape} does not match {(nrows, ncols)}: {path}")
    x_key = "xllcenter" if "xllcenter" in header else "xllcorner"
    y_key = "yllcenter" if "yllcenter" in header else "yllcorner"
    if x_key not in header or y_key not in header:
        raise ValueError(f"ASCII grid lacks lower-left coordinates: {path}")
    return AsciiGrid(
        values=values,
        ncols=ncols,
        nrows=nrows,
        xll=header[x_key],
        yll=header[y_key],
        cellsize=header["cellsize"],
        nodata=header.get("nodata_value", -9999.0),
        x_centered=x_key == "xllcenter",
        y_centered=y_key == "yllcenter",
    )


class Wind3DNetcdfWriter:
    def __init__(self, compression: bool = True):
        self.compression = compression

    def write(
        self,
        vel_asc: Path,
        ang_asc: Path,
        projection: Path,
        output_nc: Path,
        target_time: datetime,
        output_height_m: int,
        mesh_resolution_m: int,
    ) -> Path:
        speed = read_ascii_grid(vel_asc)
        direction = read_ascii_grid(ang_asc)
        speed_geometry = (speed.ncols, speed.nrows, speed.xll, speed.yll, speed.cellsize)
        direction_geometry = (
            direction.ncols,
            direction.nrows,
            direction.xll,
            direction.yll,
            direction.cellsize,
        )
        if speed_geometry != direction_geometry:
            raise ValueError("Wind speed and direction grids have different geometry")
        wind_speed = speed.values.astype(np.float64, copy=True)
        wind_direction = direction.values.astype(np.float64, copy=True)
        invalid = (
            ~np.isfinite(wind_speed)
            | ~np.isfinite(wind_direction)
            | np.isclose(wind_speed, speed.nodata)
            | np.isclose(wind_direction, direction.nodata)
            | (wind_speed < 0)
        )
        wind_speed[invalid] = np.nan
        wind_direction[invalid] = np.nan
        radians = np.deg2rad(wind_direction)
        u_wind = -wind_speed * np.sin(radians)
        v_wind = -wind_speed * np.cos(radians)
        crs = CRS.from_wkt(projection.read_text(encoding="utf-8", errors="replace"))
        transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
        # The WindNinja grid is rectilinear in projected x/y coordinates.  Keep
        # those exact axes and expose 1-D geographic axes in the same shape as
        # the Wind3D reference format, using the grid centreline for the
        # projected-to-geographic conversion.
        center_x = float(speed.x[len(speed.x) // 2])
        center_y = float(speed.y[len(speed.y) // 2])
        longitude, _ = transformer.transform(
            speed.x,
            np.full(speed.ncols, center_y, dtype=np.float64),
        )
        _, latitude = transformer.transform(
            np.full(speed.nrows, center_x, dtype=np.float64),
            speed.y,
        )
        longitude = np.asarray(longitude, dtype=np.float64)
        latitude = np.asarray(latitude, dtype=np.float64)
        output_nc.parent.mkdir(parents=True, exist_ok=True)
        if output_nc.exists():
            output_nc.unlink()
        compression = {"zlib": True, "complevel": 4} if self.compression else {}
        with nc.Dataset(output_nc, "w", format="NETCDF4") as dataset:
            dataset.Conventions = "CF-1.10"
            dataset.title = "WindNinja terrain-downscaled wind field"
            dataset.source = "WindNinja point initialization"
            dataset.target_time_utc = target_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            dataset.output_height_m = float(output_height_m)
            dataset.mesh_resolution_m = float(mesh_resolution_m)
            dataset.windninja_speed_grid = vel_asc.name
            dataset.windninja_direction_grid = ang_asc.name
            dataset.createDimension("height", 1)
            dataset.createDimension("lat", speed.nrows)
            dataset.createDimension("lon", speed.ncols)
            height = dataset.createVariable("height", "f4", ("height",))
            height.standard_name = "height"
            height.long_name = "height above ground level"
            height.units = "m"
            height.positive = "up"
            height[:] = [output_height_m]
            x_var = dataset.createVariable("x", "f8", ("lon",))
            x_var.standard_name = "projection_x_coordinate"
            x_var.units = "m"
            x_var[:] = speed.x
            y_var = dataset.createVariable("y", "f8", ("lat",))
            y_var.standard_name = "projection_y_coordinate"
            y_var.units = "m"
            y_var[:] = speed.y
            lon_var = dataset.createVariable("lon", "f8", ("lon",), **compression)
            lon_var.standard_name = "longitude"
            lon_var.units = "degrees_east"
            lon_var.axis = "X"
            lon_var[:] = longitude
            lat_var = dataset.createVariable("lat", "f8", ("lat",), **compression)
            lat_var.standard_name = "latitude"
            lat_var.units = "degrees_north"
            lat_var.axis = "Y"
            lat_var[:] = latitude
            crs_var = dataset.createVariable("crs", "i4")
            for name, value in crs.to_cf().items():
                crs_var.setncattr(name, value)
            crs_var.spatial_ref = crs.to_wkt()
            crs_var.crs_wkt = crs.to_wkt()
            arrays = (
                ("U_wind", "eastward_wind", "m s-1", u_wind),
                ("V_wind", "northward_wind", "m s-1", v_wind),
            )
            for name, standard_name, units, values in arrays:
                variable = dataset.createVariable(
                    name,
                    "f4",
                    ("height", "lat", "lon"),
                    fill_value=FILL_VALUE,
                    **compression,
                )
                variable.standard_name = standard_name
                variable.units = units
                variable.coordinates = "lat lon"
                variable.grid_mapping = "crs"
                variable[0, :, :] = np.ma.masked_invalid(values.astype(np.float32))
        return output_nc
