param(
    [string]$Vault = (Split-Path $PSScriptRoot -Parent),
    [int]$BatchSize = 0,
    [switch]$Force
)

$libraryPath = Join-Path $Vault '00_System\library.json'; $statePath = Join-Path $Vault 'Logs\GraphMigrationState.json'
$reportPath = Join-Path $Vault 'Reports\Legacy-Graph-Migration.md'; $conceptRoot = Join-Path $Vault 'Wiki\Concepts'; $entityRoot = Join-Path $Vault 'Entities'
$version = '2.5.2'; foreach($d in @($conceptRoot,$entityRoot)){if(!(Test-Path $d)){New-Item -ItemType Directory -Path $d|Out-Null}}
function Norm([string]$s){if(!$s){return ''}; return (($s.ToLowerInvariant() -replace '[^a-z0-9 ]',' ') -replace '\s+',' ').Trim()}
function Arr($v){return @($v | Where-Object {$_ -and "$_".Trim()})}
function NoteTokens($e){
  $stop='about','after','also','and','are','been','between','could','does','from','have','into','its','more','note','notes','that','the','their','this','using','with','your'
  $raw = @((Arr ($e.tags))) + @((Arr ($e.subtopics))) + @((Arr ($e.links))) + @(("$($e.summary) $($e.map_entry) $($e.page_title)" -split '[^A-Za-z0-9]+' | Where-Object {$_.Length -ge 5 -and $stop -notcontains $_.ToLowerInvariant()}))
  return @($raw | ForEach-Object { Norm "$_" } | Where-Object {$_} | Select-Object -Unique)
}
function Get-Entities($e,[string]$markdown){
  $found=[System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
  foreach($x in Arr $e.entities){[void]$found.Add("$x")}
  foreach($m in [regex]::Matches($markdown,'\[\[([^\]|#]+)')){ $n=$m.Groups[1].Value.Trim(); if($n -match '^[A-Z][A-Za-z0-9&. -]{2,80}$'){[void]$found.Add($n)} }
  # Existing metadata is authoritative; this conservative fallback avoids inventing entity pages from prose.
  return @($found | Where-Object {$_ -notmatch '\.md$'} | Sort-Object)
}
function Add-HubLink([string]$root,[string]$name,[string]$source,[ref]$created,[ref]$backlinks){
  $key=Norm $name;if(!$key){return};$path=Join-Path $root "$key.md";$marker="- [[$source]]"
  if(!(Test-Path $path)){"# $name`n"|Out-File -LiteralPath $path -Encoding utf8; $created.Value++}
  $body=Get-Content -LiteralPath $path -Raw;if($body -notmatch [regex]::Escape($marker)){Add-Content -LiteralPath $path -Value $marker -Encoding utf8;$backlinks.Value++}
}
$entries=@(Get-Content -LiteralPath $libraryPath -Raw|ConvertFrom-Json); $state=if(Test-Path $statePath){Get-Content -Raw $statePath|ConvertFrom-Json}else{[pscustomobject]@{version='';completed=@()}}
$done=[System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase);if(!$Force -and $state.version -eq $version){foreach($n in Arr $state.completed){[void]$done.Add("$n")}}
$stats=[ordered]@{notes_migrated=0;concept_pages_created=0;entity_pages_created=0;reciprocal_links_created=0;backlinks_added=0;duplicate_entities_merged=0;failures=0};$tokens=@{};$inverted=@{}
foreach($e in $entries){$t=@(NoteTokens $e);$tokens[$e.source_name]=$t;foreach($x in $t){if(!$inverted.ContainsKey($x)){$inverted[$x]=[System.Collections.Generic.List[string]]::new()};$inverted[$x].Add($e.source_name)}}
$processed=0
foreach($e in $entries){
  if($done.Contains($e.source_name)){continue};if($BatchSize -gt 0 -and $processed -ge $BatchSize){break}
  try {
    $mdPath=Join-Path $Vault $e.processed_path;$markdown=if(Test-Path -LiteralPath $mdPath){Get-Content -LiteralPath $mdPath -Raw}else{''}
    $e.entities=@(Get-Entities $e $markdown); $e.tags=@(Arr $e.tags);$e.subtopics=@(Arr $e.subtopics);$e.links=@(Arr $e.links)
    foreach($c in @($e.tags)+@($e.subtopics)){Add-HubLink $conceptRoot "$c" $e.source_name ([ref]$stats.concept_pages_created) ([ref]$stats.backlinks_added)}
    foreach($x in @($e.entities)){Add-HubLink $entityRoot "$x" $e.source_name ([ref]$stats.entity_pages_created) ([ref]$stats.backlinks_added)}
    $scores=@{};foreach($token in $tokens[$e.source_name]){foreach($other in $inverted[$token]){if($other -ne $e.source_name){if(!$scores.ContainsKey($other)){$scores[$other]=0};$scores[$other]++}}}
    $related=@($scores.GetEnumerator()|Where-Object{$_.Value -ge 2}|Sort-Object -Property Value,Key -Descending|Select-Object -First 8|ForEach-Object{$_.Key})
    # Never discard a reciprocal link already created while processing an earlier note.
    $existing=Arr $e.related_notes;$e.related_notes=@($existing+$related|Select-Object -Unique)
    foreach($target in $related){$other=$entries|Where-Object{$_.source_name -eq $target}|Select-Object -First 1;if($other){$prior=Arr $other.related_notes;if($prior -notcontains $e.source_name){$other.related_notes=@($prior+$e.source_name);$stats.reciprocal_links_created++}}}
    $stats.notes_migrated++;$processed++;[void]$done.Add($e.source_name)
  } catch {$stats.failures++;Write-Warning "Migration failure for $($e.source_name): $($_.Exception.Message)"}
  [pscustomobject]@{version=$version;completed=@($done)}|ConvertTo-Json -Depth 4|Out-File -LiteralPath $statePath -Encoding utf8 -NoNewline
}
# Final reconciliation makes reciprocal relationships invariant, irrespective of iteration order.
$byName=@{};foreach($entry in $entries){$byName[$entry.source_name]=$entry}
foreach($entry in $entries){foreach($target in Arr $entry.related_notes){if($target -ne $entry.source_name -and $byName.ContainsKey("$target")){$peer=$byName["$target"];$peerLinks=Arr $peer.related_notes;if($peerLinks -notcontains $entry.source_name){$peer.related_notes=@($peerLinks+$entry.source_name);$stats.reciprocal_links_created++}}}}
$entries|ConvertTo-Json -Depth 8|Out-File -LiteralPath $libraryPath -Encoding utf8 -NoNewline
& (Join-Path $PSScriptRoot 'GraphHealth.ps1') -Vault $Vault | Out-Null
$health=(Get-Content -Raw (Join-Path $Vault 'Reports\Graph-Health.md')|Select-String -Pattern '\*\*Graph Health Score\*\* \| \*\*(\d+)').Matches.Groups[1].Value
@"
# Legacy Graph Migration

Completed: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
Version: $version

| Statistic | Value |
|---|---:|
| Notes migrated this run | $($stats.notes_migrated) |
| Concept pages created | $($stats.concept_pages_created) |
| Entity pages created | $($stats.entity_pages_created) |
| Reciprocal links created | $($stats.reciprocal_links_created) |
| Hub backlinks added | $($stats.backlinks_added) |
| Failures | $($stats.failures) |
| Orphans remaining | $((Get-Content -Raw (Join-Path $Vault 'Reports\Graph-Health.md')|Select-String -Pattern 'Orphan notes \| (\d+)').Matches.Groups[1].Value) |
| Graph Health Score | $health / 100 (baseline: 30) |
"@ | Out-File -LiteralPath $reportPath -Encoding utf8
Write-Host "Migrated $($stats.notes_migrated) notes. Graph Health: $health/100. Report: $reportPath"
