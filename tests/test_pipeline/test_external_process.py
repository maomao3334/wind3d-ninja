from types import SimpleNamespace

import pytest

from wind3d_ninja.windninja import process


@pytest.fixture
def frozen_windows(monkeypatch):
    state = SimpleNamespace(directory=r"C:\bundle\_internal", calls=[])

    class DllApi:
        def GetDllDirectoryW(self, capacity, buffer):
            if not capacity:
                return len(state.directory) + 1 if state.directory else 0
            buffer.value = state.directory or ""
            return len(buffer.value)

        def SetDllDirectoryW(self, directory):
            state.calls.append(directory)
            state.directory = directory
            return True

    monkeypatch.setattr(process.sys, "frozen", True, raising=False)
    monkeypatch.setattr(process.sys, "platform", "win32")
    monkeypatch.setattr(process.sys, "_MEIPASS", r"C:\bundle\_internal", raising=False)
    monkeypatch.setattr(process, "_windows_dll_api", lambda: DllApi())
    return state


def test_frozen_spawn_restores_dll_directory_when_process_creation_fails(frozen_windows, monkeypatch):
    def failing_spawn(*args, **kwargs):
        assert frozen_windows.directory is None
        raise FileNotFoundError("missing solver")

    monkeypatch.setattr(process.subprocess, "Popen", failing_spawn)
    with pytest.raises(FileNotFoundError, match="missing solver"):
        process.external_popen(["missing.exe"], env={"PATH": r"C:\native\bin"})
    assert frozen_windows.calls == [None, r"C:\bundle\_internal"]
    assert frozen_windows.directory == r"C:\bundle\_internal"


def test_frozen_spawn_cleans_only_child_bundle_paths_and_restores_parent(frozen_windows, monkeypatch):
    environment = {
        "Path": r'C:\bundle\_internal;"C:\BUNDLE\_INTERNAL\PySide6";C:\bundle\_internal-other;D:\native\bin;;C:\Windows',
        "CUSTOM_SETTING": "retain me",
    }
    original = dict(environment)
    handle = object()
    parent_path = process.os.environ.get("PATH")

    def spawn(command, **kwargs):
        assert command == ["solver.exe", "-case", "case with spaces"]
        assert frozen_windows.directory is None
        assert kwargs["env"] == {
            "Path": r"C:\bundle\_internal-other;D:\native\bin;;C:\Windows",
            "CUSTOM_SETTING": "retain me",
        }
        assert kwargs["cwd"] == "case with spaces"
        return handle

    monkeypatch.setattr(process.subprocess, "Popen", spawn)
    assert process.external_popen(["solver.exe", "-case", "case with spaces"],
                                  env=environment, cwd="case with spaces") is handle
    assert environment == original
    assert process.os.environ.get("PATH") == parent_path
    assert frozen_windows.calls == [None, r"C:\bundle\_internal"]
    assert frozen_windows.directory == r"C:\bundle\_internal"


def test_frozen_spawn_copies_inherited_environment(frozen_windows, monkeypatch):
    inherited = r"C:\bundle\_internal\Qt;D:\native\bin"
    monkeypatch.setenv("PATH", inherited)
    monkeypatch.setenv("EXTERNAL_PROCESS_TEST", "keep")

    def spawn(*args, **kwargs):
        assert kwargs["env"]["PATH"] == r"D:\native\bin"
        assert kwargs["env"]["EXTERNAL_PROCESS_TEST"] == "keep"
        kwargs["env"]["EXTERNAL_PROCESS_TEST"] = "child copy"

    monkeypatch.setattr(process.subprocess, "Popen", spawn)
    process.external_popen(["solver.exe"])
    assert process.os.environ["PATH"] == inherited
    assert process.os.environ["EXTERNAL_PROCESS_TEST"] == "keep"


def test_frozen_spawn_excludes_bundled_runtime_settings(frozen_windows, monkeypatch):
    environment = {
        "PATH": r"C:\Windows",
        "QT_PLUGIN_PATH": r"C:\bundle\_internal\PySide6\plugins",
        "GDAL_DATA": r"C:\bundle\_internal\rasterio\gdal_data",
        "GDAL_CURL_CA_BUNDLE": r"C:\bundle\_internal\certifi\cacert.pem",
        "PROJ_LIB": r"C:\bundle\_internal\pyproj\proj_dir\share\proj",
        "QT_FORCE_ACCESSIBILITY": "1",
        "PROJ_NETWORK": "ON",
        "PROJ_CURL_CA_BUNDLE": r"D:\organization\ca.pem",
    }
    original = dict(environment)

    def spawn(*args, **kwargs):
        assert kwargs["env"] == {
            "PATH": r"C:\Windows",
            "QT_FORCE_ACCESSIBILITY": "1",
            "PROJ_NETWORK": "ON",
            "PROJ_CURL_CA_BUNDLE": r"D:\organization\ca.pem",
        }

    monkeypatch.setattr(process.subprocess, "Popen", spawn)
    process.external_popen(["solver.exe"], env=environment)
    assert environment == original


@pytest.mark.parametrize("frozen,platform", [(False, "win32"), (True, "linux")])
def test_unfrozen_or_nonwindows_spawn_uses_popen_without_winapi(monkeypatch, frozen, platform):
    monkeypatch.setattr(process.sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(process.sys, "platform", platform)
    environment = {"PATH": "unchanged"}
    handle = object()

    def forbidden_api():
        pytest.fail("WinAPI must not be called for an ordinary subprocess")

    def spawn(command, **kwargs):
        assert command == ["solver"]
        assert kwargs["env"] is environment
        assert kwargs["stdout"] is process.subprocess.PIPE
        return handle

    monkeypatch.setattr(process, "_windows_dll_api", forbidden_api)
    monkeypatch.setattr(process.subprocess, "Popen", spawn)
    assert process.external_popen(["solver"], env=environment, stdout=process.subprocess.PIPE) is handle
