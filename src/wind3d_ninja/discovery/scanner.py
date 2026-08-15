from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ScanResult:
    windmaster: tuple[Path, ...]
    dat: tuple[Path, ...]
    uav: tuple[Path, ...]
    dem: tuple[Path, ...]

    def as_dict(self) -> dict[str, list[str]]:
        return {
            "windmaster": [str(path) for path in self.windmaster],
            "dat": [str(path) for path in self.dat],
            "uav": [str(path) for path in self.uav],
            "dem": [str(path) for path in self.dem],
        }


class DataScanner:
    _ignored_dirs = {".git", "wind3d_output", "__pycache__", ".pytest_cache"}

    def __init__(self, input_dir: Path):
        self.input_dir = input_dir.expanduser().resolve()

    def scan(self) -> ScanResult:
        if not self.input_dir.is_dir():
            raise NotADirectoryError(self.input_dir)
        found: dict[str, list[Path]] = {"windmaster": [], "dat": [], "uav": [], "dem": []}
        for path in sorted(self.input_dir.rglob("*")):
            if not path.is_file() or any(part.lower() in self._ignored_dirs for part in path.parts):
                continue
            suffix = path.suffix.lower()
            if suffix == ".zip":
                found["windmaster"].append(path)
            elif suffix == ".dat":
                found["dat"].append(path)
            elif suffix in {".xls", ".xlsx"}:
                found["uav"].append(path)
            elif suffix in {".tif", ".tiff"}:
                found["dem"].append(path)
        return ScanResult(**{key: tuple(value) for key, value in found.items()})
