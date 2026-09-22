param(
    [Parameter(Mandatory = $true)]
    [string]$InstallerPath,
    [Parameter(Mandatory = $true)]
    [string]$InputDir,
    [string]$TimeStart = "2024-04-03 16:33",
    [string]$TimeEnd = "2024-04-03 16:33",
    [int]$Height = 10,
    [int]$Resolution = 200
)

$ErrorActionPreference = "Stop"

$InstallerPath = (Resolve-Path -LiteralPath $InstallerPath).Path
$InputDir = (Resolve-Path -LiteralPath $InputDir).Path
$Root = Join-Path $env:TEMP "wind3d-ninja-full-delivery-test"
$InstallDir = Join-Path $Root "installed"
$OutputDir = Join-Path $Root "pipeline-output"
$LogDir = Join-Path $Root "logs"

if (Test-Path -LiteralPath $Root) {
    Remove-Item -LiteralPath $Root -Recurse -Force
}
New-Item -ItemType Directory -Path $Root, $LogDir | Out-Null

function Invoke-Cli {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [Parameter(Mandatory = $true)]
        [string]$LogName
    )

    $stdout = Join-Path $LogDir "$LogName.stdout.txt"
    $stderr = Join-Path $LogDir "$LogName.stderr.txt"
    $argumentString = ($Arguments | ForEach-Object {
        $value = [string]$_
        if ($value -match '[\s"]') {
            '"' + ($value -replace '"', '\\"') + '"'
        }
        else {
            $value
        }
    }) -join ' '
    $process = Start-Process -FilePath $Cli -ArgumentList $argumentString `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr `
        -Wait -PassThru
    $out = Get-Content -LiteralPath $stdout -Raw -ErrorAction SilentlyContinue
    $err = Get-Content -LiteralPath $stderr -Raw -ErrorAction SilentlyContinue
    if ($process.ExitCode -ne 0) {
        throw "CLI command '$LogName' failed with exit code $($process.ExitCode).`n$out`n$err"
    }
    return [pscustomobject]@{
        ExitCode = $process.ExitCode
        Stdout = $out
        Stderr = $err
    }
}

$installArgs = @(
    "/VERYSILENT"
    "/SUPPRESSMSGBOXES"
    "/NORESTART"
    "/CLOSEAPPLICATIONS"
    "/DIR=$InstallDir"
    "/LOG=$(Join-Path $Root 'install.log')"
)
$installerProcess = Start-Process -FilePath $InstallerPath -ArgumentList $installArgs -Wait -PassThru
if ($installerProcess.ExitCode -ne 0) {
    throw "Wind3D Ninja installer failed with exit code $($installerProcess.ExitCode)."
}

$Ui = Join-Path $InstallDir "ui\Wind3D-Ninja.exe"
$Cli = Join-Path $InstallDir "cli\wind3d-ninja.exe"
$WindNinja = Join-Path $InstallDir "windninja\bin\WindNinja_cli.exe"
foreach ($required in @($Ui, $Cli, $WindNinja)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Installed file is missing: $required"
    }
}

$help = Invoke-Cli -Arguments @("--help") -LogName "cli-help"
$doctor = Invoke-Cli -Arguments @("doctor") -LogName "doctor"
if ($doctor.Stdout -notmatch "healthy: True") {
    throw "Installed CLI doctor did not report healthy: True.`n$($doctor.Stdout)"
}
$inspect = Invoke-Cli -Arguments @("inspect", $InputDir) -LogName "inspect"
$plan = Invoke-Cli -Arguments @(
    "plan", $InputDir,
    "--height", $Height,
    "--resolution", $Resolution,
    "--time-range", $TimeStart, $TimeEnd
) -LogName "plan"

$run = Invoke-Cli -Arguments @(
    "run", $InputDir,
    "--height", $Height,
    "--resolution", $Resolution,
    "--time-range", $TimeStart, $TimeEnd,
    "--output", $OutputDir
) -LogName "run"

$ncFiles = @(Get-ChildItem -LiteralPath (Join-Path $OutputDir "nc") -Filter "*.nc" -File -ErrorAction SilentlyContinue)
$kmzFiles = @(Get-ChildItem -LiteralPath (Join-Path $OutputDir "kmz") -Filter "*.kmz" -File -ErrorAction SilentlyContinue)
if ($ncFiles.Count -lt 1) {
    throw "Pipeline completed but no NetCDF files were produced."
}
if ($kmzFiles.Count -lt 1) {
    throw "Pipeline completed but no KMZ files were produced."
}
if (-not (Test-Path -LiteralPath (Join-Path $OutputDir "manifest.json") -PathType Leaf)) {
    throw "Pipeline completed but manifest.json was not produced."
}

$versionStdout = Join-Path $LogDir "windninja-version.stdout.txt"
$versionStderr = Join-Path $LogDir "windninja-version.stderr.txt"
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
    InputDir = $InputDir
    DoctorHealthy = $true
    InspectPassed = $true
    PlanPassed = $true
    RunPassed = $true
    NetCdfFiles = $ncFiles.Count
    KmzFiles = $kmzFiles.Count
    WindNinjaVersion = "3.12.2"
    UiStarted = $true
    Uninstalled = $true
    Logs = $LogDir
} | Format-List
