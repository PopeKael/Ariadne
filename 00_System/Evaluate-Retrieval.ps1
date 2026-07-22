[CmdletBinding()]
param([int]$Limit = 5)

$ErrorActionPreference = 'Stop'
$Vault = Split-Path -Parent $PSScriptRoot
Push-Location $Vault
try {
    & py -3 (Join-Path $PSScriptRoot 'evaluate_retrieval.py') --limit $Limit
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
