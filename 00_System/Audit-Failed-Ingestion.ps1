[CmdletBinding()]
param([string]$Stamp = (Get-Date -Format 'yyyyMMdd'))

$ErrorActionPreference = 'Stop'
$Vault = Split-Path -Parent $PSScriptRoot
Push-Location $Vault
try {
    & py -3 (Join-Path $PSScriptRoot 'audit_failed_ingestion.py') --vault $Vault --stamp $Stamp
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
