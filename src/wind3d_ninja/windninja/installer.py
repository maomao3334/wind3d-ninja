from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


WINDNINJA_VERSION = "3.12.2"
WINDNINJA_DOWNLOAD_URL = (
    "https://research.fs.usda.gov/sites/default/files/2026-03/"
    "firelab-windninja-3.12.2-win64.zip"
)
WINDNINJA_ARCHIVE_SHA256 = "217aadd90fbff5ade25ea3c9422b10122e36fab935b14fcdea1c64f1c01aea66"
WINDNINJA_INSTALLER_NAME = "WindNinja-3.12.2-win64.exe"
WINDNINJA_SOURCE_REPOSITORY = "https://github.com/maomao3334/windninja"
WINDNINJA_UPSTREAM_REPOSITORY = "https://github.com/firelab/windninja"

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class WindNinjaInstallResult:
    version: str
    install_dir: Path
    executable: Path
    downloaded_from: str
    archive_sha256: str
    already_installed: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "install_dir": str(self.install_dir),
            "executable": str(self.executable),
            "downloaded_from": self.downloaded_from,
            "archive_sha256": self.archive_sha256,
            "already_installed": self.already_installed,
        }


def default_runtime_root() -> Path:
    configured = os.getenv("WINDNINJA_HOME")
    if configured:
        return Path(configured).expanduser()
    # Installed distributions place UI/CLI in a subdirectory and WindNinja
    # in the application root. Portable builds may place it beside the EXE.
    executable_dir = Path(sys.executable).resolve().parent
    for root in (executable_dir, executable_dir.parent):
        bundled = root / "windninja"
        if (bundled / "bin" / "WindNinja_cli.exe").is_file():
            return bundled
    return Path.home() / ".wind3d-ninja" / "windninja" / WINDNINJA_VERSION


def default_runtime_executable() -> Path:
    return default_runtime_root() / "bin" / "WindNinja_cli.exe"


def _runtime_root_for_executable(executable: Path) -> Path:
    executable = executable.expanduser().resolve()
    if executable.name.casefold() != "windninja_cli.exe" or executable.parent.name.casefold() != "bin":
        raise ValueError(
            "WindNinja installation target must end with bin/WindNinja_cli.exe; "
            f"received {executable}"
        )
    return executable.parent.parent


def _notify(callback: ProgressCallback | None, message: str) -> None:
    if callback is not None:
        callback(message)


def _download_archive(destination: Path, progress: ProgressCallback | None = None) -> str:
    request = urllib.request.Request(
        WINDNINJA_DOWNLOAD_URL,
        headers={"User-Agent": "wind3d-ninja/1.0.0"},
    )
    digest = hashlib.sha256()
    downloaded = 0
    last_bucket = -1
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as target:
        total = int(response.headers.get("Content-Length") or 0)
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            target.write(chunk)
            digest.update(chunk)
            downloaded += len(chunk)
            if total:
                bucket = min(100, int(downloaded * 100 / total)) // 10 * 10
                if bucket > last_bucket:
                    _notify(progress, f"WindNinja download: {bucket}%")
                    last_bucket = bucket
    return digest.hexdigest()


def _extract_installer(archive: Path, destination: Path) -> Path:
    with zipfile.ZipFile(archive) as package:
        matches = [
            entry
            for entry in package.infolist()
            if Path(entry.filename).name.casefold() == WINDNINJA_INSTALLER_NAME.casefold()
            and not entry.is_dir()
        ]
        if len(matches) != 1:
            raise RuntimeError(
                f"Expected one {WINDNINJA_INSTALLER_NAME} in the WindNinja archive; "
                f"found {len(matches)}"
            )
        installer = destination / WINDNINJA_INSTALLER_NAME
        with package.open(matches[0]) as source, installer.open("wb") as target:
            shutil.copyfileobj(source, target)
    return installer


def _run_installer(
    installer: Path,
    install_dir: Path,
    progress: ProgressCallback | None = None,
) -> None:
    install_dir.mkdir(parents=True, exist_ok=True)
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            [str(installer), "/S", f"/D={install_dir}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
    except OSError as error:
        if getattr(error, "winerror", None) != 740:
            raise
        _notify(progress, "Windows administrator approval is required; accept the UAC prompt.")
        environment = os.environ.copy()
        environment["WIND3D_NINJA_INSTALLER"] = str(installer)
        environment["WIND3D_NINJA_INSTALL_DIR"] = str(install_dir)
        command = (
            "$ErrorActionPreference='Stop'; "
            "$process=Start-Process -FilePath $env:WIND3D_NINJA_INSTALLER "
            "-ArgumentList @('/S',('/D=' + $env:WIND3D_NINJA_INSTALL_DIR)) "
            "-Verb RunAs -WindowStyle Hidden -Wait -PassThru; "
            "exit $process.ExitCode"
        )
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation_flags,
            env=environment,
        )
    if completed.returncode != 0:
        raise RuntimeError(f"WindNinja installer exited with code {completed.returncode}")


def install_windninja(
    executable: Path,
    *,
    force: bool = False,
    progress: ProgressCallback | None = None,
) -> WindNinjaInstallResult:
    expected_executable = executable.expanduser().resolve()
    install_dir = _runtime_root_for_executable(expected_executable)
    if expected_executable.is_file() and not force:
        _notify(progress, f"WindNinja {WINDNINJA_VERSION} is already installed.")
        return WindNinjaInstallResult(
            version=WINDNINJA_VERSION,
            install_dir=install_dir,
            executable=expected_executable,
            downloaded_from=WINDNINJA_DOWNLOAD_URL,
            archive_sha256=WINDNINJA_ARCHIVE_SHA256,
            already_installed=True,
        )
    if platform.system() != "Windows":
        raise RuntimeError("Automatic WindNinja installation is currently supported on Windows only.")

    scratch_parent = install_dir.parent / ".downloads"
    scratch_parent.mkdir(parents=True, exist_ok=True)
    _notify(progress, f"Downloading WindNinja {WINDNINJA_VERSION} from the official FireLab package...")
    with tempfile.TemporaryDirectory(prefix="windninja-", dir=scratch_parent) as temporary:
        temporary_dir = Path(temporary)
        archive = temporary_dir / "windninja.zip"
        archive_sha256 = _download_archive(archive, progress)
        if archive_sha256.casefold() != WINDNINJA_ARCHIVE_SHA256.casefold():
            raise RuntimeError(
                "WindNinja archive checksum mismatch: "
                f"expected {WINDNINJA_ARCHIVE_SHA256}, received {archive_sha256}"
            )
        _notify(progress, "Checksum verified; installing WindNinja silently...")
        installer = _extract_installer(archive, temporary_dir)
        _run_installer(installer, install_dir, progress)

    if not expected_executable.is_file():
        raise FileNotFoundError(
            "WindNinja installation finished but the CLI executable was not found at "
            f"{expected_executable}"
        )
    metadata = {
        "version": WINDNINJA_VERSION,
        "installed_at_utc": datetime.now(timezone.utc).isoformat(),
        "executable": str(expected_executable),
        "download_url": WINDNINJA_DOWNLOAD_URL,
        "archive_sha256": WINDNINJA_ARCHIVE_SHA256,
        "source_repository": WINDNINJA_SOURCE_REPOSITORY,
        "upstream_repository": WINDNINJA_UPSTREAM_REPOSITORY,
    }
    (install_dir / ".wind3d-ninja-runtime.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _notify(progress, f"WindNinja {WINDNINJA_VERSION} installed: {expected_executable}")
    return WindNinjaInstallResult(
        version=WINDNINJA_VERSION,
        install_dir=install_dir,
        executable=expected_executable,
        downloaded_from=WINDNINJA_DOWNLOAD_URL,
        archive_sha256=WINDNINJA_ARCHIVE_SHA256,
        already_installed=False,
    )
