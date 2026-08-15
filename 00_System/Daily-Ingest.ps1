[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$Vault = Split-Path -Parent $PSScriptRoot
Push-Location $Vault
try {
    & py -3 (Join-Path $PSScriptRoot 'daily_rebuild_ingest.py') --vault $Vault
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
