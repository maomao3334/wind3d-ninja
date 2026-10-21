param(
    [string]$AppDir,
    [string]$StageDir
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($AppDir)) {
    $AppDir = Join-Path $ProjectRoot "dist\Wind3D"
}
if ([string]::IsNullOrWhiteSpace($StageDir)) {
    $StageDir = Join-Path $ProjectRoot "installer_stage"
}

$AppDir = (Resolve-Path -LiteralPath $AppDir).Path
$StageDir = [IO.Path]::GetFullPath($StageDir)
$Required = @(
    "Wind3D.exe",
    "Wind3D-cli.exe",
    "_internal\config\defaults.yaml",
    "runtime\windninja\3.12.2\bin\WindNinja_cli.exe",
    "runtime\openfoam8\native_runtime.json",
    "native-corresponding-source.tar.gz",
    "licenses\Wind3D-LICENSE.txt",
    "docs\user_guide_zh.md",
    "portable_app.json"
)
foreach ($RelativePath in $Required) {
    $Path = Join-Path $AppDir $RelativePath
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Frozen application is missing required installer content: $Path"
    }
}

$PortableManifest = Get-Content -LiteralPath (Join-Path $AppDir "portable_app.json") -Raw | ConvertFrom-Json
if ($PortableManifest.platform -ne "windows-native" -or
    $PortableManifest.requires_wsl -ne $false -or
    $PortableManifest.requires_system_python -ne $false -or
    $PortableManifest.ui -ne "Wind3D.exe" -or
    $PortableManifest.cli -ne "Wind3D-cli.exe" -or
    $PortableManifest.local_default_config_included -ne $true) {
    throw "portable_app.json does not describe the expected self-contained Windows Wind3D application."
}

$WorkspacePrefix = [IO.Path]::GetFullPath($ProjectRoot).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $StageDir.StartsWith($WorkspacePrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Installer staging must stay inside this project workspace."
}
if (Test-Path -LiteralPath $StageDir) {
    Remove-Item -LiteralPath $StageDir -Recurse -Force
}
New-Item -ItemType Directory -Path $StageDir | Out-Null
Copy-Item -LiteralPath $AppDir -Destination $StageDir -Recurse
$StagedApp = Join-Path $StageDir "Wind3D"

$Files = Get-ChildItem -LiteralPath $StagedApp -Recurse -File
[pscustomobject]@{
    Stage = $StagedApp
    Files = $Files.Count
    Bytes = ($Files | Measure-Object -Property Length -Sum).Sum
    GuiExecutable = Test-Path -LiteralPath (Join-Path $StagedApp "Wind3D.exe")
    CliExecutable = Test-Path -LiteralPath (Join-Path $StagedApp "Wind3D-cli.exe")
    WindNinjaExecutable = Test-Path -LiteralPath (Join-Path $StagedApp "runtime\windninja\3.12.2\bin\WindNinja_cli.exe")
    NativeRuntime = Test-Path -LiteralPath (Join-Path $StagedApp "runtime\openfoam8\native_runtime.json")
    SourceArchive = Test-Path -LiteralPath (Join-Path $StagedApp "native-corresponding-source.tar.gz")
} | Format-List
