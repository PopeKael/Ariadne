[CmdletBinding()]
param([switch]$DryRun, [switch]$Pause, [string]$ResumeRun, [string]$ControlFile, [switch]$Background)

$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'Invoke-VaultV2.ps1') -Workflow 'Daily-Ingest.ps1' -WorkflowParameters @{ DryRun = $DryRun; Pause = $Pause; ResumeRun = $ResumeRun; ControlFile = $ControlFile; Background = $Background }
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
