param(
    [Parameter(Mandatory=$true)][string]$InstallerPath
)
$ErrorActionPreference = "Stop"
# Runtime startup, bundled dependency discovery and uninstall use the installed product.
# Scientific full-solve acceptance also needs the user's terrain/observation fixtures.
& (Join-Path $PSScriptRoot "verify_installer.ps1") -InstallerPath $InstallerPath
