[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('Daily-Ingest.ps1', 'Audit-Failed-Ingestion.ps1', 'Build-Embeddings.ps1', 'Full-Vault-Rebuild.ps1', 'Run-Rebuild-Tests.ps1', 'Start-AriadneControl.ps1')]
    [string]$Workflow,
    [hashtable]$WorkflowParameters = @{}
)

$ErrorActionPreference = 'Stop'
$ControlPlane = Join-Path (Split-Path -Parent $PSScriptRoot) 'control-plane'
$Python = $env:ARIADNE_PYTHON
if (-not $Python) { $Python = 'C:\Users\Warren\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Set ARIADNE_PYTHON to a usable Python runtime.' }
$Vault = & $Python -c 'import sys; sys.path.insert(0, sys.argv[1]); from vault_config import VAULT_ROOT; print(VAULT_ROOT)' $ControlPlane
if ($LASTEXITCODE -ne 0) { throw 'Could not resolve the configured Knowledge Vault.' }
$Target = Join-Path (Join-Path $Vault '00_System') $Workflow
if (-not (Test-Path -LiteralPath $Target -PathType Leaf)) { throw "Current v2 Vault workflow not found: $Target" }
if ((Resolve-Path -LiteralPath $Target).Path -eq (Join-Path $PSScriptRoot $Workflow)) { throw 'The configured Vault must be the maintained external v2 Vault, not this historical compatibility tree.' }
$env:ARIADNE_VAULT_ROOT = $Vault
& $Target @WorkflowParameters
exit $LASTEXITCODE
