[CmdletBinding()]
param()

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
