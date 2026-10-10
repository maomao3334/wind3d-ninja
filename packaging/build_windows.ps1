$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
Push-Location $ProjectRoot
try {
py -3.11 -m PyInstaller --clean --noconfirm packaging/wind3d_ninja_cli.spec
py -3.11 -m PyInstaller --clean --noconfirm --workpath build/ui --distpath dist packaging/wind3d_ninja_ui.spec
py -3.11 -m PyInstaller --clean --noconfirm --workpath build/cli-portable --distpath release packaging/wind3d_ninja_cli_portable.spec
py -3.11 -m PyInstaller --clean --noconfirm --workpath build/ui-portable --distpath release packaging/wind3d_ninja_ui_portable.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}
Write-Host "Built dist/wind3d-ninja.exe, dist/wind3d-ninja-ui.exe, and portable CLI/UI folders under release/"
}
finally {
    Pop-Location
}
