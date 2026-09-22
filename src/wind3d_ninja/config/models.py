from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SourceConfig:
    tz_offset_hours: float = 8.0
    station_prefix: str = "OBS"
    agl_threshold_drop_m: float = -4.0
    agl_threshold_fix_m: float = 0.1
    max_height_m: float | None = 200.0
    min_obtain_rate: float = 0.0
    speed_divisor: float = 1.0


@dataclass(frozen=True)
class QualityRule:
    preferred_heights_m: tuple[float, ...] = ()
    max_time_offset_seconds: int = 0


@dataclass(frozen=True)
class DemConfig:
    cache_dir: Path = Path.home() / ".wind3d-ninja" / "dem_cache"
    source: str = "srtm"


@dataclass(frozen=True)
class TemperatureConfig:
    trust_source: bool = False
    valid_range_c: tuple[float, float] = (-40.0, 50.0)
    configured_assumption_c: float = 20.0
    cloud_cover_assumption_pct: float = 0.0


@dataclass(frozen=True)
class WindNinjaConfig:
    default_mesh_resolution_m: int = 200
    default_buffer_km: float = 10.0
    vegetation: str = "trees"
    num_threads: int = 4
    diurnal_winds: bool = False
    non_neutral_stability: bool = False
    alpha_stability: float | None = None
    input_wind_height_m: float | None = None
    station_radius_of_influence_km: float = -1.0
    output_buffer_clipping_pct: float = 0.0
    turbulence_output: bool = False


@dataclass(frozen=True)
class OutputConfig:
    default_output_dir_name: str = "wind3d_output"
    netcdf_compression: bool = True
    netcdf_naming_format: str = "Wind3D_{time}_{resolution}m_h{height:03d}m.nc"
    kmz_colormap: str = "viridis"


@dataclass(frozen=True)
class AppConfig:
    windninja_exe: Path
    sources: dict[str, SourceConfig]
    quality_rules: dict[str, QualityRule]
    dem: DemConfig = field(default_factory=DemConfig)
    temperature: TemperatureConfig = field(default_factory=TemperatureConfig)
    windninja: WindNinjaConfig = field(default_factory=WindNinjaConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)
