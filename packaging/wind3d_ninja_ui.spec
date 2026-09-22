from pathlib import Path
from PyInstaller.utils.hooks import collect_all

project = Path(SPECPATH).parent
block_cipher = None
rasterio_data, rasterio_bins, rasterio_hidden = collect_all("rasterio")
pyproj_data, pyproj_bins, pyproj_hidden = collect_all("pyproj")
a = Analysis(
    [str(project / "packaging" / "ui_entry.py")],
    pathex=[str(project / "src")], binaries=rasterio_bins + pyproj_bins,
    datas=[(str(project / "config"), "config")] + rasterio_data + pyproj_data,
    hiddenimports=["PySide6", "netCDF4", "openpyxl", "xlrd", "elevation"] + rasterio_hidden + pyproj_hidden,
    hookspath=[], hooksconfig={}, runtime_hooks=[str(project / "packaging" / "ui_runtime_hook.py")],
    excludes=["torch", "matplotlib", "pytest", "scipy", "boto3", "botocore"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="wind3d-ninja-ui", debug=False,
          bootloader_ignore_signals=False, strip=False, upx=True, console=False)
