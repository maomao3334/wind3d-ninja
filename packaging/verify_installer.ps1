param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath
)

$ErrorActionPreference = "Stop"

$InstallerPath = (Resolve-Path -LiteralPath $InstallerPath).Path
$Root = Join-Path $env:TEMP "wind3d-ninja-installer-test"
$InstallDir = Join-Path $Root "installed"
$OutputDir = Join-Path $Root "installer-output"

if (Test-Path -LiteralPath $Root) {
    Remove-Item -LiteralPath $Root -Recurse -Force
}
New-Item -ItemType Directory -Path $Root | Out-Null

$installArgs = @(
    "/VERYSILENT"
    "/SUPPRESSMSGBOXES"
    "/NORESTART"
    "/CLOSEAPPLICATIONS"
    "/DIR=$InstallDir"
    "/LOG=$($Root)\install.log"
)
$installerProcess = Start-Process -FilePath $InstallerPath -ArgumentList $installArgs -Wait -PassThru
if ($installerProcess.ExitCode -ne 0) {
    throw "Wind3D Ninja installer failed with exit code $($installerProcess.ExitCode)."
}

$Ui = Join-Path $InstallDir "ui\Wind3D-Ninja.exe"
$Cli = Join-Path $InstallDir "cli\wind3d-ninja.exe"
$WindNinja = Join-Path $InstallDir "windninja\bin\WindNinja_cli.exe"
foreach ($Required in @($Ui, $Cli, $WindNinja)) {
    if (-not (Test-Path -LiteralPath $Required -PathType Leaf)) {
        throw "Installed file is missing: $Required"
    }
}

$help = & $Cli --help 2>&1 | Out-String
if ($LASTEXITCODE -ne 0 -or $help -notmatch "doctor") {
    throw "Installed CLI --help failed."
}

$doctor = & $Cli doctor 2>&1 | Out-String
if ($LASTEXITCODE -ne 0 -or $doctor -notmatch "healthy: True") {
    throw "Installed CLI doctor failed:`n$doctor"
}

$versionStdout = Join-Path $Root "windninja-version.stdout"
$versionStderr = Join-Path $Root "windninja-version.stderr"
$versionProcess = Start-Process -FilePath $WindNinja -ArgumentList "--version" `
    -RedirectStandardOutput $versionStdout -RedirectStandardError $versionStderr `
    -Wait -PassThru
$version = ((Get-Content -LiteralPath $versionStdout -Raw -ErrorAction SilentlyContinue) +
    (Get-Content -LiteralPath $versionStderr -Raw -ErrorAction SilentlyContinue))
if ($version -notmatch "WindNinja version: 3\.12\.2") {
    throw "Bundled WindNinja version check failed:`n$version"
}

$uiProcess = Start-Process -FilePath $Ui -PassThru
Start-Sleep -Seconds 8
$uiAlive = -not $uiProcess.HasExited
if ($uiAlive) {
    Stop-Process -Id $uiProcess.Id -Force
}
if (-not $uiAlive) {
    throw "Installed UI exited before the startup check completed."
}

$uninstaller = Join-Path $InstallDir "unins000.exe"
if (-not (Test-Path -LiteralPath $uninstaller -PathType Leaf)) {
    throw "Uninstaller is missing: $uninstaller"
}
$uninstallProcess = Start-Process -FilePath $uninstaller -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait -PassThru
if ($uninstallProcess.ExitCode -ne 0) {
    throw "Uninstaller failed with exit code $($uninstallProcess.ExitCode)."
}
if (Test-Path -LiteralPath $InstallDir) {
    throw "Install directory still exists after uninstall: $InstallDir"
}

[pscustomobject]@{
    Installer = $InstallerPath
    InstalledCli = $true
    DoctorHealthy = $true
    WindNinjaVersion = "3.12.2"
    UiStarted = $true
    Uninstalled = $true
} | Format-List
