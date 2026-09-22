"""Make bundled Qt DLL directories explicit before importing PySide6."""
from __future__ import annotations

import os
import sys
from pathlib import Path


root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
qt_dir = root / "PySide6"
directories = [root, qt_dir]
for directory in directories:
    if directory.is_dir():
        os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(str(directory))

plugins = qt_dir / "plugins"
if plugins.is_dir():
    os.environ["QT_PLUGIN_PATH"] = str(plugins)
