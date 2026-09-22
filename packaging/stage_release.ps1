param(
    [string]$WindNinjaSource = $env:WINDNINJA_SOURCE
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$UiSource = Join-Path $ProjectRoot "installer_dist\Wind3D-Ninja"
$CliSource = Join-Path $ProjectRoot "installer_dist\wind3d-ninja-cli"
$Stage = Join-Path $ProjectRoot "installer_stage"

if ([string]::IsNullOrWhiteSpace($WindNinjaSource)) {
    throw "Set WINDNINJA_SOURCE to the local WindNinja 3.12.2 directory before staging a release."
}
if (-not (Test-Path -LiteralPath $WindNinjaSource -PathType Container)) {
    throw "WindNinja source directory was not found: $WindNinjaSource"
}
$WindNinjaSource = (Resolve-Path -LiteralPath $WindNinjaSource).Path

$RequiredFiles = @(
    (Join-Path $UiSource "Wind3D-Ninja.exe"),
    (Join-Path $CliSource "wind3d-ninja.exe"),
    (Join-Path $WindNinjaSource "bin\WindNinja_cli.exe")
)
foreach ($RequiredFile in $RequiredFiles) {
    if (-not (Test-Path -LiteralPath $RequiredFile -PathType Leaf)) {
        throw "Required release file is missing: $RequiredFile"
    }
}

if (Test-Path -LiteralPath $Stage) {
    Remove-Item -LiteralPath $Stage -Recurse -Force
}
New-Item -ItemType Directory -Path $Stage | Out-Null

Copy-Item -LiteralPath $UiSource -Destination (Join-Path $Stage "ui") -Recurse
Copy-Item -LiteralPath $CliSource -Destination (Join-Path $Stage "cli") -Recurse
Copy-Item -LiteralPath $WindNinjaSource -Destination (Join-Path $Stage "windninja") -Recurse

$Readme = @(
    "Wind3D Ninja 1.0.0"
    "===================="
    ""
    "Included:"
    "- Wind3D Ninja desktop UI"
    "- wind3d-ninja command line tool"
    "- WindNinja 3.12.2 runtime"
    ""
    "Quick start:"
    "1. Open Wind3D Ninja from the desktop or Start menu."
    "2. Choose the input data folder."
    "3. Enter output heights, for example: 10 20 50."
    "4. Enter resolutions, for example: 50 100 200."
    "5. Leave DEM empty to match a local DEM first and download one if needed."
    "6. Run doctor, inspect, plan, then run."
    ""
    "No separate Python or WindNinja installation is required."
) -join [Environment]::NewLine
Set-Content -LiteralPath (Join-Path $Stage "README-FIRST.txt") -Value $Readme -Encoding UTF8

$Files = Get-ChildItem -LiteralPath $Stage -Recurse -File
[pscustomobject]@{
    Stage = $Stage
    Files = $Files.Count
    Bytes = ($Files | Measure-Object -Property Length -Sum).Sum
    UiExecutable = Test-Path -LiteralPath (Join-Path $Stage "ui\Wind3D-Ninja.exe")
    CliExecutable = Test-Path -LiteralPath (Join-Path $Stage "cli\wind3d-ninja.exe")
    WindNinjaExecutable = Test-Path -LiteralPath (Join-Path $Stage "windninja\bin\WindNinja_cli.exe")
}
