from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WindNinjaResult:
    vel_asc: Path
    ang_asc: Path
    projection: Path
    kmz: Path | None
    config_file: Path
    log_file: Path
    command: tuple[str, ...]


class WindNinjaRunner:
    def __init__(self, executable: Path):
        self.executable = executable.expanduser().resolve()

    def run(self, config_file: Path, work_dir: Path) -> WindNinjaResult:
        if not self.executable.is_file():
            raise FileNotFoundError(
                f"WindNinja executable not found: {self.executable}. "
                "Run 'wind3d-ninja doctor --install' first."
            )
        work_dir.mkdir(parents=True, exist_ok=True)
        if self.executable.suffix.lower() == ".py":
            command = (sys.executable, str(self.executable), str(config_file.resolve()))
        else:
            command = (str(self.executable), str(config_file.resolve()))
        log_file = work_dir / "windninja.log"
        process = subprocess.Popen(
            command,
            cwd=work_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        try:
            with log_file.open("w", encoding="utf-8") as log:
                if process.stdout is not None:
                    for line in process.stdout:
                        log.write(line)
                        log.flush()
                        print(f"[WindNinja] {line.rstrip()}", file=sys.stderr, flush=True)
            returncode = process.wait()
        except KeyboardInterrupt:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            raise
        if returncode != 0:
            raise RuntimeError(f"WindNinja exited with {returncode}; see {log_file}")
        vel_candidates = sorted(work_dir.rglob("*_vel.asc"), key=lambda path: path.stat().st_mtime)
        for vel_asc in reversed(vel_candidates):
            ang_asc = vel_asc.with_name(vel_asc.name.replace("_vel.asc", "_ang.asc"))
            if not ang_asc.is_file():
                continue
            projection = vel_asc.with_suffix(".prj")
            if not projection.is_file():
                projection = ang_asc.with_suffix(".prj")
            if not projection.is_file():
                raise FileNotFoundError(f"No projection file for {vel_asc}")
            kmz_candidates = sorted(work_dir.rglob("*.kmz"), key=lambda path: path.stat().st_mtime)
            return WindNinjaResult(
                vel_asc=vel_asc,
                ang_asc=ang_asc,
                projection=projection,
                kmz=kmz_candidates[-1] if kmz_candidates else None,
                config_file=config_file,
                log_file=log_file,
                command=command,
            )
        raise FileNotFoundError(f"WindNinja produced no matching ASCII wind grids in {work_dir}")
