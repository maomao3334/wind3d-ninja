param(
    [Parameter(Mandatory=$true)][string]$WindNinjaSource,
    [Parameter(Mandatory=$true)][string]$OpenFoamRuntime,
    [Parameter(Mandatory=$true)][string]$NativeSourceArchive
)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PreviousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = Join-Path $ProjectRoot "src"
try {
    & py -3.11 (Join-Path $ProjectRoot "scripts\package_windows_app.py") --windninja-root $WindNinjaSource --openfoam-root $OpenFoamRuntime --native-source-archive $NativeSourceArchive
    if ($LASTEXITCODE -ne 0) { throw "Unified Windows build failed: $LASTEXITCODE" }
}
finally { $env:PYTHONPATH = $PreviousPythonPath }
