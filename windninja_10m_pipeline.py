#!/usr/bin/env python
from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import netCDF4
import numpy as np
from pyproj import CRS, Transformer


FILL_VALUE = np.float32(-9999.0)


@dataclass(frozen=True)
class AsciiGrid:
    path: Path
    values: np.ndarray
    ncols: int
    nrows: int
    xll: float
    yll: float
    cellsize: float
    nodata: float
    x_is_center: bool
    y_is_center: bool

    @property
    def x_centers(self) -> np.ndarray:
        offset = 0.0 if self.x_is_center else 0.5
        return self.xll + (np.arange(self.ncols, dtype=np.float64) + offset) * self.cellsize

    @property
    def y_centers(self) -> np.ndarray:
        offset = 0.0 if self.y_is_center else 0.5
        return self.yll + (self.nrows - np.arange(self.nrows, dtype=np.float64) - offset) * self.cellsize


def _required_float(header: dict[str, float], names: Iterable[str]) -> tuple[float, str]:
    for name in names:
        if name in header:
            return header[name], name
    choices = ", ".join(names)
    raise ValueError(f"ASCII grid header is missing one of: {choices}")


def read_esri_ascii(path: Path) -> AsciiGrid:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        header: dict[str, float] = {}
        for _ in range(6):
            fields = handle.readline().strip().split()
            if len(fields) != 2:
                raise ValueError(f"{path} does not have a valid six-line ESRI ASCII header")
            header[fields[0].lower()] = float(fields[1])
        values = np.loadtxt(handle, dtype=np.float64)

    ncols = int(header["ncols"])
    nrows = int(header["nrows"])
    values = np.atleast_2d(values)
    if values.shape != (nrows, ncols):
        raise ValueError(f"{path} contains {values.shape}, expected {(nrows, ncols)} values")

    xll, x_name = _required_float(header, ("xllcorner", "xllcenter"))
    yll, y_name = _required_float(header, ("yllcorner", "yllcenter"))
    cellsize, _ = _required_float(header, ("cellsize",))
    nodata, _ = _required_float(header, ("nodata_value",))
    return AsciiGrid(
        path=path,
        values=values,
        ncols=ncols,
        nrows=nrows,
        xll=xll,
        yll=yll,
        cellsize=cellsize,
        nodata=nodata,
        x_is_center=x_name == "xllcenter",
        y_is_center=y_name == "yllcenter",
    )


def find_prj(speed_path: Path, direction_path: Path, explicit_path: Path | None) -> Path:
    if explicit_path is not None:
        if not explicit_path.is_file():
            raise FileNotFoundError(f"Projection file does not exist: {explicit_path}")
        return explicit_path
    for candidate in (speed_path.with_suffix(".prj"), direction_path.with_suffix(".prj")):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "No .prj file was found next to the ASCII grids. Pass --prj explicitly; "
        "the projection is required to create geographically correct NetCDF coordinates."
    )


def _validated_wind_arrays(speed: AsciiGrid, direction: AsciiGrid) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    if (speed.ncols, speed.nrows, speed.xll, speed.yll, speed.cellsize) != (
        direction.ncols,
        direction.nrows,
        direction.xll,
        direction.yll,
        direction.cellsize,
    ):
        raise ValueError("Wind speed and direction grids do not have the same geometry")

    wind_speed = speed.values.astype(np.float64, copy=True)
    wind_direction = direction.values.astype(np.float64, copy=True)
    invalid = (
        ~np.isfinite(wind_speed)
        | ~np.isfinite(wind_direction)
        | np.isclose(wind_speed, speed.nodata)
        | np.isclose(wind_direction, direction.nodata)
    )
    wind_speed[invalid] = np.nan
    wind_direction[invalid] = np.nan

    # WindNinja direction is the meteorological "from" direction.
    radians = np.deg2rad(wind_direction)
    eastward = -wind_speed * np.sin(radians)
    northward = -wind_speed * np.cos(radians)
    return wind_speed, wind_direction, eastward, northward


def write_cf_netcdf(
    speed_path: Path,
    direction_path: Path,
    prj_path: Path,
    output_path: Path,
    height_m: float,
    overwrite: bool,
) -> None:
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"Output exists: {output_path}. Pass --overwrite to replace it.")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    speed = read_esri_ascii(speed_path)
    direction = read_esri_ascii(direction_path)
    wind_speed, wind_direction, eastward, northward = _validated_wind_arrays(speed, direction)

    crs = CRS.from_wkt(prj_path.read_text(encoding="utf-8", errors="replace"))
    transformer = Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True)
    x_values = speed.x_centers
    y_values = speed.y_centers
    x_mesh, y_mesh = np.meshgrid(x_values, y_values)
    longitude, latitude = transformer.transform(x_mesh, y_mesh)

    with netCDF4.Dataset(output_path, "w", format="NETCDF4") as dataset:
        dataset.Conventions = "CF-1.10"
        dataset.title = "WindNinja terrain-downscaled wind field at a single height"
        dataset.summary = "WindNinja ASCII output converted without vertical extrapolation."
        dataset.source = "WindNinja CLI ASCII speed and direction grids"
        dataset.windninja_speed_grid = speed_path.name
        dataset.windninja_direction_grid = direction_path.name
        dataset.windninja_projection = prj_path.name
        dataset.windninja_output_height_m = np.float64(height_m)

        dataset.createDimension("height", 1)
        dataset.createDimension("y", speed.nrows)
        dataset.createDimension("x", speed.ncols)

        height = dataset.createVariable("height", "f8", ("height",))
        height.standard_name = "height"
        height.long_name = "height above ground"
        height.units = "m"
        height.positive = "up"
        height.axis = "Z"
        height[:] = [height_m]

        x = dataset.createVariable("x", "f8", ("x",))
        x.standard_name = "projection_x_coordinate"
        x.long_name = "projected x coordinate"
        x.units = "m"
        x.axis = "X"
        x[:] = x_values

        y = dataset.createVariable("y", "f8", ("y",))
        y.standard_name = "projection_y_coordinate"
        y.long_name = "projected y coordinate"
        y.units = "m"
        y.axis = "Y"
        y[:] = y_values

        grid_mapping = dataset.createVariable("crs", "i4")
        for name, value in crs.to_cf().items():
            grid_mapping.setncattr(name, value)
        grid_mapping.spatial_ref = crs.to_wkt()
        grid_mapping.crs_wkt = crs.to_wkt()
        grid_mapping.epsg_code = crs.to_epsg() or "unknown"

        lon = dataset.createVariable("lon", "f8", ("y", "x"), zlib=True, complevel=4)
        lon.standard_name = "longitude"
        lon.long_name = "longitude"
        lon.units = "degrees_east"
        lon[:] = longitude

        lat = dataset.createVariable("lat", "f8", ("y", "x"), zlib=True, complevel=4)
        lat.standard_name = "latitude"
        lat.long_name = "latitude"
        lat.units = "degrees_north"
        lat[:] = latitude

        common = {
            "units": "m s-1",
            "coordinates": "height lat lon",
            "grid_mapping": "crs",
        }
        variable_specs = (
            ("wind_speed", "wind speed", "wind_speed", wind_speed),
            ("wind_from_direction", "wind direction", "wind_from_direction", wind_direction),
            ("eastward_wind", "eastward wind", "eastward_wind", eastward),
            ("northward_wind", "northward wind", "northward_wind", northward),
        )
        for name, long_name, standard_name, values in variable_specs:
            units = "degree" if name == "wind_from_direction" else common["units"]
            variable = dataset.createVariable(
                name,
                "f4",
                ("height", "y", "x"),
                fill_value=FILL_VALUE,
                zlib=True,
                complevel=4,
            )
            variable.long_name = long_name
            variable.standard_name = standard_name
            variable.units = units
            variable.coordinates = common["coordinates"]
            variable.grid_mapping = common["grid_mapping"]
            variable[0, :, :] = np.ma.masked_invalid(values.astype(np.float32))


def _find_new_grid_pair(work_dir: Path, started_at: float) -> tuple[Path, Path]:
    candidates = sorted(
        work_dir.glob("*_vel.asc"),
        key=lambda candidate: candidate.stat().st_mtime,
        reverse=True,
    )
    for speed_path in candidates:
        if speed_path.stat().st_mtime + 1.0 < started_at:
            continue
        direction_path = speed_path.with_name(speed_path.name.replace("_vel.asc", "_ang.asc"))
        if direction_path.is_file():
            return speed_path, direction_path
    raise FileNotFoundError("WindNinja completed but no newly written *_vel.asc/*_ang.asc pair was found")


def run_windninja(args: argparse.Namespace) -> tuple[Path, Path]:
    windninja_bin = Path(args.windninja_bin)
    dem_path = Path(args.dem)
    work_dir = Path(args.work_dir)
    if not windninja_bin.is_file():
        raise FileNotFoundError(f"WindNinja executable does not exist: {windninja_bin}")
    if not dem_path.is_file():
        raise FileNotFoundError(f"DEM does not exist: {dem_path}")
    work_dir.mkdir(parents=True, exist_ok=True)

    command = [
        str(windninja_bin),
        "--elevation_file", str(dem_path),
        "--initialization_method", "domainAverageInitialization",
        "--input_speed", str(args.wind_speed),
        "--input_speed_units", "mps",
        "--input_direction", str(args.wind_direction),
        "--input_wind_height", str(args.height),
        "--units_input_wind_height", "m",
        "--output_speed_units", "mps",
        "--output_wind_height", str(args.height),
        "--units_output_wind_height", "m",
        "--vegetation", args.vegetation,
        "--mesh_resolution", str(args.mesh_resolution),
        "--units_mesh_resolution", "m",
        "--write_ascii_output", "true",
        "--ascii_out_resolution", str(args.mesh_resolution),
        "--units_ascii_out_resolution", "m",
        "--ascii_out_utm", "true",
        "--output_path", str(work_dir),
        "--num_threads", str(args.num_threads),
    ]
    print("Running WindNinja:")
    print(subprocess.list2cmdline(command))
    started_at = __import__("time").time()
    completed = subprocess.run(command, cwd=work_dir, text=True, capture_output=True)
    (work_dir / "windninja.log").write_text(
        completed.stdout + "\n--- stderr ---\n" + completed.stderr,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise RuntimeError(f"WindNinja failed (exit {completed.returncode}); see {work_dir / 'windninja.log'}")
    return _find_new_grid_pair(work_dir, started_at)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run WindNinja at one height or convert an existing WindNinja ASCII pair to CF-NetCDF."
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)

    def add_conversion_options(target: argparse.ArgumentParser) -> None:
        target.add_argument("--output", required=True, help="Output .nc path")
        target.add_argument("--height", type=float, default=10.0, help="WindNinja output height in metres (default: 10)")
        target.add_argument("--overwrite", action="store_true", help="Replace an existing output file")

    convert = subparsers.add_parser("convert", help="Convert existing *_vel.asc and *_ang.asc files")
    convert.add_argument("--speed-asc", required=True, help="WindNinja *_vel.asc file")
    convert.add_argument("--direction-asc", required=True, help="Matching WindNinja *_ang.asc file")
    convert.add_argument("--prj", help="Optional projection .prj path; normally discovered beside the ASC file")
    add_conversion_options(convert)

    run = subparsers.add_parser("run", help="Run WindNinja from an existing DEM, then convert its output")
    run.add_argument("--windninja-bin", required=True, help="Path to WindNinja_cli.exe")
    run.add_argument("--dem", required=True, help="Existing DEM path")
    run.add_argument("--work-dir", required=True, help="Dedicated WindNinja output directory")
    run.add_argument("--wind-speed", required=True, type=float, help="Quality-controlled 10 m wind speed in m/s")
    run.add_argument("--wind-direction", required=True, type=float, help="Meteorological 10 m wind-from direction in degrees")
    run.add_argument("--vegetation", default="trees", choices=("grass", "brush", "trees"))
    run.add_argument("--mesh-resolution", type=float, default=200.0, help="WindNinja mesh and ASCII resolution in metres")
    run.add_argument("--num-threads", type=int, default=2)
    add_conversion_options(run)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_path = Path(args.output)
    try:
        if args.mode == "convert":
            speed_path = Path(args.speed_asc)
            direction_path = Path(args.direction_asc)
            if not speed_path.is_file() or not direction_path.is_file():
                raise FileNotFoundError("Both --speed-asc and --direction-asc must point to existing files")
            prj_path = find_prj(speed_path, direction_path, Path(args.prj) if args.prj else None)
        else:
            speed_path, direction_path = run_windninja(args)
            prj_path = find_prj(speed_path, direction_path, None)

        write_cf_netcdf(speed_path, direction_path, prj_path, output_path, args.height, args.overwrite)
    except (FileNotFoundError, FileExistsError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(f"Created: {output_path}")
    print(f"Source speed grid: {speed_path}")
    print(f"Source direction grid: {direction_path}")
    print(f"Height: {args.height:g} m AGL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
