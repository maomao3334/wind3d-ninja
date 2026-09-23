param(
    [string]$WindNinjaSource = $env:WINDNINJA_SOURCE
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($WindNinjaSource)) {
    throw "Set WINDNINJA_SOURCE to the local WindNinja 3.12.2 directory before making a release bundle."
}
if (-not (Test-Path -LiteralPath $WindNinjaSource -PathType Container)) {
    throw "WindNinja source directory was not found: $WindNinjaSource"
}
$WindNinjaSource = (Resolve-Path -LiteralPath $WindNinjaSource).Path

$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $root "release\wind3d-ninja-ui-portable"
$package = Join-Path $root "release\Wind3D-Ninja-1.0.1-Windows-x64"

if (Test-Path $package) { Remove-Item -LiteralPath $package -Recurse -Force }
Copy-Item -LiteralPath $source -Destination $package -Recurse
Copy-Item -LiteralPath $WindNinjaSource -Destination (Join-Path $package "windninja") -Recurse
Copy-Item -LiteralPath (Join-Path $root "release\wind3d-ninja.exe") -Destination $package

$readme = @"
Wind3D Ninja 1.0.1

1. Double-click wind3d-ninja-ui.exe to start.
2. WindNinja 3.12.2 is already included in the windninja folder.
3. Do not move only the EXE. Keep the complete folder together.
"@
Set-Content -LiteralPath (Join-Path $package "README-FIRST.txt") -Value $readme -Encoding UTF8

$zip = "$package.zip"
if (Test-Path $zip) { Remove-Item -LiteralPath $zip -Force }
Compress-Archive -LiteralPath $package -DestinationPath $zip -CompressionLevel Optimal
Write-Host $zip
