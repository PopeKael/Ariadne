param([string]$Vault=(Split-Path $PSScriptRoot -Parent),[int]$MaxLinksPerNote=8)
$library=Join-Path $Vault '00_System\library.json';$report=Join-Path $Vault 'Reports\Graph-Reconciliation.md'
function N([string]$s){if(!$s){return ''};(($s.ToLowerInvariant()-replace '[^a-z0-9 ]',' ')-replace '\s+',' ').Trim()}
function V($x){@($x|Where-Object{$_ -and "$_".Trim()})}
function T($e){$stop='about','after','also','and','are','been','between','could','does','from','have','into','its','more','note','notes','that','the','their','this','using','with','your';@((V $e.tags)+(V $e.subtopics)+(("$($e.summary) $($e.map_entry) $($e.page_title)" -split '[^A-Za-z0-9]+'|Where-Object{$_.Length -ge 5 -and $stop -notcontains $_.ToLowerInvariant()}))|ForEach-Object{N "$_"}|Where-Object{$_}|Select-Object -Unique)}
$entries=@(Get-Content -Raw $library|ConvertFrom-Json);$by=@{};$tokens=@{};$index=@{};$stats=[ordered]@{notes_examined=$entries.Count;reciprocal_backlinks_added=0;semantic_links_added=0;orphans_before=0;orphans_after=0;failures=0}
foreach($e in $entries){$by[$e.source_name]=$e;$tokens[$e.source_name]=@(T $e);foreach($t in $tokens[$e.source_name]){if(!$index.ContainsKey($t)){$index[$t]=[System.Collections.Generic.List[string]]::new()};$index[$t].Add($e.source_name)}}
function Add-Edge($a,$b){if(!$a -or !$b -or $a -eq $b){return $false};$links=V $by[$a].related_notes;if($links -contains $b){return $false};$by[$a].related_notes=@($links+$b);return $true}
# First repair every stored one-way relationship.
foreach($e in $entries){foreach($r in V $e.related_notes){if($by.ContainsKey("$r") -and (Add-Edge "$r" $e.source_name)){$stats.reciprocal_backlinks_added++}}}
foreach($e in $entries){
  try {
    $current=V $e.related_notes;if($current.Count -ge 2){continue};$score=@{}
    foreach($t in $tokens[$e.source_name]){foreach($candidate in $index[$t]){if($candidate -eq $e.source_name){continue};$rarity=1.0/[math]::Sqrt($index[$t].Count);if(!$score.ContainsKey($candidate)){$score[$candidate]=0};$score[$candidate]+=$rarity}}
    # A rare shared token is enough; common vocabulary needs corroboration.
    $candidates=@($score.GetEnumerator()|Where-Object{$_.Value -ge 0.30}|Sort-Object -Property Value -Descending|ForEach-Object{$_.Key})
    foreach($candidate in $candidates){if((V $e.related_notes).Count -ge $MaxLinksPerNote){break};if(Add-Edge $e.source_name $candidate){$stats.semantic_links_added++;if(Add-Edge $candidate $e.source_name){$stats.reciprocal_backlinks_added++}}}
  } catch {$stats.failures++;Write-Warning "Reconciliation failure for $($e.source_name): $($_.Exception.Message)"}
}
function Degree($e){(V $e.related_notes).Count};$stats.orphans_before=@($entries|Where-Object{(Degree $_)-eq 0}).Count
$entries|ConvertTo-Json -Depth 8|Out-File $library -Encoding utf8 -NoNewline
$stats.orphans_after=@($entries|Where-Object{(Degree $_)-eq 0}).Count
& (Join-Path $PSScriptRoot 'GraphHealth.ps1') -Vault $Vault|Out-Null
@"
# Graph Reconciliation

Completed: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')

| Statistic | Value |
|---|---:|
| Notes examined | $($stats.notes_examined) |
| Reciprocal backlinks added | $($stats.reciprocal_backlinks_added) |
| Semantic links added | $($stats.semantic_links_added) |
| Orphans before / after | $($stats.orphans_before) / $($stats.orphans_after) |
| Failures | $($stats.failures) |
"@|Out-File $report -Encoding utf8
Write-Host "Reconciliation complete. Report: $report"
