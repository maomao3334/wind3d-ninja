from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from ..config.models import AppConfig
from ..windninja.installer import WINDNINJA_VERSION


def doctor_report(config: AppConfig) -> dict[str, object]:
    dependencies = {
        name: importlib.util.find_spec(name) is not None
        for name in ("click", "netCDF4", "numpy", "pandas", "pyproj", "rasterio", "yaml")
    }
    executable = config.windninja_exe.expanduser().resolve()
    checks = {
        "python": sys.version_info >= (3, 10),
        "dependencies": dependencies,
        "windninja_executable": executable.is_file(),
    }
    healthy = bool(checks["python"]) and all(dependencies.values()) and bool(checks["windninja_executable"])
    return {
        "healthy": healthy,
        "python_version": sys.version.split()[0],
        "required_windninja_version": WINDNINJA_VERSION,
        "windninja_executable": str(executable),
        "checks": checks,
        "install_command": "wind3d-ninja doctor --install",
        "fixture_mode_requires_auth": False,
    }
