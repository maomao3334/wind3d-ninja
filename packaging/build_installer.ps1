param(
    [string]$AppDir,
    [string]$IsccPath
)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($AppDir)) { $AppDir = Join-Path $ProjectRoot "dist\Wind3D" }
if ([string]::IsNullOrWhiteSpace($IsccPath)) {
    $Compiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($Compiler) { $IsccPath = $Compiler.Source }
    else {
        foreach ($Candidate in @(
            (Join-Path $ProjectRoot "build/tools\InnoSetup\ISCC.exe"),
            (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
            (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe")
        )) {
            if (Test-Path -LiteralPath $Candidate -PathType Leaf) { $IsccPath = $Candidate; break }
        }
    }
}
if (-not $IsccPath -or -not (Test-Path -LiteralPath $IsccPath -PathType Leaf)) {
    throw "Inno Setup compiler not found. Pass -IsccPath with an existing ISCC.exe."
}
& (Join-Path $PSScriptRoot "stage_release.ps1") -AppDir $AppDir
$Version = (Get-Content -LiteralPath (Join-Path $AppDir "portable_app.json") -Encoding UTF8 -Raw | ConvertFrom-Json).version
if ($Version -notmatch "^\d+\.\d+\.\d+$") { throw "Invalid application version in portable_app.json." }
& $IsccPath ("/DMyAppVersion=" + $Version) (Join-Path $PSScriptRoot "installer.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed: $LASTEXITCODE" }
$Setup = Join-Path $ProjectRoot ("installer_output\Wind3D-Ninja-Setup-" + $Version + ".exe")
Get-Item -LiteralPath $Setup | Select-Object FullName, Length
$Hasher = [System.Security.Cryptography.SHA256]::Create()
$Stream = [System.IO.File]::OpenRead($Setup)
try {
    $Digest = [System.BitConverter]::ToString($Hasher.ComputeHash($Stream)).Replace("-", "").ToLowerInvariant()
    [PSCustomObject]@{ Algorithm = "SHA256"; Hash = $Digest; Path = $Setup }
}
finally {
    $Stream.Dispose()
    $Hasher.Dispose()
}
