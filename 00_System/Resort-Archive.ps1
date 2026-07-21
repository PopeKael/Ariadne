[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$Vault = "D:\Downloads\KnowledgeVault"
)

$ErrorActionPreference = 'Stop'
$Wiki = Join-Path $Vault 'Wiki'
$LibraryPath = Join-Path $Vault '00_System\library.json'
$MapPath = Join-Path $Vault '00_System\KnowledgeMap.md'
$ReportDirectory = Join-Path $Vault 'Reports\Ariadne'
$NL = [Environment]::NewLine

function Get-WikiFileName {
    param([string]$Topic)
    $Safe = $Topic
    foreach ($Character in [System.IO.Path]::GetInvalidFileNameChars()) { $Safe = $Safe.Replace($Character, '-') }
    return "$Safe.md"
}

function Get-ResortDecision {
    param([string]$SourceName)
    switch -Regex ($SourceName) {
        '^(Cannabis and Brain Protection|Lyrica Medication Information|Myonal 50mg Drug Info|10-Year Data Confirm No Benefit From Knee Arthroscopy|Stroke warning signs advice)' { return @{ Topic = 'Health & Medicine'; Secondary = @('Personal'); Reason = 'Reclassified from Archive as health or medical reference material.' } }
        '^(Gravity Quantum Experiment|Nikola Tesla Overview|Tetrataenite Super Material|Upending Our Understanding of the Universe|We Can Make an Antigravity Machine|V8 engine overview|Rice Growing Methods Explained|The Rabbit Hole - Lazar UFO Theory Revisited|The Rabbit Hole - Adobe Construction Debate)' { return @{ Topic = 'Science & Technology'; Secondary = @('Philosophy'); Reason = 'Reclassified from Archive as science, engineering, or technology reference material.' } }
        '^(China says it can hold people abroad accountable|What is the current situation of the Middle East war|There is current speculation that Benjamin Netanyahu|Window into the World - Soil fever overview|Window into the World - Today''s Global News Highlights|Window into the World - Fake Plane Claim Debunked|Window into the World - US Iran Invasion Analysis|5,000 dishonest officials to lose jobs over exam scandal|Latest News Updates)' { return @{ Topic = 'News & Current Affairs'; Secondary = @('History & Society'); Reason = 'Reclassified from Archive as current affairs, news, or fact-checking material.' } }
        '^(Crowley and Epstein Conspiracy|New chat\.md$|Second opinion request|The Rabbit Hole - US Middle East Policy|Apartheid in South Africa)' { return @{ Topic = 'History & Society'; Secondary = @('Philosophy'); Reason = 'Reclassified from Archive as social, political, historical, or cultural analysis.' } }
        '^New chat2026-07-12T08_09_55' { return @{ Topic = 'General Reference'; Secondary = @(); Reason = 'Reclassified from Archive as a short factual reference lookup.' } }
        '^New chat2026-07-12T08_08_31' { return @{ Topic = 'General Reference'; Secondary = @(); Reason = 'Reclassified from Archive as a short reference conversation.' } }
        '^(Photo Recognition - Next Step|Photo Recognition - Photo Recognition Pipeline)' { return @{ Topic = 'Projects'; Secondary = @('Knowledge Management', 'AI & LLMs'); Reason = 'Reclassified from Archive as a defined photo-archive software project.' } }
        '^Prime Number Checker Function' { return @{ Topic = 'Knowledge Management'; Secondary = @(); Reason = 'Reclassified from Archive as structured information about organising and analysing an image collection.' } }
        '^Gaming - Spear Rope Dart Build' { return @{ Topic = 'Gaming'; Secondary = @(); Reason = 'Reclassified from Archive as gaming build and gameplay material.' } }
        '^My art - Photo Restoration Assistance' { return @{ Topic = 'Content Creation'; Secondary = @('AI & LLMs'); Reason = 'Reclassified from Archive as creative photo-restoration content.' } }
        '^Correspondence - Social Post Draft' { return @{ Topic = 'Content Creation'; Secondary = @('Business'); Reason = 'Reclassified from Archive as social-media publishing material.' } }
        '^Interactive HTML Preview|^The Rabbit Hole2026-07-14T07_45_58' { return @{ Topic = 'General Reference'; Secondary = @(); Reason = 'Reclassified from Archive as a retained reference or exploratory snippet.' } }
        '^untitled\.md$' { return @{ Topic = 'Archive'; Secondary = @(); Reason = 'Retained in Archive because it is an empty metadata-only placeholder.' } }
        default { return @{ Topic = 'General Reference'; Secondary = @(); Reason = 'Reclassified from Archive as general reference material pending a more specific reusable classification.' } }
    }
}

function Remove-WikiSourceBlock {
    param([string]$Content, [string]$SourceName)
    $Escaped = [regex]::Escape($SourceName)
    $Pattern = "(?ms)^- Source: $Escaped\r?\n.*?(?=^- Source: |\z)"
    return [regex]::Replace($Content, $Pattern, '')
}

function Add-WikiSourceBlock {
    param($Entry, [string]$Topic)
    $Path = Join-Path $Wiki (Get-WikiFileName $Topic)
    $TagLine = if (@($Entry.tags).Count) { @($Entry.tags) -join ', ' } else { 'None' }
    $Related = @(@($Entry.secondary_domains) + @($Entry.links) | Where-Object { $_ } | Select-Object -Unique)
    $RelatedLine = if ($Related.Count) { $Related -join ', ' } else { 'None' }
    if (!(Test-Path -LiteralPath $Path)) {
        "# $Topic$NL$NL" + "Purpose: Domain for $Topic material.$NL$NL## Sources$NL" | Out-File -LiteralPath $Path -Encoding utf8 -NoNewline
    }
    $Existing = Get-Content -LiteralPath $Path -Raw
    if ($Existing -match [regex]::Escape("Source: $($Entry.source_name)")) { return }
    $Block = @"
- Source: $($Entry.source_name)
  - Processed: [[$($Entry.processed_path)]]
  - Review: [[$($Entry.review_path)]]
  - Added: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
  - Tags: $TagLine
  - Related: $RelatedLine
  - Map Entry: $($Entry.map_entry)
  - Summary: $($Entry.summary)

"@
    Add-Content -LiteralPath $Path -Value $Block -Encoding utf8
}

function Move-MapEntry {
    param([string[]]$Lines, [string]$Topic, [string]$MapEntry)
    $TargetHeader = "## $Topic"
    $Exact = "- $MapEntry"
    $Lines = @($Lines | Where-Object { $_ -ne $Exact })
    $HeaderIndex = [array]::IndexOf($Lines, $TargetHeader)
    if ($HeaderIndex -lt 0) {
        $Lines += @('', $TargetHeader, "Purpose: Domain for $Topic material.", $Exact)
        return ,$Lines
    }
    $NextHeader = $Lines.Count
    for ($i = $HeaderIndex + 1; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i] -match '^##\s+') { $NextHeader = $i; break }
    }
    if (-not (@($Lines[$HeaderIndex..($NextHeader-1)] | Where-Object { $_ -eq $Exact }).Count)) {
        $InsertAt = $HeaderIndex + 1
        while ($InsertAt -lt $NextHeader -and $Lines[$InsertAt] -notmatch '^Purpose:') { $InsertAt++ }
        if ($InsertAt -lt $NextHeader) { $InsertAt++ }
        $Before = if ($InsertAt -gt 0) { @($Lines[0..($InsertAt-1)]) } else { @() }
        $After = if ($InsertAt -lt $Lines.Count) { @($Lines[$InsertAt..($Lines.Count-1)]) } else { @() }
        $Lines = @($Before + $Exact + $After)
    }
    return ,$Lines
}

if (!(Test-Path -LiteralPath $LibraryPath)) { throw "Library not found: $LibraryPath" }
$Entries = @(Get-Content -LiteralPath $LibraryPath -Raw | ConvertFrom-Json)
$ArchiveEntries = @($Entries | Where-Object { $_.primary_topic -eq 'Archive' })
$Changes = @()
$MapLines = @(Get-Content -LiteralPath $MapPath)

foreach ($Entry in $ArchiveEntries) {
    $Decision = Get-ResortDecision $Entry.source_name
    if ($Decision.Topic -eq 'Archive') { continue }
    $OldWiki = Join-Path $Wiki (Get-WikiFileName 'Archive')
    if (Test-Path -LiteralPath $OldWiki) {
        $OldContent = Get-Content -LiteralPath $OldWiki -Raw
        $NewContent = Remove-WikiSourceBlock -Content $OldContent -SourceName $Entry.source_name
        if ($NewContent -ne $OldContent) { $NewContent.TrimEnd() + $NL | Out-File -LiteralPath $OldWiki -Encoding utf8 -NoNewline }
    }
    Add-WikiSourceBlock -Entry $Entry -Topic $Decision.Topic
    $Entry | Add-Member -NotePropertyName previous_primary_topic -NotePropertyValue 'Archive' -Force
    $Entry.primary_topic = $Decision.Topic
    $Entry.secondary_domains = @($Decision.Secondary | Where-Object { $_ -ne $Decision.Topic })
    $Entry.reason = $Decision.Reason
    $Entry.wiki_path = "Wiki/$(Get-WikiFileName $Decision.Topic)"
    $MapLines = Move-MapEntry -Lines $MapLines -Topic $Decision.Topic -MapEntry $Entry.map_entry
    $Changes += [pscustomobject]@{ source_name = $Entry.source_name; from = 'Archive'; to = $Decision.Topic; reason = $Decision.Reason }
}

if ($Changes.Count -gt 0) {
    $Entries | ConvertTo-Json -Depth 20 | Out-File -LiteralPath $LibraryPath -Encoding utf8 -NoNewline
    "> Last updated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')$NL$NL" + ($MapLines -join $NL) | Out-File -LiteralPath $MapPath -Encoding utf8 -NoNewline
}

New-Item -ItemType Directory -Path $ReportDirectory -Force | Out-Null
$ReportPath = Join-Path $ReportDirectory ("Archive-Resort-{0}.md" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
@"
# Archive Resort Report

Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')

Archive records reviewed: $($ArchiveEntries.Count)
Records reclassified: $($Changes.Count)
Records retained in Archive: $($ArchiveEntries.Count - $Changes.Count)

## Changes

$(if ($Changes.Count) { ($Changes | ForEach-Object { "- $($_.source_name) → $($_.to)" }) -join $NL } else { '- None' })
"@ | Out-File -LiteralPath $ReportPath -Encoding utf8 -NoNewline

Write-Output "Reclassified $($Changes.Count) Archive records; retained $($ArchiveEntries.Count - $Changes.Count)."
Write-Output "Report: $ReportPath"
