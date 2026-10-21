param(
    [Parameter(Mandatory=$true)][string]$WindNinjaSource,
    [Parameter(Mandatory=$true)][string]$OpenFoamRuntime,
    [Parameter(Mandatory=$true)][string]$NativeSourceArchive,
    [string]$IsccPath
)
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "build_windows.ps1") -WindNinjaSource $WindNinjaSource -OpenFoamRuntime $OpenFoamRuntime -NativeSourceArchive $NativeSourceArchive
& (Join-Path $PSScriptRoot "build_installer.ps1") -IsccPath $IsccPath
