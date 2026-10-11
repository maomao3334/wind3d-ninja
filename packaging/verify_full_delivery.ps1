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
$TempRoot = [IO.Path]::GetFullPath($env:TEMP).TrimEnd("\\")
$Root = [IO.Path]::GetFullPath((Join-Path $TempRoot "wind3d-ninja-full-delivery-test"))
if ([IO.Path]::GetDirectoryName($Root) -ne $TempRoot) {
    throw "Verification root is outside the designated temporary directory."
}
$InstallDir = Join-Path $Root "installed"
$OutputDir = Join-Path $Root "pipeline-output"
$MigratedRoot = Join-Path $Root "migrated"
$MigratedOutputDir = Join-Path $Root "migrated-pipeline-output"
$NonNeutralOutputDir = Join-Path $Root "non-neutral-output"
$NonNeutralConfigPath = Join-Path $Root "non-neutral-config.yaml"
$TurbulenceInputDir = Join-Path $Root "turbulence-input"
$MigratedTurbulenceInputDir = Join-Path $Root "migrated-turbulence-input"
$TurbulenceOutputDir = Join-Path $Root "turbulence-output"
$MigratedTurbulenceOutputDir = Join-Path $Root "migrated-turbulence-output"
$LogDir = Join-Path $Root "logs"

if (Test-Path -LiteralPath $Root) {
    Remove-Item -LiteralPath $Root -Recurse -Force
}
New-Item -ItemType Directory -Path $Root, $LogDir | Out-Null
Remove-Item Env:WINDNINJA_HOME -ErrorAction SilentlyContinue

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
    $process = Start-Process -FilePath $Cli -ArgumentList $argumentString -WindowStyle Hidden `
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

function Invoke-CliAt {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Executable,
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,
        [Parameter(Mandatory = $true)]
        [string]$LogName
    )
    $stdout = Join-Path $LogDir "$LogName.stdout.txt"
    $stderr = Join-Path $LogDir "$LogName.stderr.txt"
    $argumentString = ($Arguments | ForEach-Object {
        $value = [string]$_
        if ($value -match '[\s\"]') {
            '"' + ($value -replace '"', '\\"') + '"'
        }
        else {
            $value
        }
    }) -join ' '
    $process = Start-Process -FilePath $Executable -ArgumentList $argumentString -WindowStyle Hidden `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr `
        -Wait -PassThru
    $out = Get-Content -LiteralPath $stdout -Raw -ErrorAction SilentlyContinue
    $err = Get-Content -LiteralPath $stderr -Raw -ErrorAction SilentlyContinue
    if ($process.ExitCode -ne 0) {
        throw "CLI command '$LogName' failed with exit code $($process.ExitCode).`n$out`n$err"
    }
    return [pscustomobject]@{ ExitCode = $process.ExitCode; Stdout = $out; Stderr = $err }
}

$installArgs = @(
    "/VERYSILENT"
    "/SUPPRESSMSGBOXES"
    "/NORESTART"
    "/CLOSEAPPLICATIONS"
    "/DIR=$InstallDir"
    "/LOG=$(Join-Path $Root 'install.log')"
)
$installerProcess = Start-Process -FilePath $InstallerPath -ArgumentList $installArgs -WindowStyle Hidden -Wait -PassThru
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
$BundledDefaultConfig = Join-Path $InstallDir "cli\_internal\config\defaults.yaml"
if (-not (Test-Path -LiteralPath $BundledDefaultConfig -PathType Leaf)) {
    throw "Installed CLI default configuration is missing."
}
$nonNeutralConfig = Get-Content -LiteralPath $BundledDefaultConfig -Raw
if ($nonNeutralConfig -notmatch "(?m)^(\s*non_neutral_stability:\s*)false\s*$") {
    throw "Installed default configuration has no disabled non_neutral_stability setting to enable."
}
$nonNeutralConfig = [regex]::Replace(
    $nonNeutralConfig,
    "(?m)^(\s*non_neutral_stability:\s*)false\s*$",
    "`${1}true"
)
[IO.File]::WriteAllText($NonNeutralConfigPath, $nonNeutralConfig, [Text.UTF8Encoding]::new($false))

Copy-Item -LiteralPath $InstallDir -Destination $MigratedRoot -Recurse -Force
$MigratedCli = Join-Path $MigratedRoot "cli\wind3d-ninja.exe"
$MigratedUi = Join-Path $MigratedRoot "ui\Wind3D-Ninja.exe"
$MigratedWindNinja = Join-Path $MigratedRoot "windninja\bin\WindNinja_cli.exe"
foreach ($required in @($MigratedCli, $MigratedUi, $MigratedWindNinja)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Migrated installation file is missing: $required"
    }
}

$help = Invoke-Cli -Arguments @("--help") -LogName "cli-help"
$doctor = Invoke-Cli -Arguments @("doctor") -LogName "doctor"
if ($doctor.Stdout -notmatch "healthy: True") {
    throw "Installed CLI doctor did not report healthy: True.`n$($doctor.Stdout)"
}
if ($doctor.Stdout -notmatch "windninja[\\/]+bin[\\/]+WindNinja_cli\.exe") {
    throw "Installed CLI doctor did not resolve the bundled WindNinja executable."
}

$migratedDoctor = Invoke-CliAt -Executable $MigratedCli -Arguments @("doctor") -LogName "migrated-doctor"
if ($migratedDoctor.Stdout -notmatch "healthy: True") {
    $migratedDoctorText = $migratedDoctor.Stdout + $migratedDoctor.Stderr
    throw "Migrated CLI doctor failed:`n$migratedDoctorText"
}
if ($migratedDoctor.Stdout -notmatch "windninja[\\/]+bin[\\/]+WindNinja_cli\.exe") {
    throw "Migrated CLI doctor did not resolve the bundled WindNinja executable."
}
$demExtensions = @(".tif", ".tiff", ".asc")
$demCandidates = @(
    Get-ChildItem -LiteralPath $InputDir -File | Where-Object { $demExtensions -contains $_.Extension.ToLowerInvariant() }
)
if ($demCandidates.Count -eq 0) {
    $demCandidates = @(
        Get-ChildItem -LiteralPath $InputDir -Recurse -File |
            Where-Object { $demExtensions -contains $_.Extension.ToLowerInvariant() } |
            Sort-Object FullName
    )
}
if ($demCandidates.Count -eq 0) {
    throw "No DEM was found for turbulence-mode delivery verification."
}
New-Item -ItemType Directory -Path $TurbulenceInputDir, $MigratedTurbulenceInputDir | Out-Null
$turbulenceDem = Join-Path $TurbulenceInputDir ("terrain" + $demCandidates[0].Extension.ToLowerInvariant())
$migratedTurbulenceDem = Join-Path $MigratedTurbulenceInputDir ("terrain" + $demCandidates[0].Extension.ToLowerInvariant())
Copy-Item -LiteralPath $demCandidates[0].FullName -Destination $turbulenceDem
Copy-Item -LiteralPath $demCandidates[0].FullName -Destination $migratedTurbulenceDem
$turbulenceInputFilesBefore = @(
    Get-ChildItem -LiteralPath $TurbulenceInputDir -Recurse -File |
        ForEach-Object { $_.FullName.Substring($TurbulenceInputDir.Length + 1) } |
        Sort-Object
)
$turbulenceArguments = @(
    "--mode", "turbulence",
    "--background-speed-ms", "5",
    "--background-direction-deg", "270",
    "--background-height-m", "10",
    "--simulation-time", "2024-04-03T08:16:00Z",
    "--height", $Height,
    "--resolution", $Resolution
)
$turbulencePlan = Invoke-Cli -Arguments (@("plan", $TurbulenceInputDir) + $turbulenceArguments) -LogName "turbulence-plan"
$migratedTurbulencePlan = Invoke-CliAt -Executable $MigratedCli -Arguments (@("plan", $MigratedTurbulenceInputDir) + $turbulenceArguments) -LogName "migrated-turbulence-plan"
$turbulenceRun = Invoke-Cli -Arguments (@("run", $TurbulenceInputDir) + $turbulenceArguments + @("--output", $TurbulenceOutputDir)) -LogName "turbulence-run"
$turbulenceInputFilesAfter = @(
    Get-ChildItem -LiteralPath $TurbulenceInputDir -Recurse -File |
        ForEach-Object { $_.FullName.Substring($TurbulenceInputDir.Length + 1) } |
        Sort-Object
)
if ($null -ne (Compare-Object $turbulenceInputFilesBefore $turbulenceInputFilesAfter)) {
    throw "Turbulence mode modified the DEM-only input directory."
}
$migratedTurbulenceRun = Invoke-CliAt -Executable $MigratedCli -Arguments (@("run", $MigratedTurbulenceInputDir) + $turbulenceArguments + @("--output", $MigratedTurbulenceOutputDir)) -LogName "migrated-turbulence-run"
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

$nonNeutralRun = Invoke-Cli -Arguments @(
    "run", $InputDir,
    "--config", $NonNeutralConfigPath,
    "--height", $Height,
    "--resolution", $Resolution,
    "--time-range", $TimeStart, $TimeStart,
    "--output", $NonNeutralOutputDir
) -LogName "non-neutral-run"

$migratedRun = Invoke-CliAt -Executable $MigratedCli -Arguments @(
    "run", $InputDir,
    "--height", $Height,
    "--resolution", $Resolution,
    "--time-range", $TimeStart, $TimeEnd,
    "--output", $MigratedOutputDir
) -LogName "migrated-run"
$migratedNcFiles = @(Get-ChildItem -LiteralPath (Join-Path $MigratedOutputDir "nc") -Filter "*.nc" -File -ErrorAction SilentlyContinue)
$migratedKmzFiles = @(Get-ChildItem -LiteralPath (Join-Path $MigratedOutputDir "kmz") -Filter "*.kmz" -File -ErrorAction SilentlyContinue)
if ($migratedNcFiles.Count -lt 1 -or $migratedKmzFiles.Count -lt 1) {
    throw "Migrated CLI run did not produce both NetCDF and KMZ output."
}

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

$nonNeutralNcFiles = @(Get-ChildItem -LiteralPath (Join-Path $NonNeutralOutputDir "nc") -Filter "*.nc" -File -ErrorAction SilentlyContinue)
$nonNeutralKmzFiles = @(Get-ChildItem -LiteralPath (Join-Path $NonNeutralOutputDir "kmz") -Filter "*.kmz" -File -ErrorAction SilentlyContinue)
if ($nonNeutralNcFiles.Count -lt 1 -or $nonNeutralKmzFiles.Count -lt 1) {
    throw "Non-neutral station run did not produce both NetCDF and KMZ output."
}

$turbulenceNcFiles = @(Get-ChildItem -LiteralPath (Join-Path $TurbulenceOutputDir "nc") -Filter "*.nc" -File -ErrorAction SilentlyContinue)
$turbulenceKmzFiles = @(Get-ChildItem -LiteralPath (Join-Path $TurbulenceOutputDir "kmz") -Filter "*.kmz" -File -ErrorAction SilentlyContinue)
if ($turbulenceNcFiles.Count -lt 1 -or $turbulenceKmzFiles.Count -lt 1) {
    throw "Turbulence mode did not produce both NetCDF and KMZ output."
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$turbulenceLayerVerified = $false
foreach ($kmzFile in $turbulenceKmzFiles) {
    $archive = [IO.Compression.ZipFile]::OpenRead($kmzFile.FullName)
    try {
        $kmlEntry = $archive.Entries | Where-Object { $_.Name -match "\.kml$" } | Select-Object -First 1
        $legendEntry = $archive.Entries | Where-Object { $_.Name -ieq "colMax_legend" } | Select-Object -First 1
        if ($null -eq $kmlEntry) {
            continue
        }
        $reader = New-Object IO.StreamReader($kmlEntry.Open())
        try {
            $kml = $reader.ReadToEnd()
        }
        finally {
            $reader.Dispose()
        }
        if ($kml -match "Column Max Velocity Fluctuations" -and $null -ne $legendEntry -and $legendEntry.Length -gt 0) {
            $turbulenceLayerVerified = $true
            break
        }
    }
    finally {
        $archive.Dispose()
    }
}
if (-not $turbulenceLayerVerified) {
    throw "Turbulence KMZ is missing the native Column Max Velocity Fluctuations layer or legend."
}

$migratedTurbulenceNcFiles = @(Get-ChildItem -LiteralPath (Join-Path $MigratedTurbulenceOutputDir "nc") -Filter "*.nc" -File -ErrorAction SilentlyContinue)
$migratedTurbulenceKmzFiles = @(Get-ChildItem -LiteralPath (Join-Path $MigratedTurbulenceOutputDir "kmz") -Filter "*.kmz" -File -ErrorAction SilentlyContinue)
if ($migratedTurbulenceNcFiles.Count -lt 1 -or $migratedTurbulenceKmzFiles.Count -lt 1) {
    throw "Migrated turbulence mode did not produce both NetCDF and KMZ output."
}
$migratedTurbulenceLayerVerified = $false
foreach ($kmzFile in $migratedTurbulenceKmzFiles) {
    $archive = [IO.Compression.ZipFile]::OpenRead($kmzFile.FullName)
    try {
        $kmlEntry = $archive.Entries | Where-Object { $_.Name -match "\.kml$" } | Select-Object -First 1
        $legendEntry = $archive.Entries | Where-Object { $_.Name -ieq "colMax_legend" } | Select-Object -First 1
        if ($null -eq $kmlEntry) {
            continue
        }
        $reader = New-Object IO.StreamReader($kmlEntry.Open())
        try {
            $kml = $reader.ReadToEnd()
        }
        finally {
            $reader.Dispose()
        }
        if ($kml -match "Column Max Velocity Fluctuations" -and $null -ne $legendEntry -and $legendEntry.Length -gt 0) {
            $migratedTurbulenceLayerVerified = $true
            break
        }
    }
    finally {
        $archive.Dispose()
    }
}
if (-not $migratedTurbulenceLayerVerified) {
    throw "Migrated turbulence KMZ is missing the native Column Max Velocity Fluctuations layer or legend."
}

$versionStdout = Join-Path $LogDir "windninja-version.stdout.txt"
$versionStderr = Join-Path $LogDir "windninja-version.stderr.txt"
$versionProcess = Start-Process -FilePath $WindNinja -ArgumentList "--version" -WindowStyle Hidden `
    -RedirectStandardOutput $versionStdout -RedirectStandardError $versionStderr `
    -Wait -PassThru
$version = ((Get-Content -LiteralPath $versionStdout -Raw -ErrorAction SilentlyContinue) +
    (Get-Content -LiteralPath $versionStderr -Raw -ErrorAction SilentlyContinue))
if ($version -notmatch "WindNinja version: 3\.12\.2") {
    throw "Bundled WindNinja version check failed:`n$version"
}

$uiProcess = Start-Process -FilePath $Ui -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 8
$uiAlive = -not $uiProcess.HasExited
if ($uiAlive) {
    Stop-Process -Id $uiProcess.Id -Force
}

$migratedUiProcess = Start-Process -FilePath $MigratedUi -WindowStyle Hidden -PassThru
Start-Sleep -Seconds 8
$migratedUiAlive = -not $migratedUiProcess.HasExited
if ($migratedUiAlive) {
    Stop-Process -Id $migratedUiProcess.Id -Force
}
if (-not $migratedUiAlive) {
    throw "Migrated UI exited before the startup check completed."
}
if (-not $uiAlive) {
    throw "Installed UI exited before the startup check completed."
}

$uninstaller = Join-Path $InstallDir "unins000.exe"
if (-not (Test-Path -LiteralPath $uninstaller -PathType Leaf)) {
    throw "Uninstaller is missing: $uninstaller"
}
$uninstallProcess = Start-Process -FilePath $uninstaller -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -WindowStyle Hidden -Wait -PassThru
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
    NonNeutralRunPassed = $true
    NonNeutralNetCdfFiles = $nonNeutralNcFiles.Count
    NonNeutralKmzFiles = $nonNeutralKmzFiles.Count
    MigratedDoctorHealthy = $true
    MigratedRunPassed = $true
    TurbulencePlanPassed = $true
    MigratedTurbulencePlanPassed = $true
    TurbulenceRunPassed = $true
    MigratedTurbulenceRunPassed = $true
    TurbulenceNetCdfFiles = $turbulenceNcFiles.Count
    TurbulenceKmzFiles = $turbulenceKmzFiles.Count
    TurbulenceKmzLayerVerified = $turbulenceLayerVerified
    MigratedTurbulenceNetCdfFiles = $migratedTurbulenceNcFiles.Count
    MigratedTurbulenceKmzFiles = $migratedTurbulenceKmzFiles.Count
    MigratedTurbulenceKmzLayerVerified = $migratedTurbulenceLayerVerified
    MigratedUiStarted = $true
    MigratedNetCdfFiles = $migratedNcFiles.Count
    MigratedKmzFiles = $migratedKmzFiles.Count
    NetCdfFiles = $ncFiles.Count
    KmzFiles = $kmzFiles.Count
    WindNinjaVersion = "3.12.2"
    UiStarted = $true
    Uninstalled = $true
    Logs = $LogDir
} | Format-List
