from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path

import pytest


MODULE_SPEC = spec_from_file_location(
    "package_windows_app", Path(__file__).resolve().parents[2] / "scripts" / "package_windows_app.py",
)
packaging = module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(packaging)


def test_shared_application_spec_has_two_entries_and_no_local_defaults(tmp_path):
    spec = packaging.write_spec(tmp_path / "spec", tmp_path / "project with spaces")
    text = spec.read_text(encoding="utf-8")
    compile(text, str(spec), "exec")
    assert "windows_ui_entry.py" in text and "windows_cli_entry.py" in text
    assert "COLLECT(ui, cli, a.binaries, a.datas" in text
    assert "config/defaults.yaml" not in text
    assert "G:/weather_nowcasting" not in text
    for name in ("rasterio", "netCDF4", "pyproj", "PySide6.QtWidgets"):
        assert name in text


def _runtime(tmp_path, executable="bin/simpleFoam.exe", requires_wsl=False):
    windninja = tmp_path / "windninja"
    (windninja / "bin").mkdir(parents=True)
    (windninja / "bin" / "WindNinja_cli.exe").write_bytes(b"fixture")
    (windninja / "share").mkdir()
    foam = tmp_path / "openfoam8"
    (foam / "bin").mkdir(parents=True)
    (foam / "bin" / "simpleFoam.exe").write_bytes(b"fixture")
    (foam / "native_runtime.json").write_text(json.dumps({
        "requires_wsl": requires_wsl, "executables": {"simpleFoam": executable},
    }), encoding="utf-8")
    return windninja, foam


def test_packaging_accepts_existing_relative_native_runtime(tmp_path):
    packaging.validate_inputs(*_runtime(tmp_path))


@pytest.mark.parametrize("executable", ["../outside.exe", "bin/missing.exe"])
def test_packaging_rejects_missing_or_escaping_native_executable(tmp_path, executable):
    windninja, foam = _runtime(tmp_path, executable=executable)
    (tmp_path / "outside.exe").write_bytes(b"fixture")
    with pytest.raises(ValueError, match="Missing or nonportable"):
        packaging.validate_inputs(windninja, foam)


def test_packaging_rejects_wsl_dependent_runtime(tmp_path):
    with pytest.raises(ValueError, match="requires_wsl=false"):
        packaging.validate_inputs(*_runtime(tmp_path, requires_wsl=True))


def test_packaging_rejects_unbuilt_native_runtime(tmp_path):
    with pytest.raises(FileNotFoundError, match="Required portable runtime"):
        packaging.validate_inputs(tmp_path / "windninja", tmp_path / "openfoam")


def test_runtime_packaging_rejects_stale_frozen_sources(tmp_path):
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "scripts").mkdir()
    source = project / "src" / "native.py"
    source.write_text("backend = 'stationNudging'\n", encoding="utf-8")
    for name in ("windows_ui_entry.py", "windows_cli_entry.py", "package_windows_app.py"):
        (project / "scripts" / name).write_text("pass\n", encoding="utf-8")
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    recorded = packaging.source_fingerprints(project)
    (app_dir / "build_sources.json").write_text(json.dumps({"source_sha256": recorded}), encoding="utf-8")
    assert packaging.verify_frozen_sources(app_dir, project)["source_sha256"] == recorded
    assert all(not Path(name).is_absolute() for name in recorded)
    source.write_text("backend = 'changed'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="sources changed after freezing"):
        packaging.verify_frozen_sources(app_dir, project)


def test_dll_colocation_keeps_solver_versions_separate(tmp_path):
    for version in ("2.2", "8"):
        root = tmp_path / version
        (root / "lib").mkdir(parents=True)
        (root / "bin").mkdir()
        (root / "lib/libOpenFOAM.dll").write_bytes(version.encode())
        (root / "lib/not-a-library.txt").write_text("excluded")
        copied = packaging.colocate_runtime_dlls(root / "lib", root / "bin")
        assert copied == [root / "bin/libOpenFOAM.dll"]
        assert copied[0].read_bytes() == version.encode()
        assert not (root / "bin/not-a-library.txt").exists()
        assert packaging.colocate_runtime_dlls(root / "lib", root / "bin") == copied


def test_dll_colocation_rejects_existing_different_library(tmp_path):
    library, executable = tmp_path / "lib", tmp_path / "bin"
    library.mkdir()
    executable.mkdir()
    (library / "shared.dll").write_bytes(b"expected")
    (executable / "shared.dll").write_bytes(b"other version")
    with pytest.raises(ValueError, match="Conflicting runtime DLL"):
        packaging.colocate_runtime_dlls(library, executable)
    assert (executable / "shared.dll").read_bytes() == b"other version"
