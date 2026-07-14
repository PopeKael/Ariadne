param([string]$Vault=(Split-Path $PSScriptRoot -Parent),[int]$MaxLinksPerNote=8)
$library=Join-Path $Vault '00_System\library.json';$report=Join-Path $Vault 'Reports\Graph-Reconciliation.md'
function N([string]$s){if(!$s){return ''};(($s.ToLowerInvariant()-replace '[^a-z0-9 ]',' ')-replace '\s+',' ').Trim()}
function Val($v){@($v|Where-Object{$_ -and "$($_)".Trim()})}
function Tokens($e){
  $stop=@('about','after','also','and','are','been','between','could','does','from','have','into','its','more','note','notes','that','the','their','this','using','with','your')
  $words=@("$($e.summary) $($e.map_entry) $($e.page_title)" -split '[^A-Za-z0-9]+' | Where-Object {$_.Length -ge 5 -and $stop -notcontains $_.ToLowerInvariant()})
  $raw=@(Val ($e.tags))+@(Val ($e.subtopics))+@(Val ($e.entities))+$words
  return @($raw|ForEach-Object{N "$_"}|Where-Object{$_}|Select-Object -Unique)
}
$entries=@(Get-Content -Raw $library|ConvertFrom-Json);$names=@($entries|ForEach-Object{$_.source_name});$token=@{};$index=@{};$degree=@{};$links=@{}
foreach($name in $names){$degree[$name]=0;$links[$name]=[System.Collections.Generic.HashSet[string]]::new()}
foreach($e in $entries){$token[$e.source_name]=@(Tokens $e);foreach($t in $token[$e.source_name]){if(!$index.ContainsKey($t)){$index[$t]=[System.Collections.Generic.List[string]]::new()};$index[$t].Add($e.source_name)}}
$pairs=@{}
foreach($name in $names){
  $scores=@{}
  foreach($t in $token[$name]){foreach($other in $index[$t]){if($other -ne $name){if(!$scores.ContainsKey($other)){$scores[$other]=0};$scores[$other]+=1.0/[math]::Sqrt($index[$t].Count)}}}
  foreach($x in ($scores.GetEnumerator()|Where-Object{$_.Value -ge 0.45}|Sort-Object -Property Value -Descending|Select-Object -First $MaxLinksPerNote)){$key=if($name -lt $x.Key){"$name`n$($x.Key)"}else{"$($x.Key)`n$name"};if(!$pairs.ContainsKey($key)){$pairs[$key]=$x.Value}else{$pairs[$key]=[math]::Max($pairs[$key],$x.Value)}}
}
foreach($p in $pairs.GetEnumerator()|Sort-Object Value -Descending){$a,$b=$p.Key-split "`n",2;if($degree[$a] -lt $MaxLinksPerNote -and $degree[$b] -lt $MaxLinksPerNote){[void]$links[$a].Add($b);[void]$links[$b].Add($a);$degree[$a]++;$degree[$b]++}}
foreach($e in $entries){$e.related_notes=@($links[$e.source_name]|Sort-Object)}
$entries|ConvertTo-Json -Depth 8|Out-File $library -Encoding utf8 -NoNewline
& (Join-Path $PSScriptRoot 'GraphHealth.ps1') -Vault $Vault|Out-Null
$health=(Get-Content -Raw (Join-Path $Vault 'Reports\Graph-Health.md')|Select-String '\*\*Graph Health Score\*\* \| \*\*(\d+)').Matches.Groups[1].Value
@"
# Graph Reconciliation

Completed: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')

| Statistic | Value |
|---|---:|
| Notes examined | $($entries.Count) |
| Bounded symmetric links | $(@($links.Values|ForEach-Object{$_.Count}|Measure-Object -Sum).Sum) |
| Maximum links per note | $MaxLinksPerNote |
| Orphans remaining | $(@($degree.Values|Where-Object{$_ -eq 0}).Count) |
| Graph Health Score | $health / 100 |
"@|Out-File $report -Encoding utf8
Write-Host "Rebuilt bounded symmetric graph. Health: $health/100"
