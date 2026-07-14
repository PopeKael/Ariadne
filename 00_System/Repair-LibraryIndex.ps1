param(
    [string]$Vault = (Split-Path $PSScriptRoot -Parent),
    [string]$SourceName = 'New chat2026-07-12T18_40_25+07_00.duplicate.md'
)

$libraryPath = Join-Path $Vault '00_System\library.json'
$entries = @(Get-Content -LiteralPath $libraryPath -Raw | ConvertFrom-Json)
$matches = @($entries | Where-Object {$_.source_name -eq $SourceName})
if ($matches.Count -ne 1) { throw "Expected one matching library entry for '$SourceName'; found $($matches.Count)." }
$retained = @($entries | Where-Object {$_.source_name -ne $SourceName})
$retained | ConvertTo-Json -Depth 8 | Out-File -LiteralPath $libraryPath -Encoding utf8 -NoNewline
Write-Host "Removed dangling library entry: $SourceName"
