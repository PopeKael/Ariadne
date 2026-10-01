[CmdletBinding()]
param([string]$Stamp = (Get-Date -Format 'yyyyMMdd'), [switch]$DryRun)

$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'Invoke-VaultV2.ps1') -Workflow 'Audit-Failed-Ingestion.ps1' -WorkflowParameters @{ Stamp = $Stamp; DryRun = $DryRun }
exit $LASTEXITCODE

# Historical implementation below is unreachable; current operations delegate above.

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
