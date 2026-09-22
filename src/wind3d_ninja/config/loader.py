from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from .models import (
    AppConfig,
    DemConfig,
    OutputConfig,
    QualityRule,
    SourceConfig,
    TemperatureConfig,
    WindNinjaConfig,
)
from ..windninja.installer import default_runtime_executable


DEFAULTS: dict[str, Any] = {
    "windninja_exe": str(default_runtime_executable()),
    "sources": {
        "windmaster": {
            "tz_offset_hours": 8,
            "station_prefix": "WM",
            "max_height_m": 200,
            "min_obtain_rate": 0.8,
        },
        "dat": {
            "tz_offset_hours": 8,
            "station_prefix": "DAT",
            "max_height_m": 200,
            "speed_divisor": 100,
        },
        "uav": {
            "tz_offset_hours": 8,
            "station_prefix": "UAV",
            "agl_threshold_drop_m": -4.0,
            "agl_threshold_fix_m": 0.1,
            "max_height_m": 1000,
        },
        "generic_station": {
            "tz_offset_hours": 8,
            "station_prefix": "STA",
            "max_height_m": 1000,
        },
    },
    "quality_rules": {
        # All valid observation heights are passed to WindNinja.  The legacy
        # preferred_heights_m key is retained for config compatibility but is
        # intentionally empty and no longer filters input records.
        "windmaster": {"preferred_heights_m": [], "max_time_offset_seconds": 120},
        "dat": {"preferred_heights_m": [], "max_time_offset_seconds": 120},
    },
    "dem": {"cache_dir": "~/.wind3d-ninja/dem_cache", "source": "srtm"},
    "temperature": {
        "trust_source": False,
        "valid_range_c": [-40, 50],
        "configured_assumption_c": 20.0,
        "cloud_cover_assumption_pct": 0.0,
    },
    "windninja": {
        "default_mesh_resolution_m": 200,
        "default_buffer_km": 10.0,
        "vegetation": "trees",
        "num_threads": 4,
        "diurnal_winds": False,
        "non_neutral_stability": False,
        "alpha_stability": None,
        "input_wind_height_m": None,
        "station_radius_of_influence_km": -1.0,
        "output_buffer_clipping_pct": 0.0,
        "turbulence_output": False,
    },
    "output": {
        "default_output_dir_name": "wind3d_output",
        "netcdf_compression": True,
        "netcdf_naming_format": "Wind3D_{time}_{resolution}m_h{height:03d}m.nc",
        "kmz_colormap": "viridis",
    },
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def _read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Configuration root must be a mapping: {path}")
    return data


def _default_file() -> Path | None:
    configured = os.getenv("WIND3D_NINJA_DEFAULTS")
    if configured:
        return Path(configured).expanduser()
    candidates = [Path(__file__).resolve().parents[3] / "config" / "defaults.yaml"]
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        candidates.insert(0, Path(bundle_root) / "config" / "defaults.yaml")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _resolve_executable(value: str | Path, config_dir: Path) -> Path:
    env_value = os.getenv("WINDNINJA_BIN")
    env_home = os.getenv("WINDNINJA_HOME")
    executable_dir = Path(sys.executable).resolve().parent
    bundled_candidates = [
        root / "windninja" / "bin" / "WindNinja_cli.exe"
        for root in (executable_dir, executable_dir.parent)
    ]
    bundled = next((candidate for candidate in bundled_candidates if candidate.is_file()), None)
    if env_value:
        path = Path(env_value).expanduser()
    elif env_home:
        path = Path(env_home).expanduser() / "bin" / "WindNinja_cli.exe"
    elif bundled is not None:
        path = bundled
    else:
        path = Path(value).expanduser()
    if not path.is_absolute():
        path = (config_dir / path).resolve()
    return path


def load_config(custom_path: Path | None = None) -> AppConfig:
    merged = copy.deepcopy(DEFAULTS)
    default_path = _default_file()
    if default_path and default_path.is_file():
        _merge(merged, _read_yaml(default_path))
    if custom_path is not None:
        custom_path = custom_path.expanduser().resolve()
        _merge(merged, _read_yaml(custom_path))
        config_dir = custom_path.parent
    elif default_path is not None:
        config_dir = default_path.parent.parent
    else:
        config_dir = Path.cwd()

    sources = {name: SourceConfig(**values) for name, values in merged["sources"].items()}
    quality_rules = {
        name: QualityRule(
            preferred_heights_m=tuple(values.get("preferred_heights_m", ())),
            max_time_offset_seconds=int(values.get("max_time_offset_seconds", 0)),
        )
        for name, values in merged.get("quality_rules", {}).items()
    }
    dem_values = merged.get("dem", {})
    dem = DemConfig(
        cache_dir=Path(dem_values.get("cache_dir", DEFAULTS["dem"]["cache_dir"])).expanduser(),
        source=str(dem_values.get("source", "srtm")),
    )
    temperature_values = merged.get("temperature", {})
    temperature = TemperatureConfig(
        trust_source=bool(temperature_values.get("trust_source", False)),
        valid_range_c=tuple(float(v) for v in temperature_values.get("valid_range_c", (-40, 50))),
        configured_assumption_c=float(temperature_values.get("configured_assumption_c", 20.0)),
        cloud_cover_assumption_pct=float(temperature_values.get("cloud_cover_assumption_pct", 0.0)),
    )
    windninja = WindNinjaConfig(**merged.get("windninja", {}))
    output = OutputConfig(**merged.get("output", {}))
    return AppConfig(
        windninja_exe=_resolve_executable(merged["windninja_exe"], config_dir),
        sources=sources,
        quality_rules=quality_rules,
        dem=dem,
        temperature=temperature,
        windninja=windninja,
        output=output,
        raw=merged,
    )
