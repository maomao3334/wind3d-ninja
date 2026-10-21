from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import tarfile


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "output/native_rans_20261012"


def main() -> None:
    build = json.loads((EVIDENCE / "sdk/sdk_build_manifest.json").read_text())
    if build["unresolved_runtime_imports"]:
        raise RuntimeError(f"Unresolved native imports: {build['unresolved_runtime_imports']}")
    if any(value["returncode"] for value in build["help_checks"].values()):
        raise RuntimeError("SDK native executable smoke checks failed")
    target = ROOT / "runtime/openfoam8"
    target.mkdir(parents=True, exist_ok=True)
    mapping = build["native_runtime_source_mapping"]
    bin_dir = target / "bin"
    lib_dir = target / "lib"
    bin_dir.mkdir(exist_ok=True)
    lib_dir.mkdir(exist_ok=True)
    selected = {}
    for source in build["runtime_dll_closure"]:
        path = Path(source)
        previous = selected.get(path.name.lower())
        if previous and previous != path:
            raise ValueError(f"Conflicting DLL basenames: {previous}, {path}")
        selected[path.name.lower()] = path
        shutil.copy2(path, (bin_dir if path.suffix.lower() == ".exe" else lib_dir) / path.name)
    if any("msmpi.dll" in (name.lower() for name in names)
           for names in build["runtime_dll_closure"].values()):
        raise ValueError("Serial runtime unexpectedly depends on externally installed MS-MPI")
    foam = Path(build["foam_root"])
    shutil.copytree(foam / "etc", target / "etc", dirs_exist_ok=True)
    licenses = target / "licenses"
    licenses.mkdir(exist_ok=True)
    for name in ("COPYING", "README.org"):
        if (foam / name).is_file():
            shutil.copy2(foam / name, licenses / ("OpenFOAM-" + name))
    windninja = Path("G:/weather_nowcasting/tools/windninja-src")
    for name in ("LICENSE", "COPYING"):
        if (windninja / name).is_file():
            shutil.copy2(windninja / name, licenses / ("WindNinja-" + name))
    sources = EVIDENCE / "native-corresponding-source.tar.gz"
    with tarfile.open(sources, "w:gz") as archive:
        for name in ("src", "applications", "wmake", "etc", "COPYING", "README.org", "Allwmake"):
            if (foam / name).exists():
                archive.add(foam / name, arcname="OpenFOAM-8/" + name)
        archive.add(EVIDENCE / "build/boundary_src", arcname="WindNinja-boundary")
        archive.add(ROOT / "scripts/build_native_windows.py", arcname="build_native_windows.py")
    (licenses / "SOURCES.md").write_text(
        "OpenFOAM 8 / blueCFD-Core-2020-1: GPL-3.0-or-later.\n"
        "https://github.com/blueCFD/OpenFOAM-dev/tree/blueCFD-Core-8\n"
        f"Upstream commit: {build['upstream_commit']}\n"
        "WindNinja boundary source: f3e9923daf622154258ccf573b419a028672b787.\n"
        "https://github.com/firelab/windninja/tree/f3e9923daf622154258ccf573b419a028672b787/src/ninjafoam/8\n"
        "Distribute native-corresponding-source.tar.gz beside the software.\n"
        "GCC runtime libraries use GPLv3 with the GCC Runtime Library Exception.\n"
        "https://www.gnu.org/licenses/gcc-exception-3.1.html\n",
        encoding="utf-8")
    manifest = {
        "platform": "windows-native", "openfoam_version": "8",
        "executables": {name: "bin/" + Path(value).name for name, value in mapping["executables"].items()},
        "boundary_library": "lib/" + Path(mapping["boundary_library"]).name,
        "project_dir": ".", "etc_dir": "etc", "library_dirs": ["lib"],
        "wm_options": mapping["wm_options"], "requires_wsl": False,
        "release": build["release"], "upstream_commit": build["upstream_commit"],
        "source_archive": sources.name,
        "source_model": build["source_model"],
    }
    manifest["files"] = {
        path.relative_to(target).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(target.rglob("*")) if path.is_file() and path.name != "native_runtime.json"
    }
    (target / "native_runtime.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (EVIDENCE / "runtime_package.json").write_text(json.dumps({
        "directory": str(target), "source_archive": str(sources),
        "file_count": len(manifest["files"]),
        "bytes": sum(path.stat().st_size for path in target.rglob("*") if path.is_file()),
        "compiler_included": False, "msys_shell_included": False, "requires_wsl": False,
    }, indent=2), encoding="utf-8")
    print(f"Portable native runtime: {target}", flush=True)


if __name__ == "__main__":
    main()
