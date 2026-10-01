[CmdletBinding()]
param([switch]$DryRun)

$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'Invoke-VaultV2.ps1') -Workflow 'Daily-Ingest.ps1' -WorkflowParameters @{ DryRun = $DryRun }
exit $LASTEXITCODE

# Historical implementation below is unreachable; current operations delegate above.

$ErrorActionPreference = 'Stop'
$Vault = if ($env:ARIADNE_VAULT_ROOT) { (Resolve-Path -LiteralPath $env:ARIADNE_VAULT_ROOT).Path } else { Split-Path -Parent $PSScriptRoot }
$env:ARIADNE_VAULT_ROOT = $Vault
Push-Location $Vault
try {
    & py -3 (Join-Path $PSScriptRoot 'daily_rebuild_ingest.py') --vault $Vault
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
