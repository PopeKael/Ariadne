param(
    [string]$Vault = (Split-Path $PSScriptRoot -Parent),
    [int]$LowConnectivityThreshold = 2,
    [switch]$FailBelowThreshold,
    [int]$MinimumHealthScore = 70
)

$LibraryPath = Join-Path $Vault '00_System\library.json'
$ReportPath = Join-Path $Vault 'Reports\Graph-Health.md'
$RetryQueuePath = Join-Path $Vault 'Logs\IngestionRetryQueue.json'
function N([string]$s) { if ($null -eq $s) { return '' }; return (($s.ToLowerInvariant() -replace '[^a-z0-9 ]',' ') -replace '\s+',' ').Trim() }
function A($v) { if ($null -eq $v) { return @() }; return @($v) }

$entries = if (Test-Path -LiteralPath $LibraryPath) { @(Get-Content -Raw -LiteralPath $LibraryPath | ConvertFrom-Json) } else { @() }
$notes = @{}; $edges = @{}; $concepts = @{}; $entities = @{}
foreach ($e in $entries) {
    if (!$e.source_name) { continue }; $notes[$e.source_name] = $e; $edges[$e.source_name] = [System.Collections.Generic.HashSet[string]]::new()
    foreach ($c in @(A $e.tags) + @(A $e.subtopics)) { $k=N "$c"; if ($k) { if (!$concepts.ContainsKey($k)) {$concepts[$k]=@()}; $concepts[$k]+=$e.source_name } }
    foreach ($x in @(A $e.entities)) { $k=N "$x"; if ($k) { if (!$entities.ContainsKey($k)) {$entities[$k]=@()}; $entities[$k]+=$e.source_name } }
    foreach ($r in @(A $e.related_notes)) { if ($notes.ContainsKey("$r")) { [void]$edges[$e.source_name].Add("$r") } }
}
# Links are deliberately explicit only: inferred shared domain/tag membership is useful
# for discovery, but counting it as a graph edge would make a sparse vault look healthy.
$orphan=@($notes.Keys | Where-Object {$edges[$_].Count -eq 0}); $low=@($notes.Keys | Where-Object {$edges[$_].Count -lt $LowConnectivityThreshold})
$dupConcept=@($concepts.Keys | Group-Object { ($_ -replace '\b(ai|llms?|the|and)\b','').Trim() } | Where-Object {$_.Count -gt 1})
$dupEntity=@($entities.Keys | Group-Object { $_ } | Where-Object {$_.Count -gt 1})
$wikiConcepts=Join-Path $Vault 'Wiki\Concepts'; $wikiEntities=Join-Path $Vault 'Entities'
$missingConcept=@($concepts.Keys | Where-Object { !(Test-Path -LiteralPath (Join-Path $wikiConcepts ("$_.md"))) }); $missingEntity=@($entities.Keys | Where-Object { !(Test-Path -LiteralPath (Join-Path $wikiEntities ("$_.md"))) })
$backlinkMissing=@(); foreach($n in $notes.Keys) { foreach($r in @(A $notes[$n].related_notes)) { if ($notes.ContainsKey("$r") -and @(A $notes["$r"].related_notes) -notcontains $n) {$backlinkMissing += "$n -> $r"} } }
$cross=0; $possible=0; foreach($a in $notes.Keys){foreach($b in $edges[$a]){if($a -lt $b){$possible++; if($notes[$a].primary_topic -ne $notes[$b].primary_topic){$cross++}}}}
$avg=if($notes.Count){[math]::Round((@($edges.Values|ForEach-Object {$_.Count}|Measure-Object -Sum).Sum/$notes.Count),2)}else{0}; $ratio=if($possible){$cross/$possible}else{0}
$retry=@(); if(Test-Path $RetryQueuePath){try{$retry=@(Get-Content -Raw $RetryQueuePath|ConvertFrom-Json)}catch{}}
$permanent=@($retry|Where-Object {$_.status -eq 'permanent'}).Count; $pending=@($retry|Where-Object {$_.status -eq 'pending'}).Count
$score=[math]::Max(0,[math]::Min(100,[math]::Round(100 - (40*($orphan.Count/[math]::Max(1,$notes.Count))) - (20*($low.Count/[math]::Max(1,$notes.Count))) - (10*($backlinkMissing.Count/[math]::Max(1,$possible))) - (10*($missingConcept.Count/[math]::Max(1,$concepts.Count))) - (10*($missingEntity.Count/[math]::Max(1,$entities.Count))) + (10*$ratio))))
$report=@"
# Knowledge Graph Health Audit

Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')

| Metric | Value |
|---|---:|
| Total notes | $($notes.Count) |
| Total links (observed relationships) | $(@($edges.Values | ForEach-Object {$_.Count} | Measure-Object -Sum).Sum) |
| Average links per note | $avg |
| Orphan notes | $($orphan.Count) |
| Low-connectivity notes (< $LowConnectivityThreshold) | $($low.Count) |
| Duplicate concept groups | $($dupConcept.Count) |
| Duplicate entity groups | $($dupEntity.Count) |
| Missing backlinks | $($backlinkMissing.Count) |
| Missing concept pages | $($missingConcept.Count) |
| Missing entity pages | $($missingEntity.Count) |
| Cross-cluster connectivity | $([math]::Round(100*$ratio,1))% |
| Retry queue pending / permanent | $pending / $permanent |
| **Graph Health Score** | **$score / 100** |

## Orphan notes
$(if($orphan.Count){$orphan|ForEach-Object{"- $_"}}else{'None'})

## Low-connectivity notes
$(if($low.Count){$low|ForEach-Object{"- $_ ($($edges[$_].Count))"}}else{'None'})

## Integrity debt
$(if($backlinkMissing.Count){$backlinkMissing|ForEach-Object{"- $_"}}else{'No missing explicit backlinks.'})
"@
$report | Out-File -LiteralPath $ReportPath -Encoding utf8
Write-Host "Graph Health Score: $score/100. Report: $ReportPath"
if($FailBelowThreshold -and $score -lt $MinimumHealthScore){exit 2}
