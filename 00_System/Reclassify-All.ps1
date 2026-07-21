[CmdletBinding()]
param([string]$Vault = 'D:\Downloads\KnowledgeVault')
$ErrorActionPreference='Stop'
$System=Join-Path $Vault '00_System'
$Wiki=Join-Path $Vault 'Wiki'
$Library=Join-Path $System 'library.json'
$MapPath=Join-Path $System 'KnowledgeMap.md'
$DomainsFile=Join-Path $System 'DomainVocabulary.json'
$Backups=Join-Path $Vault 'Archive\ReclassificationBackups'
$Processed=Join-Path $Vault 'Processed'
$Inbox=Join-Path $Vault 'Inbox'
$Failed=Join-Path $Vault 'Failed'
$NL=[Environment]::NewLine

function Set-Field($o,[string]$n,$v){if($o.PSObject.Properties.Name -contains $n){$o.$n=$v}else{$o|Add-Member -NotePropertyName $n -NotePropertyValue $v}}
function Remove-SourceBlock([string]$content,[string]$name){$p="(?ms)^- Source: $([regex]::Escape($name))\r?\n.*?(?=^- Source: |\z)";return [regex]::Replace($content,$p,'')}
function Add-MapEntry([string[]]$lines,[string]$topic,[string]$entry){
 $line="- $entry";if($lines -contains $line){return ,$lines};$idx=[array]::IndexOf($lines,"## $topic")
 if($idx -lt 0){return ,@($lines+@('',"## $topic","Purpose: Domain for $topic material.",$line))}
 $next=$lines.Count;for($j=$idx+1;$j -lt $lines.Count;$j++){if($lines[$j]-match '^##\s+'){$next=$j;break}}
 $at=$idx+1;while($at -lt $next -and $lines[$at]-notmatch '^Purpose:'){$at++};if($at -lt $next){$at++}
 return ,@($lines[0..($at-1)]+$line+$lines[$at..($lines.Count-1)])
}
function Valid($r,[string[]]$a){if(!$r -or $r.primary_topic -notin $a){return $false};$s=@($r.secondary_domains);if($s.Count -gt 3){return $false};if(@($s|Where-Object{$_ -notin $a -or $_ -eq $r.primary_topic}).Count){return $false};return @($s|Select-Object -Unique).Count -eq $s.Count}
function Ask([string]$e,[string]$d,[string[]]$a){
 $p=@('Reclassify one existing KnowledgeVault record. Return only JSON: {"primary_topic":"domain","secondary_domains":[],"reason":"short reason"}','Use only the supplied domains. Do not summarise or create tags, links, or entities.','DOMAINS:',$d,'EVIDENCE:',$e)-join $NL
 for($n=1;$n -le 2;$n++){ $w=[Diagnostics.Stopwatch]::StartNew();try{$b=@{model='gpt-oss:20b';prompt=$p;stream=$false;format='json';options=@{temperature=0;num_predict=220}}|ConvertTo-Json -Depth 8;$x=Invoke-RestMethod -Uri 'http://localhost:11434/api/generate' -Method Post -ContentType 'application/json' -Body $b;$raw=([string]$x.response).Trim();$start=$raw.IndexOf('{');$end=$raw.LastIndexOf('}');$json=if($start -ge 0 -and $end -gt $start){$raw.Substring($start,$end-$start+1)}else{$raw};$r=$json|ConvertFrom-Json;if(Valid $r $a){return [pscustomobject]@{Result=$r;Ms=$w.ElapsedMilliseconds;Attempts=$n}};$f='Invalid domain response'}catch{$f=$_.Exception.Message};$p+=$NL+'Repair: return exactly valid JSON with primary_topic, secondary_domains, and reason.'}
 return [pscustomobject]@{Failure=$f;Ms=$w.ElapsedMilliseconds;Attempts=2}
}

$latest=@(Get-ChildItem -LiteralPath $Backups -Directory -ErrorAction SilentlyContinue|Where-Object{Test-Path (Join-Path $_.FullName 'library.json')}|Sort-Object LastWriteTime|Select-Object -Last 1)
$basePath=if($latest){Join-Path $latest.FullName 'library.json'}else{$Library}
$base=@(Get-Content -LiteralPath $basePath -Raw|ConvertFrom-Json);$byName=@{};foreach($e in $base){$byName[$e.source_name]=$e}
$v=Get-Content -LiteralPath $DomainsFile -Raw|ConvertFrom-Json;$allowed=@($v.domains|Where-Object{$_.name -ne 'Archive'}|ForEach-Object{$_.name});$catalog=(($v.domains|Where-Object{$_.name -ne 'Archive'}|ForEach-Object{"- $($_.name): $($_.description)"})-join $NL)
$files=@(Get-ChildItem -LiteralPath $Processed -File -Filter '*.md'|Where-Object{$_.Name -ne 'README.md'};Get-ChildItem -LiteralPath $Inbox -File -Filter '*.md';Get-ChildItem -LiteralPath $Failed -File -Filter '*.md')|Sort-Object Name -Unique
if(!$files.Count){throw 'No source documents found.'}
$final=@{};$i=0;$ok=0;$bad=0;Write-Host "Compact reclassification started: $($files.Count) documents" -ForegroundColor Cyan
foreach($file in $files){$i++;$old=if($byName.ContainsKey($file.Name)){$byName[$file.Name]}else{$null};$e=@("Source: $($file.Name)");if($old){$e+="Map entry: $($old.map_entry)";$e+="Summary: $($old.summary)";$e+="Tags: $(@($old.tags)-join ', ')";$e+="Subtopics: $(@($old.subtopics)-join ', ')";$e+="Entities: $(@($old.entities)-join ', ')"}else{$t=(Get-Content -LiteralPath $file.FullName -Raw)-replace '\s+',' ';if($t.Length -gt 1800){$t=$t.Substring(0,1800)};$e+="Excerpt: $t"};Write-Host ("[{0}/{1}] {2}" -f $i,$files.Count,$file.Name);$c=Ask ($e -join $NL) $catalog $allowed;if(!$c.Result){$bad++;Write-Host "FAILED: $($c.Failure)" -ForegroundColor Red;continue};$entry=if($old){$old|ConvertTo-Json -Depth 20|ConvertFrom-Json}else{[pscustomobject][ordered]@{source_name=$file.Name;document_id="source:$($file.Name)";source_language='en';subtopics=@();tags=@();links=@();entities=@();people=@();related_notes=@();map_entry=$file.BaseName;summary='Recovered classification record.';processed_path="Processed/$($file.Name)"}};Set-Field $entry 'previous_primary_topic' ([string]$entry.primary_topic);Set-Field $entry 'primary_topic' ([string]$c.Result.primary_topic);Set-Field $entry 'secondary_domains' @($c.Result.secondary_domains|Select-Object -Unique);Set-Field $entry 'reason' ([string]$c.Result.reason);Set-Field $entry 'wiki_path' "Wiki/$($entry.primary_topic).md";$final[$file.Name]=$entry;$ok++;Write-Host (" -> {0} | {1} ms | attempts {2}" -f $entry.primary_topic,$c.Ms,$c.Attempts) -ForegroundColor Green}
$entries=@($files|ForEach-Object{if($final.ContainsKey($_.Name)){$final[$_.Name]}});$entries|ConvertTo-Json -Depth 20|Out-File -LiteralPath $Library -Encoding utf8 -NoNewline
Write-Host "Classification complete. Success=$ok Failed=$bad" -ForegroundColor Cyan
foreach($page in @(Get-ChildItem -LiteralPath $Wiki -File -Filter '*.md')){
 $clean=Get-Content -LiteralPath $page.FullName -Raw
 foreach($name in $final.Keys){$clean=Remove-SourceBlock $clean $name}
 $clean.TrimEnd()+$NL|Out-File -LiteralPath $page.FullName -Encoding utf8 -NoNewline
}
foreach($entry in $entries){
 $page=Join-Path $Wiki "$($entry.primary_topic).md"
 if(!(Test-Path $page)){"# $($entry.primary_topic)$NL$NL## Sources$NL"|Out-File -LiteralPath $page -Encoding utf8 -NoNewline}
 $tags=if(@($entry.tags).Count){@($entry.tags)-join ', '}else{'None'}
 $related=@(@($entry.secondary_domains)+@($entry.links)|Where-Object{$_}|Select-Object -Unique)
 @"
- Source: $($entry.source_name)
  - Processed: [[Processed/$($entry.source_name)]]
  - Review: [[Review/$([IO.Path]::GetFileNameWithoutExtension($entry.source_name)).review.md]]
  - Added: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
  - Tags: $tags
  - Related: $($related -join ', ')
  - Map Entry: $($entry.map_entry)
  - Summary: $($entry.summary)

"@|Add-Content -LiteralPath $page -Encoding utf8
}
$map=@(Get-Content -LiteralPath $MapPath)
$oldMap=@($base|Where-Object{$_.map_entry}|ForEach-Object{"- $($_.map_entry)"})
$map=@($map|Where-Object{$oldMap -notcontains $_})
foreach($entry in $entries){$map=Add-MapEntry $map $entry.primary_topic $entry.map_entry}
"> Last updated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')$NL$NL"+($map -join $NL)|Out-File -LiteralPath $MapPath -Encoding utf8 -NoNewline
Write-Host 'Rebuilding graph relationships and published views.' -ForegroundColor Cyan
$shell=Get-Command pwsh -ErrorAction SilentlyContinue;if(!$shell){$shell=Get-Command powershell -ErrorAction Stop}
& $shell.Source -NoProfile -ExecutionPolicy Bypass -File (Join-Path $System 'Rebuild-GraphRelations.ps1')
& $shell.Source -NoProfile -ExecutionPolicy Bypass -File (Join-Path $System 'Publish-Knowledge.ps1')
Write-Host "Complete. Success=$ok Failed=$bad" -ForegroundColor Cyan
