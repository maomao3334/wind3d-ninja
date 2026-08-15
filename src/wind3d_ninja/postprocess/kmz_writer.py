from __future__ import annotations

import shutil
from pathlib import Path


class KmzWriter:
    def write(self, netcdf_path: Path, output_path: Path, source_kmz: Path | None = None) -> Path:
        _ = netcdf_path
        if source_kmz is None or not source_kmz.is_file():
            raise FileNotFoundError("WindNinja did not produce the requested KMZ output")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if source_kmz.resolve() != output_path.resolve():
            shutil.copy2(source_kmz, output_path)
        return output_path
