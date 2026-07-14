param([string]$Vault=(Split-Path $PSScriptRoot -Parent),[int]$BatchSize=0,[switch]$Force)
$library=Join-Path $Vault '00_System\library.json';$statePath=Join-Path $Vault 'Logs\PersonMigrationState.json';$report=Join-Path $Vault 'Reports\Person-Entity-Migration.md'
$entries=@(Get-Content -Raw $library|ConvertFrom-Json);$state=if(Test-Path $statePath){Get-Content -Raw $statePath|ConvertFrom-Json}else{[pscustomobject]@{completed=@()}}
$done=[System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase);if(!$Force){foreach($name in @($state.completed|Where-Object{$_})){[void]$done.Add("$name")}}
$stats=[ordered]@{notes_scanned=0;notes_updated=0;people_detected=0;failures=0};$processed=0
foreach($entry in $entries){
  if($done.Contains($entry.source_name)){continue};if($BatchSize -gt 0 -and $processed -ge $BatchSize){break}
  try {
    $path=Join-Path $Vault $entry.processed_path;if(!(Test-Path -LiteralPath $path)){throw "Processed source missing: $($entry.processed_path)"}
    $people=@(& (Join-Path $PSScriptRoot 'Resolve-PersonIdentities.ps1') -Document (Get-Content -Raw -LiteralPath $path) -SourceName $entry.source_name -Vault $Vault)
    $prior=@($entry.people|Where-Object{$_});$merged=@($prior+$people|Where-Object{$_}|Select-Object -Unique)
    if((Compare-Object $prior $merged -SyncWindow 0).Count){$entry|Add-Member -NotePropertyName people -NotePropertyValue $merged -Force;$entry.entities=@(@($entry.entities|Where-Object{$_})+$merged|Select-Object -Unique);$stats.notes_updated++}
    $stats.people_detected+=$people.Count;$stats.notes_scanned++;$processed++;[void]$done.Add($entry.source_name)
  } catch {$stats.failures++;Write-Warning "Person migration failure for $($entry.source_name): $($_.Exception.Message)"}
  [pscustomobject]@{completed=@($done)}|ConvertTo-Json -Depth 4|Out-File $statePath -Encoding utf8 -NoNewline
}
$entries|ConvertTo-Json -Depth 8|Out-File $library -Encoding utf8 -NoNewline
& (Join-Path $PSScriptRoot 'Rebuild-GraphRelations.ps1') -Vault $Vault|Out-Null
@"
# Person Entity Migration

Completed: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')

| Statistic | Value |
|---|---:|
| Processed notes scanned | $($stats.notes_scanned) |
| Note metadata records updated | $($stats.notes_updated) |
| Person references resolved | $($stats.people_detected) |
| Failures | $($stats.failures) |
| State | $statePath |
"@|Out-File $report -Encoding utf8
Write-Host "Person migration complete. Report: $report"
