param(
    [Parameter(Mandatory=$true)][string]$InstallerPath,
    [string]$InstallDir,
    [switch]$KeepInstalled
)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$InstallerPath = (Resolve-Path -LiteralPath $InstallerPath).Path
$Root = Join-Path $ProjectRoot ("output\installer_verification\" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $Root -Force | Out-Null
if (-not $InstallDir) { $InstallDir = Join-Path $Root "installed app" }
$InstallDir = [IO.Path]::GetFullPath($InstallDir)
$WorkspacePrefix = [IO.Path]::GetFullPath($ProjectRoot).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $InstallDir.StartsWith($WorkspacePrefix,[StringComparison]::OrdinalIgnoreCase)) {
    throw "Verification installation must stay inside this project."
}
if (Test-Path -LiteralPath $InstallDir) { throw "Verification needs a new installation directory." }
$Args = @("/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/SP-","/NOICONS","/TASKS=","/NOCLOSEAPPLICATIONS","/DIR=`"$InstallDir`"","/LOG=`"$Root\install.log`"")
$Process = Start-Process -FilePath $InstallerPath -ArgumentList $Args -WindowStyle Hidden -Wait -PassThru
if ($Process.ExitCode -ne 0) { throw "Installation failed: $($Process.ExitCode)" }
$Cli = Join-Path $InstallDir "Wind3D-cli.exe"
$Gui = Join-Path $InstallDir "Wind3D.exe"
$PreviousPath = $env:PATH
$PreviousHome = $env:WINDNINJA_HOME
$PreviousBin = $env:WINDNINJA_BIN
$PreviousQt = $env:QT_QPA_PLATFORM
try {
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    $env:WINDNINJA_HOME = ""
    $env:WINDNINJA_BIN = ""
    & $Cli --json doctor | Out-File (Join-Path $Root "doctor.json") -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw "Installed dependency check failed." }
    & $Cli native-assimilation --help | Out-File (Join-Path $Root "native_help.txt") -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw "Native mode help failed." }
    $env:QT_QPA_PLATFORM = "offscreen"
    $GuiProcess = Start-Process -FilePath $Gui -WorkingDirectory $InstallDir -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 5
    if ($GuiProcess.HasExited) { throw "Frozen GUI exited: $($GuiProcess.ExitCode)" }
    Stop-Process -Id $GuiProcess.Id
}
finally {
    $env:PATH = $PreviousPath
    $env:WINDNINJA_HOME = $PreviousHome
    $env:WINDNINJA_BIN = $PreviousBin
    $env:QT_QPA_PLATFORM = $PreviousQt
}
$Uninstalled = $false
if (-not $KeepInstalled) {
    $Process = Start-Process -FilePath (Join-Path $InstallDir "unins000.exe") -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART" -WindowStyle Hidden -Wait -PassThru
    if ($Process.ExitCode -ne 0 -or (Test-Path -LiteralPath $Gui)) { throw "Uninstall verification failed." }
    $Uninstalled = $true
}
$Result = [pscustomobject]@{passed=$true;installer=$InstallerPath;install_dir=$InstallDir;isolated_path=$true;gui_startup_seconds=5;uninstalled=$Uninstalled;fresh_machine=$false}
$Result | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Root "verification.json") -Encoding utf8
$Result | ConvertTo-Json
