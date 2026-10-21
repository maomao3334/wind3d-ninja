from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace

from wind3d_ninja.config.loader import load_config
from wind3d_ninja.windninja import installer


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes):
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


def test_default_runtime_executable_respects_windninja_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("WINDNINJA_HOME", str(tmp_path))
    executable = tmp_path / "bin" / "WindNinja_cli.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fixture-cli")

    assert installer.default_runtime_executable() == tmp_path / "bin" / "WindNinja_cli.exe"
    assert load_config().windninja_exe == tmp_path / "bin" / "WindNinja_cli.exe"


def test_invalid_machine_path_falls_back_to_bundled_runtime(tmp_path: Path, monkeypatch) -> None:
    install_root = tmp_path / "portable"
    executable = install_root / "ui" / "Wind3D-Ninja.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fixture-ui")
    bundled = install_root / "windninja" / "bin" / "WindNinja_cli.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"fixture-cli")

    monkeypatch.setattr(installer.sys, "executable", str(executable))
    monkeypatch.setenv("WINDNINJA_BIN", str(tmp_path / "old-machine" / "WindNinja_cli.exe"))
    monkeypatch.setenv("WINDNINJA_HOME", str(tmp_path / "old-machine"))

    assert load_config().windninja_exe == bundled


def test_bundled_runtime_root_is_relative_to_current_executable(tmp_path: Path, monkeypatch) -> None:
    install_root = tmp_path / "portable"
    executable = install_root / "cli" / "wind3d-ninja.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fixture-cli")
    bundled = install_root / "windninja" / "bin" / "WindNinja_cli.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"fixture-windninja")

    monkeypatch.setattr(installer.sys, "executable", str(executable))

    assert installer.bundled_runtime_executable() == bundled
    assert installer.default_runtime_root() == install_root / "windninja"
def test_frozen_runtime_discovery_follows_relocated_executable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("WINDNINJA_HOME", raising=False)
    monkeypatch.setattr(installer.sys, "frozen", True, raising=False)
    for directory in (tmp_path / "original", tmp_path / "relocated app"):
        monkeypatch.setattr(installer.sys, "executable", str(directory / "Wind3D.exe"))
        assert installer.default_runtime_executable() == (
            directory / "runtime" / "windninja" / installer.WINDNINJA_VERSION / "bin" / "WindNinja_cli.exe"
        )


def test_environment_override_precedes_frozen_runtime(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(installer.sys, "frozen", True, raising=False)
    monkeypatch.setattr(installer.sys, "executable", str(tmp_path / "app" / "Wind3D.exe"))
    monkeypatch.setenv("WINDNINJA_HOME", str(tmp_path / "custom runtime"))
    assert installer.default_runtime_root() == tmp_path / "custom runtime"


def test_unfrozen_runtime_stays_in_user_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("WINDNINJA_HOME", raising=False)
    monkeypatch.setattr(installer.sys, "frozen", False, raising=False)
    monkeypatch.setattr(installer.Path, "home", lambda: tmp_path)
    assert installer.default_runtime_root() == tmp_path / ".wind3d-ninja" / "windninja" / installer.WINDNINJA_VERSION


def test_install_windninja_downloads_verifies_and_installs(tmp_path: Path, monkeypatch) -> None:
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w") as package:
        package.writestr(installer.WINDNINJA_INSTALLER_NAME, b"fixture-installer")
    archive = archive_buffer.getvalue()

    monkeypatch.setattr(installer.platform, "system", lambda: "Windows")
    monkeypatch.setattr(installer.urllib.request, "urlopen", lambda *args, **kwargs: FakeResponse(archive))
    monkeypatch.setattr(installer, "WINDNINJA_ARCHIVE_SHA256", hashlib.sha256(archive).hexdigest())

    def fake_run(command, **kwargs):
        install_dir = Path(command[-1].removeprefix("/D="))
        executable = install_dir / "bin" / "WindNinja_cli.exe"
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_bytes(b"fixture-cli")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    executable = tmp_path / "runtime" / "bin" / "WindNinja_cli.exe"
    messages: list[str] = []

    result = installer.install_windninja(executable, progress=messages.append)

    assert result.executable == executable.resolve()
    assert result.already_installed is False
    assert executable.is_file()
    assert (tmp_path / "runtime" / ".wind3d-ninja-runtime.json").is_file()
    assert any("Checksum verified" in message for message in messages)


def test_run_installer_requests_uac_when_windows_requires_elevation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            error = OSError("elevation required")
            error.winerror = 740
            raise error
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    messages: list[str] = []

    installer._run_installer(tmp_path / "installer.exe", tmp_path / "runtime", messages.append)

    assert calls[0][1:] == ["/S", f"/D={tmp_path / 'runtime'}"]
    assert calls[1][0] == "powershell.exe"
    assert any("UAC" in message for message in messages)


def test_stale_machine_override_uses_unified_runtime(tmp_path: Path, monkeypatch) -> None:
    executable = tmp_path / "app" / "Wind3D.exe"
    bundled = executable.parent / "runtime" / "windninja" / installer.WINDNINJA_VERSION / "bin" / "WindNinja_cli.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"fixture-cli")
    monkeypatch.setattr(installer.sys, "executable", str(executable))
    monkeypatch.setenv("WINDNINJA_BIN", str(tmp_path / "old-machine" / "WindNinja_cli.exe"))
    monkeypatch.setenv("WINDNINJA_HOME", str(tmp_path / "old-machine"))
    assert load_config().windninja_exe == bundled
