param([Parameter(Mandatory)][string]$Document,[Parameter(Mandatory)][string]$SourceName,[string]$Vault=(Split-Path $PSScriptRoot -Parent))
$indexPath=Join-Path $Vault '00_System\PersonIdentityIndex.json';$peopleRoot=Join-Path $Vault 'People'
$aliasMapPath=Join-Path $Vault '00_System\PersonAliases.json'
if(!(Test-Path $peopleRoot)){New-Item -ItemType Directory -Path $peopleRoot|Out-Null}
function Key([string]$s){$s.Trim().TrimStart('@').ToLowerInvariant()}
$index=if(Test-Path $indexPath){@(Get-Content -Raw $indexPath|ConvertFrom-Json)}else{@()};$byAlias=@{};foreach($p in $index){foreach($a in @($p.aliases)){if($a){$byAlias[(Key "$a")]=$p}}
}
$canonicalAliases=@{};if(Test-Path $aliasMapPath){$map=Get-Content -Raw $aliasMapPath|ConvertFrom-Json;foreach($property in $map.psobject.Properties){$canonicalAliases[(Key $property.Name)]=$property.Value}}
$Document=$Document -replace '\\_','_'
$people=[System.Collections.Generic.List[string]]::new()
foreach($match in [regex]::Matches($Document,'(?<![A-Za-z0-9_])@([A-Za-z0-9_]{2,32})')){
  $alias='@'+$match.Groups[1].Value;$key=Key $alias;$canonical=if($canonicalAliases.ContainsKey($key)){$canonicalAliases[$key]}else{$alias};$person=if($byAlias.ContainsKey((Key $canonical))){$byAlias[(Key $canonical)]}elseif($byAlias.ContainsKey($key)){$byAlias[$key]}else{$null}
  if(!$person){$person=[pscustomobject]@{canonical_name=$canonical;aliases=@($canonical);created_at=(Get-Date -Format 's');last_seen=$null;interaction_count=0};$index+=@($person);$byAlias[(Key $canonical)]=$person}
  if(@($person.aliases) -notcontains $alias){$person.aliases=@($person.aliases+$alias);$byAlias[$key]=$person}
  $person.last_seen=(Get-Date -Format 's');$person.interaction_count=[int]$person.interaction_count+1
  $safe=(Key $person.canonical_name);$page=Join-Path $peopleRoot "$safe.md";$marker="- [[$SourceName]]"
  if(!(Test-Path $page)){@"
# $($person.canonical_name)

Type: Person
Aliases: $($person.aliases -join ', ')

## Interactions

"@|Out-File $page -Encoding utf8}
  $body=Get-Content -Raw $page;if($body -notmatch [regex]::Escape($marker)){Add-Content $page $marker -Encoding utf8}
  if(!$people.Contains($person.canonical_name)){$people.Add($person.canonical_name)}
}
$index|ConvertTo-Json -Depth 5|Out-File $indexPath -Encoding utf8 -NoNewline
return @($people)
