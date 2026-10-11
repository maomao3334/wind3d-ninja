from __future__ import annotations

import shutil
import zipfile
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

    def write_turbulence(self, output_path: Path, source_kmz: Path | None) -> Path:
        if source_kmz is None or not source_kmz.is_file():
            raise FileNotFoundError("WindNinja did not produce the required native turbulence KMZ output")
        try:
            with zipfile.ZipFile(source_kmz) as archive:
                kml_names = [name for name in archive.namelist() if name.lower().endswith(".kml")]
                kml_text = "\n".join(
                    archive.read(name).decode("utf-8", errors="replace") for name in kml_names
                )
        except zipfile.BadZipFile as error:
            raise RuntimeError(f"WindNinja turbulence output is not a valid KMZ: {source_kmz}") from error
        if "Column Max Velocity Fluctuations" not in kml_text:
            raise RuntimeError("WindNinja KMZ does not contain the native turbulence layer")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if source_kmz.resolve() != output_path.resolve():
            shutil.copy2(source_kmz, output_path)
        return output_path
