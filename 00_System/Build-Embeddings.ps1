[CmdletBinding()]
param([switch]$Rebuild, [switch]$Status, [switch]$DryRun, [string]$Model)

$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'Invoke-VaultV2.ps1') -Workflow 'Build-Embeddings.ps1' -WorkflowParameters @{ Rebuild = $Rebuild; Status = $Status; DryRun = $DryRun; Model = $Model }
exit $LASTEXITCODE

# Historical implementation below is unreachable; current operations delegate above.

if ($Rebuild -and $Status) { throw 'Use either -Rebuild or -Status, not both.' }
$Arguments = @('-3', (Join-Path $PSScriptRoot 'build_embeddings.py'))
if ($Rebuild) { $Arguments += '--rebuild' }
if ($Status) { $Arguments += '--status' }
if ($Model) { $Arguments += @('--model', $Model) }
& py @Arguments
exit $LASTEXITCODE
