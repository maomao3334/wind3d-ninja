$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$InnoRoot = Join-Path $ProjectRoot "installer_build\innosetup"
$InnoInstaller = Join-Path $ProjectRoot "installer_build\innosetup-7.1.0-x64.exe"
$Iscc = Join-Path $InnoRoot "ISCC.exe"
$OutputDir = Join-Path $ProjectRoot "installer_output"

if (-not (Test-Path -LiteralPath $Iscc -PathType Leaf)) {
    New-Item -ItemType Directory -Path (Split-Path $InnoInstaller) -Force | Out-Null
    if (-not (Test-Path -LiteralPath $InnoInstaller -PathType Leaf)) {
        Invoke-WebRequest `
            -Uri "https://github.com/jrsoftware/issrc/releases/download/is-7_1_0/innosetup-7.1.0-x64.exe" `
            -OutFile $InnoInstaller
    }
    $installArgs = @(
        "/VERYSILENT"
        "/SUPPRESSMSGBOXES"
        "/NORESTART"
        ("/DIR=" + $InnoRoot)
    )
    $installProcess = Start-Process -FilePath $InnoInstaller -ArgumentList $installArgs -Wait -PassThru
    if ($installProcess.ExitCode -ne 0) {
        throw "Inno Setup bootstrap installer failed with exit code $($installProcess.ExitCode)."
    }
}

if (-not (Test-Path -LiteralPath $Iscc -PathType Leaf)) {
    throw "ISCC.exe was not found after Inno Setup installation: $Iscc"
}

& $Iscc (Join-Path $ProjectRoot "packaging\installer.iss")
if ($LASTEXITCODE -ne 0) {
    throw "ISCC failed with exit code $LASTEXITCODE."
}

$Artifact = Join-Path $OutputDir "Wind3D-Ninja-Setup-1.0.1-x64.exe"
if (-not (Test-Path -LiteralPath $Artifact -PathType Leaf)) {
    throw "Installer artifact was not created: $Artifact"
}

$File = Get-Item -LiteralPath $Artifact
$sha256 = [System.Security.Cryptography.SHA256]::Create()
$stream = [System.IO.File]::OpenRead($Artifact)
try {
    $Hash = ([System.BitConverter]::ToString($sha256.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
}
finally {
    $stream.Dispose()
    $sha256.Dispose()
}
[pscustomobject]@{
    Artifact = $File.FullName
    Bytes = $File.Length
    SHA256 = $Hash
    InnoCompiler = $Iscc
} | Format-List
