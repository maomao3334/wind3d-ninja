from pathlib import Path
from PyInstaller.utils.hooks import collect_all

project = Path(SPECPATH).parent
rasterio_data, rasterio_bins, rasterio_hidden = collect_all("rasterio")
pyproj_data, pyproj_bins, pyproj_hidden = collect_all("pyproj")

a = Analysis(
    [str(project / "packaging" / "ui_entry.py")],
    pathex=[str(project / "src")],
    binaries=rasterio_bins + pyproj_bins,
    datas=[(str(project / "config"), "config")] + rasterio_data + pyproj_data,
    hiddenimports=["netCDF4", "openpyxl", "xlrd", "elevation"] + rasterio_hidden + pyproj_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(project / "packaging" / "ui_runtime_hook.py")],
    excludes=["torch", "matplotlib", "pytest", "scipy", "boto3", "botocore"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="wind3d-ninja-ui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="wind3d-ninja-ui-portable",
)
