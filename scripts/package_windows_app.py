from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
import ast
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def source_fingerprints(project_root: Path) -> dict[str, str]:
    sources = sorted((project_root / "src").rglob("*.py"))
    sources.extend(project_root / "scripts" / name for name in (
        "windows_ui_entry.py", "windows_cli_entry.py", "package_windows_app.py",
    ))
    return {path.relative_to(project_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sources}


def verify_frozen_sources(app_dir: Path, project_root: Path) -> dict:
    record = json.loads((app_dir / "build_sources.json").read_text(encoding="utf-8"))
    if record["source_sha256"] != source_fingerprints(project_root):
        raise ValueError("Application sources changed after freezing; rebuild the application before packaging runtimes")
    return record


def write_spec(destination: Path, project_root: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    spec = destination / "Wind3D.spec"
    spec.write_text(
        "from PyInstaller.utils.hooks import collect_all\n"
        f"project = {str(project_root.resolve())!r}\n"
        "datas, binaries, hiddenimports = [], [], []\n"
        "for package in ('rasterio', 'netCDF4', 'pyproj'):\n"
        "    package_datas, package_binaries, package_imports = collect_all(package)\n"
        "    datas += package_datas\n"
        "    binaries += package_binaries\n"
        "    hiddenimports += package_imports\n"
        "hiddenimports += ['PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets']\n"
        "a = Analysis([project + '/scripts/windows_ui_entry.py',\n"
        "              project + '/scripts/windows_cli_entry.py'],\n"
        "             pathex=[project + '/src'], binaries=binaries,\n"
        "             datas=[(project + '/config', 'config')] + datas,\n"
        "             hiddenimports=hiddenimports, hookspath=[], hooksconfig={},\n"
        "             runtime_hooks=[], excludes=['pytest', 'torch', 'tensorflow', 'transformers',\n"
        "                 'IPython', 'scipy', 'matplotlib', 'numba', 'jax', 'jupyter'], noarchive=False)\n"
        "pyz = PYZ(a.pure)\n"
        "# A host-side Poppler runtime can be discovered through PATH while PyInstaller\n"
        "# scans native dependencies.  Its generic ICU 78 DLL has version-suffixed\n"
        "# exports and shadows Windows' compatible ICU DLL for Qt6Core after freeze.\n"
        "# Keep package-scoped, hashed ICU libraries (for netCDF4) but discard only\n"
        "# that unscoped collision so the Qt runtime resolves the system ICU.\n"
        "a.binaries = a.binaries.__class__([entry for entry in a.binaries\n"
        "                                    if entry[0].replace('\\\\', '/').casefold() != 'icuuc.dll'])\n"
        "a.datas = a.datas.__class__([entry for entry in a.datas\n"
        "                              if entry[0].replace('\\\\', '/').casefold() != 'icuuc.dll'])\n"
        "ui_scripts = [item for item in a.scripts if item[0] != 'windows_cli_entry']\n"
        "cli_scripts = [item for item in a.scripts if item[0] != 'windows_ui_entry']\n"
        "ui = EXE(pyz, ui_scripts, [], exclude_binaries=True, name='Wind3D',\n"
        "         debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False)\n"
        "cli = EXE(pyz, cli_scripts, [], exclude_binaries=True, name='Wind3D-cli',\n"
        "          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=True)\n"
        "COLLECT(ui, cli, a.binaries, a.datas, strip=False, upx=False, name='Wind3D')\n",
        encoding="utf-8",
    )
    return spec


def validate_inputs(windninja_root: Path, openfoam_root: Path) -> None:
    for path in (windninja_root / "bin" / "WindNinja_cli.exe", openfoam_root / "native_runtime.json"):
        if not path.is_file():
            raise FileNotFoundError(f"Required portable runtime file is missing: {path}")
    if not (windninja_root / "share").is_dir():
        raise FileNotFoundError(f"WindNinja share directory is missing: {windninja_root / 'share'}")
    native = json.loads((openfoam_root / "native_runtime.json").read_text(encoding="utf-8"))
    if native.get("requires_wsl") is not False:
        raise ValueError("The packaged OpenFOAM runtime must explicitly declare requires_wsl=false")
    for relative in native["executables"].values():
        path = (openfoam_root / relative).resolve()
        if not path.is_relative_to(openfoam_root.resolve()) or not path.is_file():
            raise ValueError(f"Missing or nonportable native executable: {relative}")


def copy_licenses(app_dir: Path) -> None:
    destination = app_dir / "licenses"
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "LICENSE", destination / "Wind3D-LICENSE.txt")
    shutil.copytree(ROOT / "THIRD_PARTY_NOTICES", destination / "third-party", dirs_exist_ok=True)
    for package in ("PyInstaller", "rasterio", "netCDF4", "pyproj", "PySide6", "PySide6_Essentials",
                    "PySide6_Addons", "shiboken6", "numpy"):
        distribution = metadata.distribution(package)
        for relative in distribution.files or ():
            if ".dist-info" in str(relative) and any(
                token in relative.name.casefold() for token in ("license", "copying", "notice")
            ):
                source = Path(distribution.locate_file(relative))
                if source.is_file():
                    target = destination / package / Path(*relative.parts[1:])
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)


def _source_archive(
    project_root: Path,
    openfoam_root: Path,
    native: dict,
    explicit: Path | None,
) -> Path:
    name = str(native.get("source_archive", "native-corresponding-source.tar.gz"))
    candidates = [
        explicit,
        openfoam_root / name,
        openfoam_root.parent / name,
        project_root / "output" / "native_rans_20261012" / name,
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        "Native corresponding-source archive is missing; provide "
        f"--native-source-archive {name} when packaging"
    )


def application_version() -> str:
    nodes = ast.parse((ROOT / "src" / "wind3d_ninja" / "__init__.py").read_text(encoding="utf-8")).body
    for node in nodes:
        if isinstance(node, ast.Assign) and any(isinstance(name, ast.Name) and name.id == "__version__" for name in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError("Source application version is missing")


def colocate_runtime_dlls(library_dir: Path, executable_dir: Path) -> list[Path]:
    copied = []
    for source in sorted(library_dir.glob("*.dll")):
        destination = executable_dir / source.name
        if destination.is_file() and destination.read_bytes() != source.read_bytes():
            raise ValueError(f"Conflicting runtime DLL beside executable: {destination}")
        shutil.copy2(source, destination)
        copied.append(destination)
    return copied


def package_runtime(
    app_dir: Path,
    windninja_root: Path,
    openfoam_root: Path,
    native_source_archive: Path | None = None,
) -> None:
    target = app_dir / "runtime" / "windninja" / "3.12.2"
    for name in ("bin", "share", "etc", "lib"):
        if (windninja_root / name).is_dir():
            shutil.copytree(windninja_root / name, target / name, dirs_exist_ok=True)
    shutil.copytree(openfoam_root, app_dir / "runtime" / "openfoam8", dirs_exist_ok=True)
    native = json.loads((openfoam_root / "native_runtime.json").read_text(encoding="utf-8"))
    # Packaged Windows process contexts can exclude PATH from DLL discovery.
    legacy = target / "bin/platforms/linux64mingw-w64DPOpt"
    colocated = colocate_runtime_dlls(legacy / "lib", legacy / "bin")
    native_target = app_dir / "runtime/openfoam8"
    executable_dirs = {native_target / Path(relative).parent for relative in native["executables"].values()}
    for relative in native["library_dirs"]:
        library_dir = (native_target / relative).resolve()
        if not library_dir.is_relative_to(native_target.resolve()):
            raise ValueError(f"Native library directory escapes package: {relative}")
        for executable_dir in sorted(executable_dirs):
            colocated.extend(colocate_runtime_dlls(library_dir, executable_dir))
    source_archive = _source_archive(ROOT, openfoam_root, native, native_source_archive)
    shutil.copy2(source_archive, app_dir / source_archive.name)
    copy_licenses(app_dir)
    shutil.copy2(ROOT / "README.md", app_dir / "README.md")
    shutil.copytree(ROOT / "docs", app_dir / "docs", dirs_exist_ok=True)
    (app_dir / "portable_app.json").write_text(json.dumps({
        "version": application_version(),
        "platform": "windows-native", "ui": "Wind3D.exe", "cli": "Wind3D-cli.exe",
        "windninja_root": "runtime/windninja/3.12.2", "native_runtime": "runtime/openfoam8",
        "requires_wsl": False, "requires_system_python": False,
        "dem_policy": "User selects an existing DEM for offline runs",
        "local_default_config_included": True,
        "native_manifest_sha256": hashlib.sha256((openfoam_root / "native_runtime.json").read_bytes()).hexdigest(),
        "build_sources": "build_sources.json",
        "colocated_runtime_dll_sha256": {
            path.relative_to(app_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in colocated
        },
        "documentation_sha256": {path.relative_to(app_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in [app_dir / "README.md", *sorted((app_dir / "docs").rglob("*.md"))]},
    }, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the relocatable native Windows Wind3D application")
    parser.add_argument("--windninja-root", required=True, type=Path)
    parser.add_argument("--openfoam-root", type=Path, default=ROOT / "runtime" / "openfoam8")
    parser.add_argument("--dist-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--work-dir", type=Path, default=ROOT / "build" / "windows_app")
    parser.add_argument(
        "--native-source-archive",
        type=Path,
        help="Native corresponding-source archive included beside the application binaries.",
    )
    parser.add_argument("--stage", choices=("all", "freeze", "runtime"), default="all")
    options = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Build with Windows Python and PyInstaller on Windows")
    windninja_root, openfoam_root = options.windninja_root.resolve(), options.openfoam_root.resolve()
    if options.stage != "freeze":
        validate_inputs(windninja_root, openfoam_root)
    if options.stage != "runtime":
        fingerprints = source_fingerprints(ROOT)
        spec = write_spec(options.work_dir / "spec", ROOT)
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--distpath", str(options.dist_dir.resolve()),
            "--workpath", str((options.work_dir / "pyinstaller").resolve()), str(spec),
        ], check=True)
    app_dir = options.dist_dir.resolve() / "Wind3D"
    if options.stage != "runtime":
        if fingerprints != source_fingerprints(ROOT):
            raise ValueError("Application sources changed during freezing; rebuild before distribution")
        (app_dir / "build_sources.json").write_text(json.dumps({
            "source_sha256": fingerprints,
            "executable_sha256": {name: hashlib.sha256((app_dir / name).read_bytes()).hexdigest()
                                  for name in ("Wind3D.exe", "Wind3D-cli.exe")},
        }, indent=2), encoding="utf-8")
    if options.stage == "freeze":
        print(f"Application binaries frozen; portable runtime packaging is pending: {app_dir}", flush=True)
        return
    for name in ("Wind3D.exe", "Wind3D-cli.exe"):
        if not (app_dir / name).is_file():
            raise FileNotFoundError(f"Freeze the application before packaging runtimes: {app_dir / name}")
    verify_frozen_sources(app_dir, ROOT)
    package_runtime(app_dir, windninja_root, openfoam_root, options.native_source_archive)
    print(app_dir, flush=True)


if __name__ == "__main__":
    main()
