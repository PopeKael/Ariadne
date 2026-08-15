[CmdletBinding()]
param([switch]$Rebuild, [switch]$Status, [string]$Model)

if ($Rebuild -and $Status) { throw 'Use either -Rebuild or -Status, not both.' }
$Arguments = @('-3', (Join-Path $PSScriptRoot 'build_embeddings.py'))
if ($Rebuild) { $Arguments += '--rebuild' }
if ($Status) { $Arguments += '--status' }
if ($Model) { $Arguments += @('--model', $Model) }
& py @Arguments
exit $LASTEXITCODE
