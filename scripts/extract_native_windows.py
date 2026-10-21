from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1] / "output/native_rans_20261012/sdk"
URL = "https://constexpr.org/innoextract/files/innoextract-1.9/innoextract-1.9-windows.zip"
SHA256 = "6989342c9b026a00a72a38f23b62a8e6a22cc5de69805cf47d68ac2fec993065"
INSTALLER_SHA1 = "31c78a935309ece053035e4d2a81ebac5f766c43"


def digest(path: Path, algorithm: str) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()


def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    archive = ROOT / "innoextract-1.9-windows.zip"
    if not archive.exists():
        with urllib.request.urlopen(URL, timeout=60) as response:
            archive.write_bytes(response.read())
    if digest(archive, "sha256") != SHA256:
        raise ValueError("innoextract official ZIP SHA256 mismatch")
    tools = ROOT / "extractor"
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (tools / member.filename).resolve()
            if not target.is_relative_to(tools.resolve()):
                raise ValueError("Unsafe archive member")
        bundle.extractall(tools)
    extractor = next(tools.rglob("innoextract.exe"))
    installer = ROOT / "blueCFD-Core-2020-1-win64-setup.exe"
    if installer.stat().st_size != 870543288:
        raise ValueError("SDK download is not complete")
    if digest(installer, "sha1") != INSTALLER_SHA1:
        raise ValueError("SDK official release SHA1 mismatch")
    listing = subprocess.run([str(extractor), "--list", str(installer)], capture_output=True, text=True, check=True)
    (ROOT / "installer_listing.txt").write_text(listing.stdout, encoding="utf-8")
    print("Official installer verified; listing saved", flush=True)
    destination = ROOT / "extracted"
    selected: set[str] = set()
    for line in listing.stdout.splitlines():
        if '"' in line:
            candidate = line.split('"')[1].replace("\\", "/")
            if candidate.startswith("app/") and "paraview" not in candidate.lower() and "/AddOns/" not in candidate:
                selected.add(candidate.split("/")[1])
    if not selected:
        raise ValueError("No SDK app files found in installer listing")
    destination.mkdir(parents=True, exist_ok=True)
    drive = next(letter + ":" for letter in "RQTUVWXYZ" if not Path(letter + ":/").exists())
    target = "\??\\" + str(destination.resolve())
    if not ctypes.windll.kernel32.DefineDosDeviceW(1, drive, target):
        raise ctypes.WinError()
    args = [str(extractor), "--extract", "--test", "--output-dir", drive + "\\"]
    selected.discard("OpenFOAM-8")
    selected.update("OpenFOAM-8/" + part for part in (
        "src", "etc", "wmake", "bin", "platforms",
        "applications/solvers/incompressible/simpleFoam",
        "applications/utilities/postProcessing/postProcess",
    ))
    for prefix in sorted(selected):
        args.extend(["--include", prefix.replace("/", chr(92))])
    args.append(str(installer))
    try:
        with (ROOT / "extraction.log").open("w", encoding="utf-8") as log:
            subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, check=True)
    finally:
        if not ctypes.windll.kernel32.DefineDosDeviceW(3, drive, target):
            raise ctypes.WinError()
    if not any(destination.rglob("simpleFoam.exe")):
        raise RuntimeError("Extraction did not produce native OpenFOAM binaries")
    (ROOT / "extraction_manifest.json").write_text(json.dumps({
        "installer_sha1": INSTALLER_SHA1, "extractor_sha256": SHA256,
        "extractor_url": URL, "selected_prefixes": sorted(selected),
        "directory": str(destination), "installer_executed": False,
    }, indent=2), encoding="utf-8")
    print(f"SDK extracted without installation: {destination}", flush=True)


if __name__ == "__main__":
    main()
