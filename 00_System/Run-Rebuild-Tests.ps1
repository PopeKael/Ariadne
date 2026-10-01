[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'Invoke-VaultV2.ps1') -Workflow 'Run-Rebuild-Tests.ps1'
exit $LASTEXITCODE

# Historical implementation below is unreachable; current operations delegate above.

$ErrorActionPreference = 'Stop'
$Vault = Split-Path -Parent $PSScriptRoot
Push-Location $PSScriptRoot
try {
    & py -3 -m unittest `
        (Join-Path $PSScriptRoot 'test_ollama_adapter.py') `
        (Join-Path $PSScriptRoot 'test_rebuild_lock.py') `
        (Join-Path $PSScriptRoot 'test_rebuild_foundation.py') `
        (Join-Path $PSScriptRoot 'test_rebuild_safeguards.py') `
        (Join-Path $PSScriptRoot 'test_citations.py') `
        (Join-Path $PSScriptRoot 'test_retrieval_evaluation.py')
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
