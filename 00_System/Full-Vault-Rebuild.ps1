[CmdletBinding()]
param([switch]$DryRun)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'Invoke-VaultV2.ps1') -Workflow 'Full-Vault-Rebuild.ps1' -WorkflowParameters @{ DryRun = $DryRun }
exit $LASTEXITCODE
