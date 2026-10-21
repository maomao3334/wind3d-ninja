from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1] / "output/native_rans_20261012"
SDK = ROOT / "sdk"


def msys_path(path: Path) -> str:
    value = str(path.absolute()).replace("\\", "/")
    return "/" + value[0].lower() + value[2:]


def build(drive: str) -> None:
    extracted = SDK / "extracted"
    sdk_source = extracted / "$" / "app" / "OpenFOAM-8"
    sdk_runtime = extracted / "app" / "OpenFOAM-8"
    if sdk_source.exists():
        for directory in ("src", "applications"):
            source_path = chr(92) * 2 + "?" + chr(92) + str((sdk_source / directory).resolve())
            target_path = chr(92) * 2 + "?" + chr(92) + str((sdk_runtime / directory).resolve())
            shutil.copytree(source_path, target_path, dirs_exist_ok=True)
        print("Installer SDK sources overlaid into runtime OpenFOAM root", flush=True)
    bash = next(extracted.rglob("usr/bin/bash.exe"))
    bashrc = next(p for p in extracted.rglob("etc/bashrc") if p.parent.parent.name == "OpenFOAM-8")
    foam = bashrc.parent.parent
    msys = bash.parent.parent.parent
    compiler = next(p for p in extracted.rglob("g++.exe") if "mingw64" in str(p))
    env = os.environ.copy()
    env.update({"MSYSTEM": "MINGW64", "CHERE_INVOKING": "1", "WM_TARGET_ARCH": "mingw_w64", "WM_OSTYPE": "MSwindows"})
    env["PATH"] = os.pathsep.join([str(compiler.parent), str(bash.parent), os.path.join(os.environ["SystemRoot"], "System32")])
    (ROOT / "build" / "home").mkdir(parents=True, exist_ok=True)
    source = ROOT / "build" / "boundary_src"
    if not source.exists():
        shutil.copytree(Path("G:/weather_nowcasting/tools/windninja-src/src/ninjafoam/8"), source)
    native_source = Path(__file__).resolve().parents[1] / "src" / "nativefoam"
    shutil.copytree(native_source, source / "stationNudging", dirs_exist_ok=True)
    make_files = source / "Make" / "files"
    make_text = make_files.read_text()
    if "stationNudging/stationNudging.C" not in make_text:
        make_text = "stationNudging/stationNudging.C" + chr(10) + make_text
        make_files.write_text(make_text)
    if "stationNudging/stationKEpsilonRegistration.C" not in make_text:
        make_text = "stationNudging/stationKEpsilonRegistration.C" + chr(10) + make_text
        make_files.write_text(make_text)
    compiler_version = subprocess.run([str(compiler), "--version"], env=env, capture_output=True, text=True, check=True).stdout.splitlines()[0]
    build_env_file = ROOT / "build" / "foam_environment.txt"
    build_user = ROOT / "build" / "home"
    build_lib = ROOT / "build" / "lib"
    build_lib.mkdir(parents=True, exist_ok=True)
    short_root = Path(drive + "/")
    short_bashrc = short_root / bashrc.relative_to(ROOT)
    short_source = short_root / source.relative_to(ROOT)
    short_build_lib = short_root / build_lib.relative_to(ROOT)
    short_build_user = short_root / build_user.relative_to(ROOT)
    short_env_file = short_root / build_env_file.relative_to(ROOT)
    script = f"source '{msys_path(short_bashrc)}' WM_TARGET_ARCH=mingw_w64 WM_OSTYPE=MSwindows WM_MPLIB=NONE WM_PROJECT_USER_DIR='{msys_path(short_build_user)}' && export FOAM_USER_LIBBIN='{msys_path(short_build_lib)}' && cd '{msys_path(short_source)}' && wmake libso && env > '{msys_path(short_env_file)}'"
    log_path = ROOT / "build" / "boundary_build.log"
    with log_path.open("w", encoding="utf-8") as log:
        subprocess.run([str(bash), "--noprofile", "--norc", "-c", script], env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    values = dict(line.split("=", 1) for line in build_env_file.read_text().splitlines() if "=" in line)
    platform = foam / "platforms" / values["WM_OPTIONS"]
    library = next((ROOT / "build").rglob("libWindNinja.dll"), None)
    if library is None:
        library = next(extracted.rglob("libWindNinja.dll"), None)
    if library is None:
        raise FileNotFoundError("wmake did not produce libWindNinja.dll")
    thirdparty_lib = next(extracted.rglob("libstack_trace.dll")).parent
    runtime_env = env.copy()
    runtime_env.update({"WM_PROJECT_DIR": str(foam), "FOAM_ETC": str(foam / "etc"), "FOAM_LIBBIN": str(platform / "lib"), "FOAM_USER_LIBBIN": str(library.parent)})
    runtime_env["PATH"] = os.pathsep.join([str(platform / "bin"), str(library.parent), str(platform / "lib/dummy"), str(platform / "lib"), str(thirdparty_lib), str(compiler.parent), os.path.join(os.environ["SystemRoot"], "System32")])
    help_results = {}
    for executable in ("simpleFoam.exe", "postProcess.exe"):
        result = subprocess.run([str(platform / "bin" / executable), "-help"], env=runtime_env, capture_output=True, text=True)
        (ROOT / "build" / (executable + ".help.txt")).write_text(result.stdout + result.stderr, encoding="utf-8")
        help_results[executable] = {"returncode": result.returncode}
    objdump = compiler.parent / "objdump.exe"
    imports = {}
    dynamic_names = ["libsampling.dll", "libfieldFunctionObjects.dll", "libfvOptions.dll", "libincompressibleMomentumTransportModels.dll", "libmomentumTransportModels.dll"]
    roots = [platform / "bin/simpleFoam.exe", platform / "bin/postProcess.exe", library]
    for name in dynamic_names:
        matches = list((platform / "lib").rglob(name))
        if not matches:
            raise FileNotFoundError(f"Required dynamic runtime library missing: {name}")
        roots.append(matches[0])
    for file in roots:
        result = subprocess.run([str(objdump), "-p", str(file)], capture_output=True, text=True, check=True)
        imports[str(file)] = [line.partition("DLL Name:")[2].strip() for line in result.stdout.splitlines() if "DLL Name:" in line]
    search_dirs = [library.parent, platform / "lib/dummy", platform / "lib", thirdparty_lib, compiler.parent]
    runtime_closure = {}
    unresolved = set()
    queue = list(imports)
    seen = set()
    system32 = Path(os.environ["SystemRoot"]) / "System32"
    while queue:
        path = Path(queue.pop())
        if path in seen:
            continue
        seen.add(path)
        result = subprocess.run([str(objdump), "-p", str(path)], capture_output=True, text=True, check=True)
        names = [line.partition("DLL Name:")[2].strip() for line in result.stdout.splitlines() if "DLL Name:" in line]
        runtime_closure[str(path)] = names
        for name in names:
            matches = [directory / name for directory in search_dirs if (directory / name).is_file()]
            if matches:
                queue.append(str(matches[0]))
            elif name.lower() in ("msmpi.dll", "msys-2.0.dll") or (not (system32 / name).is_file() and not name.lower().startswith(("api-ms-", "ext-ms-"))):
                unresolved.add(name)
    manifest = {
        "source_model": {"type": "stationNudging", "turbulence_model": "stationKEpsilon",
                         "epsilon_initial_guess": "restore pre-wall-update internal field after matrix manipulation and source addition",
                         "turbulence_source_sha256": hashlib.sha256(b"".join((native_source / name).read_bytes() for name in ("stationKEpsilon.H", "stationKEpsilon.C", "stationKEpsilonRegistration.C"))).hexdigest(), "linearisation": "Su - fvm::Sp(lambda,q)",
                         "source_sha256": hashlib.sha256((native_source / "stationNudging.C").read_bytes()).hexdigest()},
        "release": "blueCFD-Core-2020-1", "openfoam_version": "8", "upstream_commit": "218f49416d26264af7e84df7cbebca25ad43a172",
        "compiler": str(compiler), "compiler_version": compiler_version, "msys_root": str(msys), "foam_root": str(foam),
        "platform": values["WM_OPTIONS"], "platform_bin": str(platform / "bin"), "platform_lib": str(platform / "lib"),
        "boundary_dll": str(library), "help_checks": help_results, "direct_dll_imports": imports,
        "runtime_dll_closure": runtime_closure, "unresolved_runtime_imports": sorted(unresolved),
        "runtime_environment": {key: runtime_env[key] for key in ("WM_PROJECT_DIR", "FOAM_ETC", "FOAM_LIBBIN", "FOAM_USER_LIBBIN", "PATH")},
        "runtime_requires_wsl": False, "installer_executed": False,
        "native_runtime_source_mapping": {
            "platform": "windows-native", "openfoam_version": "8",
            "executables": {name: str(platform / "bin" / (name + ".exe")) for name in ("simpleFoam", "postProcess")},
            "boundary_library": str(library), "etc_dir": str(foam / "etc"), "project_dir": str(foam),
            "library_dirs": [str(path) for path in search_dirs], "wm_options": values["WM_OPTIONS"],
        },
    }
    (SDK / "sdk_build_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if any(value["returncode"] for value in help_results.values()):
        raise RuntimeError("Native executable help check failed; inspect help logs")
    print(f"Native boundary built: {library}", flush=True)


def main() -> None:
    drive = next(letter + ":" for letter in "RQTUVWXYZ" if not Path(letter + ":/").exists())
    target = chr(92) + "??" + chr(92) + str(ROOT.resolve())
    if not ctypes.windll.kernel32.DefineDosDeviceW(1, drive, target):
        raise ctypes.WinError()
    try:
        build(drive)
    finally:
        if not ctypes.windll.kernel32.DefineDosDeviceW(3, drive, target):
            raise ctypes.WinError()


if __name__ == "__main__":
    main()
