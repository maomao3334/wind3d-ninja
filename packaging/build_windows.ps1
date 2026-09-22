$ErrorActionPreference = "Stop"
py -3.11 -m PyInstaller --clean --noconfirm packaging/wind3d_ninja_cli.spec
py -3.11 -m PyInstaller --clean --noconfirm --workpath build/ui --distpath dist packaging/wind3d_ninja_ui.spec
py -3.11 -m PyInstaller --clean --noconfirm --workpath build/ui-portable --distpath release packaging/wind3d_ninja_ui_portable.spec
Write-Host "Built dist/wind3d-ninja.exe and dist/wind3d-ninja-ui.exe"
