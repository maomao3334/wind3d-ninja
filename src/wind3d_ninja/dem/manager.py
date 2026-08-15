from __future__ import annotations

from contextlib import ExitStack
import gzip
import hashlib
import math
from pathlib import Path
import shutil
import tempfile
from typing import Iterable, Protocol
from urllib.request import Request, urlopen

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.merge import merge
from rasterio.transform import array_bounds
from rasterio.warp import calculate_default_transform, reproject
from pyproj import Transformer

from ..config.models import DemConfig
from ..utils.geometry import GeoBounds, bounds_from_points


class LocatedRecord(Protocol):
    lat: float | None
    lon: float | None


class DemManager:
    def __init__(self, config: DemConfig):
        self.config = config

    def get_dem(
        self,
        scanned_dems: Iterable[Path],
        observations: Iterable[LocatedRecord],
        buffer_km: float,
        manual_bounds: GeoBounds | None = None,
    ) -> Path:
        records = [item for item in observations if item.lat is not None and item.lon is not None]
        required = manual_bounds or bounds_from_points(
            [(float(item.lat), float(item.lon)) for item in records],
            buffer_km,
        )
        cache_dir = self.config.cache_dir.expanduser().resolve()
        cache_dir.mkdir(parents=True, exist_ok=True)
        for candidate in scanned_dems:
            path = Path(candidate).expanduser().resolve()
            if path.is_file() and self.covers(path, required):
                if self.is_projected(path):
                    return path
                stat = path.stat()
                projected_digest = hashlib.sha256(
                    f"{path},{stat.st_size},{stat.st_mtime_ns}".encode()
                ).hexdigest()[:16]
                projected = cache_dir / f"dem_projected_{projected_digest}.tif"
                if not projected.is_file() or not self.covers(projected, required):
                    self._project_dem(path, projected, required)
                return projected
        digest = hashlib.sha256(
            f"{required.min_lat:.6f},{required.max_lat:.6f},{required.min_lon:.6f},{required.max_lon:.6f},{self.config.source}".encode()
        ).hexdigest()[:16]
        output = cache_dir / f"dem_{digest}.tif"
        if output.is_file() and self.covers(output, required) and self.is_projected(output):
            return output
        self.download(required, output)
        if not self.covers(output, required) or not self.is_projected(output):
            raise ValueError(f"Downloaded DEM is not a projected raster covering required bounds: {output}")
        return output

    @staticmethod
    def covers(path: Path, required: GeoBounds) -> bool:
        with rasterio.open(path) as dataset:
            if dataset.crs is None:
                return False
            transformer = Transformer.from_crs("EPSG:4326", dataset.crs, always_xy=True)
            corners = (
                (required.min_lon, required.min_lat),
                (required.min_lon, required.max_lat),
                (required.max_lon, required.min_lat),
                (required.max_lon, required.max_lat),
            )
            projected = [transformer.transform(lon, lat) for lon, lat in corners]
            return all(
                dataset.bounds.left <= x <= dataset.bounds.right
                and dataset.bounds.bottom <= y <= dataset.bounds.top
                for x, y in projected
            )

    @staticmethod
    def covers_points(path: Path, records: Iterable[LocatedRecord]) -> bool:
        points = [
            (float(item.lon), float(item.lat))
            for item in records
            if item.lat is not None and item.lon is not None
        ]
        if not points:
            return False
        with rasterio.open(path) as dataset:
            if dataset.crs is None:
                return False
            transformer = Transformer.from_crs("EPSG:4326", dataset.crs, always_xy=True)
            projected = [transformer.transform(lon, lat) for lon, lat in points]
            return all(
                dataset.bounds.left <= x <= dataset.bounds.right
                and dataset.bounds.bottom <= y <= dataset.bounds.top
                for x, y in projected
            )

    @staticmethod
    def is_projected(path: Path) -> bool:
        with rasterio.open(path) as dataset:
            return bool(dataset.crs and dataset.crs.is_projected)

    def download(self, bounds: GeoBounds, output: Path) -> None:
        if self.config.source.lower() in {"srtm", "srtm1"}:
            self._download_srtm1(bounds, output)
            return
        try:
            import elevation
        except ImportError as error:
            raise RuntimeError(
                "No local DEM covers the observations and the optional elevation package is unavailable"
            ) from error
        elevation.clip(
            bounds=(bounds.min_lon, bounds.min_lat, bounds.max_lon, bounds.max_lat),
            output=str(output),
            product="SRTM1" if self.config.source.lower() == "srtm" else self.config.source,
        )

    @staticmethod
    def _download_srtm1(bounds: GeoBounds, output: Path) -> None:
        """Download and clip SRTM 1-arcsecond tiles without external executables."""
        if not (-60.0 <= bounds.min_lat < bounds.max_lat <= 60.0):
            raise ValueError("SRTM 1 arc-second coverage is limited to latitudes -60 through 60")

        padding = 1.0 / 3600.0
        clip_bounds = (
            bounds.min_lon - padding,
            bounds.min_lat - padding,
            bounds.max_lon + padding,
            bounds.max_lat + padding,
        )
        temporary_dir = Path(tempfile.mkdtemp(prefix="wind3d-ninja-srtm-"))
        try:
            tile_paths: list[Path] = []
            for latitude in range(math.floor(clip_bounds[1]), math.ceil(clip_bounds[3])):
                for longitude in range(math.floor(clip_bounds[0]), math.ceil(clip_bounds[2])):
                    lat_name = f"{'N' if latitude >= 0 else 'S'}{abs(latitude):02d}"
                    lon_name = f"{'E' if longitude >= 0 else 'W'}{abs(longitude):03d}"
                    tile_name = f"{lat_name}{lon_name}"
                    tile_path = temporary_dir / f"{tile_name}.hgt"
                    url = (
                        "https://s3.amazonaws.com/elevation-tiles-prod/skadi/"
                        f"{lat_name}/{tile_name}.hgt.gz"
                    )
                    request = Request(url, headers={"User-Agent": "wind3d-ninja/0.1"})
                    with urlopen(request, timeout=120) as response:
                        with gzip.GzipFile(fileobj=response) as compressed, tile_path.open("wb") as target:
                            shutil.copyfileobj(compressed, target)
                    tile_paths.append(tile_path)

            with ExitStack() as stack:
                datasets = [stack.enter_context(rasterio.open(path)) for path in tile_paths]
                data, transform = merge(datasets, bounds=clip_bounds)
                profile = datasets[0].profile.copy()
                source_bounds = array_bounds(data.shape[1], data.shape[2], transform)
                target_crs = DemManager._utm_crs(bounds)
                target_transform, target_width, target_height = calculate_default_transform(
                    "EPSG:4326",
                    target_crs,
                    data.shape[2],
                    data.shape[1],
                    *source_bounds,
                )
                nodata = profile.get("nodata")
                destination_data = (
                    np.full((data.shape[0], target_height, target_width), nodata, dtype=data.dtype)
                    if nodata is not None
                    else np.empty((data.shape[0], target_height, target_width), dtype=data.dtype)
                )
                for band_index in range(data.shape[0]):
                    reproject(
                        source=data[band_index],
                        destination=destination_data[band_index],
                        src_transform=transform,
                        src_crs="EPSG:4326",
                        dst_transform=target_transform,
                        dst_crs=target_crs,
                        src_nodata=nodata,
                        dst_nodata=nodata,
                        resampling=Resampling.nearest,
                    )
                profile.update(
                    driver="GTiff",
                    count=destination_data.shape[0],
                    height=destination_data.shape[1],
                    width=destination_data.shape[2],
                    transform=target_transform,
                    crs=target_crs,
                    compress="deflate",
                )
                output.parent.mkdir(parents=True, exist_ok=True)
                with rasterio.open(output, "w", **profile) as dataset:
                    dataset.write(destination_data)
        finally:
            shutil.rmtree(temporary_dir, ignore_errors=True)

    @staticmethod
    def _utm_crs(bounds: GeoBounds) -> str:
        longitude = (bounds.min_lon + bounds.max_lon) / 2.0
        latitude = (bounds.min_lat + bounds.max_lat) / 2.0
        zone = max(1, min(60, int((longitude + 180.0) // 6.0) + 1))
        return f"EPSG:{32600 + zone if latitude >= 0.0 else 32700 + zone}"

    @staticmethod
    def _project_dem(source: Path, output: Path, bounds: GeoBounds) -> None:
        target_crs = DemManager._utm_crs(bounds)
        with rasterio.open(source) as source_dataset:
            target_transform, target_width, target_height = calculate_default_transform(
                source_dataset.crs,
                target_crs,
                source_dataset.width,
                source_dataset.height,
                *source_dataset.bounds,
            )
            profile = source_dataset.profile.copy()
            profile.update(
                driver="GTiff",
                crs=target_crs,
                transform=target_transform,
                width=target_width,
                height=target_height,
                compress="deflate",
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            with rasterio.open(output, "w", **profile) as output_dataset:
                for band_index in range(1, source_dataset.count + 1):
                    reproject(
                        source=rasterio.band(source_dataset, band_index),
                        destination=rasterio.band(output_dataset, band_index),
                        src_transform=source_dataset.transform,
                        src_crs=source_dataset.crs,
                        dst_transform=target_transform,
                        dst_crs=target_crs,
                        src_nodata=source_dataset.nodata,
                        dst_nodata=source_dataset.nodata,
                        resampling=Resampling.bilinear,
                    )
