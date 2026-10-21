import json

import pytest

from wind3d_ninja.windninja.native import NativeRuntime


def runtime_manifest(root):
    (root / "bin").mkdir(parents=True)
    (root / "lib").mkdir()
    (root / "etc").mkdir()
    for path in ("bin/simpleFoam.exe", "bin/postProcess.exe", "lib/libWindNinja.dll"):
        (root / path).write_bytes(b"fixture")
    manifest = {"platform": "windows-native", "openfoam_version": "8",
                "source_model": {"type": "stationNudging", "turbulence_model": "stationKEpsilon"},
                "executables": {"simpleFoam": "bin/simpleFoam.exe", "postProcess": "bin/postProcess.exe"},
                "boundary_library": "lib/libWindNinja.dll", "etc_dir": "etc", "project_dir": ".", "library_dirs": ["lib"]}
    (root / "native_runtime.json").write_text(json.dumps(manifest))
    return manifest


def test_runtime_relocates_and_excludes_other_openfoam_environments(tmp_path, monkeypatch):
    original = tmp_path / "original"
    runtime_manifest(original)
    moved = tmp_path / "relocated directory with spaces"
    original.rename(moved)
    monkeypatch.setenv("FOAM_SITE_LIBBIN", "old-2.2-runtime")
    monkeypatch.setenv("WM_PROJECT_VERSION", "2.2.0")
    monkeypatch.setenv("PATH", "old-compiler-and-wsl")
    runtime = NativeRuntime(moved)
    env = runtime.environment()
    assert runtime.executables["simpleFoam"].is_relative_to(moved)
    assert env["WM_PROJECT_VERSION"] == "8"
    assert "FOAM_SITE_LIBBIN" not in env
    assert "old-compiler-and-wsl" not in env["PATH"]
    assert str(moved / "lib") in env["PATH"]


def test_runtime_rejects_paths_outside_portable_package(tmp_path):
    manifest = runtime_manifest(tmp_path / "runtime")
    manifest["boundary_library"] = "../../outside.dll"
    (tmp_path / "runtime/native_runtime.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="escapes"):
        NativeRuntime(tmp_path / "runtime")
