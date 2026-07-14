param([string]$Vault=(Split-Path $PSScriptRoot -Parent),[switch]$IncludePermanent)
$failed=Join-Path $Vault 'Failed';$inbox=Join-Path $Vault 'Inbox';$queuePath=Join-Path $Vault 'Logs\IngestionRetryQueue.json'
$queue=if(Test-Path $queuePath){@(Get-Content -Raw $queuePath|ConvertFrom-Json)}else{@()};$known=@{};foreach($q in $queue){$known[$q.source_name]=$q}
$moved=0
foreach($file in Get-ChildItem -LiteralPath $failed -File -Filter '*.md'){
  $q=$known[$file.Name];if($q -and $q.status -eq 'permanent' -and !$IncludePermanent){continue};if($q){$q.status='pending';$q.next_attempt=(Get-Date).ToString('s')}else{$q=[pscustomobject]@{source_name=$file.Name;attempts=0;status='pending';last_reason='Legacy failed file queued for schema-normalised retry';last_attempt=$null;next_attempt=(Get-Date).ToString('s')};$queue+=@($q)}
  Move-Item -LiteralPath $file.FullName -Destination (Join-Path $inbox $file.Name) -Force;$known[$file.Name]=$q;$moved++
}
$queue|ConvertTo-Json -Depth 6|Out-File $queuePath -Encoding utf8 -NoNewline
Write-Host "Queued $moved failed file(s) for retry. Run ariadne.ps1 to process them."
