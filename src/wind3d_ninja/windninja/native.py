from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

from .process import external_popen


class NativeRuntime:
    def __init__(self, root: Path | None = None):
        if root is None:
            root = (Path(sys.executable).resolve().parent / "runtime/openfoam8"
                    if getattr(sys, "frozen", False)
                    else Path(__file__).resolve().parents[3] / "runtime/openfoam8")
        self.root = root.expanduser().resolve()
        manifest = self.root / "native_runtime.json"
        if not manifest.is_file():
            raise FileNotFoundError(f"Portable Windows OpenFOAM runtime manifest missing: {manifest}")
        self.manifest = json.loads(manifest.read_text(encoding="utf-8"))
        if self.manifest.get("platform") != "windows-native" or self.manifest.get("openfoam_version") != "8":
            raise ValueError("Coupled mode requires the verified Windows-native OpenFOAM 8 runtime")
        if (self.manifest.get("source_model", {}).get("type") != "stationNudging"
                or self.manifest.get("source_model", {}).get("turbulence_model") != "stationKEpsilon"):
            raise ValueError("Native runtime lacks the implicit stationNudging source model; rebuild the portable runtime")
        self.executables = {key: self._path(value) for key, value in self.manifest["executables"].items()}
        self.boundary_library = self._path(self.manifest["boundary_library"])
        self.etc_dir = self._path(self.manifest["etc_dir"])
        self.project_dir = self._path(self.manifest["project_dir"])
        self.library_dirs = [self._path(value) for value in self.manifest["library_dirs"]]
        for path in (*self.executables.values(), self.boundary_library):
            if not path.is_file():
                raise FileNotFoundError(f"Native runtime file missing: {path}")

    def _path(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        if not candidate.is_relative_to(self.root):
            raise ValueError(f"Runtime manifest path escapes package: {relative}")
        return candidate

    def environment(self) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("FOAM_", "WM_"))}
        system = Path(env.get("SystemRoot", "C:/Windows"))
        paths = list(dict.fromkeys([str(path.parent) for path in self.executables.values()]
                                  + [str(path) for path in self.library_dirs]
                                  + [str(system / "System32"), str(system)]))
        env.update(PATH=os.pathsep.join(paths), WM_PROJECT="OpenFOAM", WM_PROJECT_VERSION="8",
                   WM_PROJECT_DIR=str(self.project_dir), FOAM_ETC=str(self.etc_dir),
                   FOAM_LIBBIN=str(self.library_dirs[0]), FOAM_USER_LIBBIN=str(self.boundary_library.parent),
                   FOAM_APPBIN=str(self.executables["simpleFoam"].parent),
                   WM_OPTIONS=self.manifest.get("wm_options", "mingw_w64GccDPInt32Opt"))
        return env

    def run(self, name: str, args: list[str], case: Path, log_name: str, progress=None) -> Path:
        case.mkdir(parents=True, exist_ok=True)
        log = case / log_name
        command = [str(self.executables[name]), *args]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        fatal = False
        with log.open("w", encoding="utf-8") as handle:
            process = external_popen(command, cwd=case, env=self.environment(),
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                       encoding="utf-8", errors="replace", creationflags=flags)
            try:
                for line in process.stdout:
                    fatal = fatal or "FOAM FATAL" in line or "Floating point exception" in line
                    handle.write(line)
                    handle.flush()
                    if progress and (line.startswith("Time =") or "converged" in line.lower()):
                        progress(line.rstrip())
                code = process.wait()
            except BaseException:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                raise
        if code or fatal:
            raise RuntimeError(f"Native {name} failed with {code}; see {log}")
        return log
