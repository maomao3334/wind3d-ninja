from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

class StationListWriter:
    def write(
        self,
        station_files: Iterable[Path],
        output_path: Path,
    ) -> Path:
        values = list(station_files)
        if not values:
            raise ValueError("Cannot write an empty WindNinja station list")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Recent_Station_File_List", ""])
            for path in values:
                writer.writerow([path.resolve().relative_to(output_path.parent.resolve())])
        return output_path
