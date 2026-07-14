param([Parameter(Mandatory)]$Classification,[Parameter(Mandatory)][string]$SourceName,[string]$Document='',[string]$Vault=(Split-Path $PSScriptRoot -Parent))
$libraryPath=Join-Path $Vault '00_System\library.json'; $conceptRoot=Join-Path $Vault 'Wiki\Concepts'; $entityRoot=Join-Path $Vault 'Entities'
foreach($d in @($conceptRoot,$entityRoot)){if(!(Test-Path $d)){New-Item -ItemType Directory -Path $d|Out-Null}}
function Norm([string]$s){(($s.ToLowerInvariant() -replace '[^a-z0-9 ]',' ') -replace '\s+',' ').Trim()}
function Words($e){@($e.primary_topic)+@($e.secondary_domains)+@($e.subtopics)+@($e.tags)+@($e.entities)+@($e.summary -split '\W+'|Where-Object {$_.Length -gt 4})|ForEach-Object{Norm "$_"}|Where-Object{$_}}
$entries=if(Test-Path $libraryPath){@(Get-Content -Raw $libraryPath|ConvertFrom-Json)}else{@()}; $candidate=[pscustomobject]@{primary_topic=$Classification.primary_topic;secondary_domains=@($Classification.secondary_domains);subtopics=@($Classification.subtopics);tags=@($Classification.tags);entities=@($Classification.entities);summary=$Classification.summary}; $cw=@(Words $candidate|Select-Object -Unique)
$people=@(& (Join-Path $PSScriptRoot 'Resolve-PersonIdentities.ps1') -Document $Document -SourceName $SourceName -Vault $Vault)
if($people.Count){$Classification.entities=@($Classification.entities+$people|Where-Object {$_}|Select-Object -Unique);$Classification|Add-Member -NotePropertyName people -NotePropertyValue $people -Force}
$related=@($entries|ForEach-Object{$w=@(Words $_|Select-Object -Unique);$common=@($cw|Where-Object{$w -contains $_}).Count;$domain=if(@(@($_.primary_topic)+@($_.secondary_domains)|Where-Object{@($candidate.primary_topic)+@($candidate.secondary_domains)-contains $_}).Count){1}else{0};[pscustomobject]@{name=$_.source_name;score=$common+(2*$domain)}}|Where-Object{$_.score -ge 3}|Sort-Object -Property score,name -Descending|Select-Object -First 8)
$Classification|Add-Member -NotePropertyName related_notes -NotePropertyValue @($related.name) -Force
# Persist reciprocal note links now. The new note itself is saved by ariadne.ps1 immediately after this pass.
foreach($target in $related.name){$entry=$entries|Where-Object{$_.source_name -eq $target}|Select-Object -First 1;if($entry){$current=@($entry.related_notes);if($current -notcontains $SourceName){$entry|Add-Member -NotePropertyName related_notes -NotePropertyValue @($current+$SourceName) -Force}}}
if($related.Count){$entries|ConvertTo-Json -Depth 8|Out-File -LiteralPath $libraryPath -Encoding utf8 -NoNewline}
foreach($e in @($Classification.entities)){if(!$e -or "$e" -match '^@'){continue};$p=Join-Path $entityRoot ("$(Norm "$e").md");if(!(Test-Path $p)){"# $e`n"|Out-File $p -Encoding utf8};$marker="- [[$SourceName]]";if((Get-Content -Raw $p) -notmatch [regex]::Escape($marker)){Add-Content $p $marker -Encoding utf8}}
foreach($c in @($Classification.tags)+@($Classification.subtopics)){if(!$c){continue};$p=Join-Path $conceptRoot ("$(Norm "$c").md");if(!(Test-Path $p)){"# $c`n"|Out-File $p -Encoding utf8};Add-Content $p "- [[$SourceName]]" -Encoding utf8}
return $Classification
