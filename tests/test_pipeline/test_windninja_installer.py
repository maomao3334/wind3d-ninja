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

    assert installer.default_runtime_executable() == tmp_path / "bin" / "WindNinja_cli.exe"
    assert load_config().windninja_exe == tmp_path / "bin" / "WindNinja_cli.exe"


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
