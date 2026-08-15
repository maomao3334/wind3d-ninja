from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def file_record(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path.resolve()), "size": path.stat().st_size, "sha256": digest.hexdigest()}


def normalize(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {str(key): normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [normalize(item) for item in value]
    return value


class ManifestBuilder:
    def __init__(self, output_path: Path):
        self.output_path = output_path
        self.data: dict[str, Any] = {
            "schema_version": "1.0",
            "created_utc": datetime.now(timezone.utc),
            "inputs": [],
            "outputs": [],
            "runs": [],
            "errors": [],
        }

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def add_input(self, path: Path, kind: str) -> None:
        value = file_record(path)
        value["kind"] = kind
        self.data["inputs"].append(value)

    def add_output(self, path: Path, kind: str) -> None:
        value = file_record(path)
        value["kind"] = kind
        self.data["outputs"].append(value)

    def add_run(self, value: dict[str, Any]) -> None:
        self.data["runs"].append(value)

    def add_error(self, error: Exception) -> None:
        self.data["errors"].append({"type": type(error).__name__, "message": str(error)})

    def write(self) -> Path:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(
            json.dumps(normalize(self.data), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return self.output_path
