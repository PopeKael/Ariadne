# Ariadne v0.7
# Process Inbox, ask the model to file the document into the Knowledge Map,
# write a review file, update the Knowledge Map, then move the original to Processed.

$Vault = "D:\Downloads\KnowledgeVault"

$System    = Join-Path $Vault "00_System"
$Inbox     = Join-Path $Vault "Inbox"
$Review    = Join-Path $Vault "Review"
$Processed = Join-Path $Vault "Processed"
$Failed    = Join-Path $Vault "Failed"
$Wiki      = Join-Path $Vault "Wiki"

foreach ($Folder in @($Review, $Processed, $Failed, $Wiki)) {
    if (!(Test-Path $Folder)) {
        New-Item -ItemType Directory -Path $Folder | Out-Null
    }
}

$KnowledgeMapPath  = Join-Path $System "KnowledgeMap.md"
$AriadnePromptPath = Join-Path $System "AriadnePrompt.md"
$LibraryPath       = Join-Path $System "library.json"
$MaxItemsPerRun    = 0
$AllowedPrimaryTopics = @(
    "AI & LLMs",
    "Infrastructure",
    "Knowledge Management",
    "Projects",
    "Content Creation",
    "Business",
    "Personal",
    "Gaming",
    "Philosophy",
    "Archive",
    "Travel & Expat Experience"
)

function Get-KnowledgeMapBody {
    param($RawContent)
    # Strips a leading "> Last updated: ..." line if present, returns the rest
    $Lines = $RawContent -split "`r?`n"
    if ($Lines[0] -match '^\>\s*Last updated:') {
        return ($Lines[1..($Lines.Length - 1)] -join "`n").TrimStart("`n")
    }
    return $RawContent
}

function Update-KnowledgeMap {
    param($Topic, $Reason, $MapEntry)

    $Raw   = Get-Content $KnowledgeMapPath -Raw
    $Body  = Get-KnowledgeMapBody $Raw
    $Lines = [System.Collections.Generic.List[string]]($Body -split "`r?`n")

    $HeadingIndex = -1
    for ($i = 0; $i -lt $Lines.Count; $i++) {
        if ($Lines[$i].Trim() -eq "## $Topic") {
            $HeadingIndex = $i
            break
        }
    }

    if ($HeadingIndex -eq -1) {
        # New topic: append heading, purpose line, entry
        if ($Lines.Count -gt 0 -and $Lines[$Lines.Count - 1].Trim() -ne "") {
            $Lines.Add("")
        }
        $Lines.Add("## $Topic")
        $Lines.Add("Purpose: $Reason")
        $Lines.Add("- $MapEntry")
    } else {
        # Existing topic: insert entry after the Purpose line (or right after heading)
        $InsertAt = $HeadingIndex + 1
        if ($InsertAt -lt $Lines.Count -and $Lines[$InsertAt] -match '^Purpose:') {
            $InsertAt++
        }
        $Lines.Insert($InsertAt, "- $MapEntry")
    }

    $Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $NewContent = "> Last updated: $Timestamp`n`n" + ($Lines -join "`n")
    $NewContent | Out-File -LiteralPath $KnowledgeMapPath -Encoding utf8 -NoNewline
}

function Test-AriadneResponse {
    param($Parsed)

    if ($null -eq $Parsed) { return $false }

    $RequiredFields = @("primary_topic", "subtopics", "is_new_topic", "reason", "tags", "links", "map_entry", "summary")
    foreach ($Field in $RequiredFields) {
        if ($null -eq $Parsed.$Field) {
            return $false
        }
    }

    if ($Parsed.primary_topic -isnot [string] -or [string]::IsNullOrWhiteSpace($Parsed.primary_topic)) { return $false }
    if ($AllowedPrimaryTopics -notcontains $Parsed.primary_topic) { return $false }
    if ($Parsed.reason -isnot [string] -or [string]::IsNullOrWhiteSpace($Parsed.reason)) { return $false }
    if ($Parsed.map_entry -isnot [string] -or [string]::IsNullOrWhiteSpace($Parsed.map_entry)) { return $false }
    if ($Parsed.summary -isnot [string] -or [string]::IsNullOrWhiteSpace($Parsed.summary)) { return $false }
    if ($Parsed.is_new_topic -isnot [bool]) { return $false }
    if ($Parsed.subtopics -is [string]) { return $false }
    if ($Parsed.tags -is [string]) { return $false }
    if ($Parsed.links -is [string]) { return $false }

    $SubtopicList = @($Parsed.subtopics)
    $TagList = @($Parsed.tags)
    $LinkList = @($Parsed.links)
    foreach ($Subtopic in $SubtopicList) {
        if ($Subtopic -isnot [string]) { return $false }
    }
    foreach ($Tag in $TagList) {
        if ($Tag -isnot [string]) { return $false }
    }
    foreach ($Link in $LinkList) {
        if ($Link -isnot [string]) { return $false }
    }

    if ($Parsed.map_entry -match '[\r\n]') { return $false }
    if ($Parsed.summary -match '(^|\n)\s*#') { return $false }

    return $true
}

function ConvertTo-AriadneReply {
    param([string]$RawReply)

    $Candidate = $RawReply.Trim()
    $Candidate = $Candidate -replace '^```json\s*', '' -replace '^```\s*', '' -replace '\s*```$', ''

    $Parsed = $null
    try {
        $Parsed = $Candidate | ConvertFrom-Json
    } catch {
        $Parsed = $null
    }

    if (Test-AriadneResponse -Parsed $Parsed) {
        return $Parsed
    }

    return $null
}

function Get-AriadneSchemaExample {
    return '{"primary_topic":"AI & LLMs","subtopics":["agent workflows","local models"],"is_new_topic":false,"reason":"The document discusses AI tools and model behavior.","tags":["ai","llms"],"links":["Codex","Claude Code"],"map_entry":"Example document - concise description of the source.","summary":"This document discusses AI tools, model behavior, and practical workflow implications. It compares approaches and highlights relevant concepts. The source is useful as a reference for future retrieval."}'
}

function Get-AllowedPrimaryTopicsText {
    return ($AllowedPrimaryTopics -join ", ")
}

function Invoke-AriadneModel {
    param(
        [string]$Prompt,
        [string]$DocumentName
    )

    $Body = @{
        model  = "gpt-oss:20b"
        prompt = $Prompt
        stream = $false
    } | ConvertTo-Json -Depth 5

    $Response = Invoke-RestMethod `
        -Uri "http://localhost:11434/api/generate" `
        -Method Post `
        -ContentType "application/json" `
        -Body $Body

    $RawReply = $Response.response.Trim()
    $Parsed = ConvertTo-AriadneReply -RawReply $RawReply
    if ($Parsed) {
        return @{
            Parsed = $Parsed
            RawReply = $RawReply
        }
    }

    $SchemaExample = Get-AriadneSchemaExample
    $AllowedTopicText = Get-AllowedPrimaryTopicsText
    $RetryPrompt = @"
$Prompt

----- IMPORTANT RETRY INSTRUCTION -----
Your previous reply for "$DocumentName" was invalid for the required schema.
Return ONLY one valid JSON object that exactly matches the schema.
Do not add markdown fences.
Do not add explanations.
Do not change language away from English.
Use exactly these keys and no others:
primary_topic, subtopics, is_new_topic, reason, tags, links, map_entry, summary
primary_topic must be one of these exact values:
$AllowedTopicText
Here is a valid example shape:
$SchemaExample

Your previous invalid reply was:
$RawReply
"@

    $RetryBody = @{
        model  = "gpt-oss:20b"
        prompt = $RetryPrompt
        stream = $false
    } | ConvertTo-Json -Depth 5

    $RetryResponse = Invoke-RestMethod `
        -Uri "http://localhost:11434/api/generate" `
        -Method Post `
        -ContentType "application/json" `
        -Body $RetryBody

    $RetryRawReply = $RetryResponse.response.Trim()
    $RetryParsed = ConvertTo-AriadneReply -RawReply $RetryRawReply

    return @{
        Parsed = $RetryParsed
        RawReply = $RetryRawReply
    }
}

function Convert-ToWikiFileName {
    param([string]$Topic)

    $Safe = $Topic.Trim()
    $InvalidChars = [System.IO.Path]::GetInvalidFileNameChars()
    foreach ($Char in $InvalidChars) {
        $Safe = $Safe.Replace($Char, "-")
    }
    $Safe = ($Safe -replace '\s+', ' ').Trim()
    return "$Safe.md"
}

function Normalize-LibraryEntry {
    param($Entry)

    $PrimaryTopic = $Entry.primary_topic
    if ([string]::IsNullOrWhiteSpace($PrimaryTopic) -and $Entry.topic) {
        $PrimaryTopic = switch ($Entry.topic) {
            "Personal Finance" { "Business" }
            "Finance & Banking" { "Business" }
            "Wealth & Luxury" { "Business" }
            "Ariadne’s 10‑Layer Human Intelligence Engine" { "Projects" }
            "Build a Self Improving Claude Knowledge Base" { "Knowledge Management" }
            "Markdown Knowledge Base Architecture" { "Knowledge Management" }
            "Anthropic’s J‑Space and Model Alignment" { "AI & LLMs" }
            "AI‑Generated Infographic Design & Troubleshooting" { "Content Creation" }
            "Attention Filtering & Personal AI" { "Projects" }
            "Attention Management & AI" { "Projects" }
            "E-Begging Foreigners in Thailand" { "Travel & Expat Experience" }
            "Aging in Thailand" { "Travel & Expat Experience" }
            "Arcade Gaming Industry" { "Gaming" }
            default { $Entry.topic }
        }
    }

    if ([string]::IsNullOrWhiteSpace($PrimaryTopic)) {
        $PrimaryTopic = "Archive"
    }

    $Subtopics = @()
    if ($Entry.subtopics) {
        $Subtopics = @($Entry.subtopics)
    } elseif ($Entry.topic -and $Entry.topic -ne $PrimaryTopic) {
        $Subtopics = @([string]$Entry.topic)
    }

    return [pscustomobject][ordered]@{
        source_name     = $Entry.source_name
        primary_topic   = $PrimaryTopic
        subtopics       = @($Subtopics)
        reason          = $Entry.reason
        tags            = @($Entry.tags)
        links           = @($Entry.links)
        map_entry       = $Entry.map_entry
        summary         = $Entry.summary
        review_path     = $Entry.review_path
        processed_path  = $Entry.processed_path
        wiki_path       = $Entry.wiki_path
        indexed_at      = $Entry.indexed_at
    }
}

function Get-LibraryEntries {
    if (!(Test-Path $LibraryPath)) {
        return @()
    }

    $Raw = Get-Content -LiteralPath $LibraryPath -Raw
    if ([string]::IsNullOrWhiteSpace($Raw)) {
        return @()
    }

    try {
        $Parsed = $Raw | ConvertFrom-Json
        if ($Parsed -is [System.Array]) {
            return @($Parsed | ForEach-Object { Normalize-LibraryEntry -Entry $_ })
        }
        if ($null -ne $Parsed) {
            return @(Normalize-LibraryEntry -Entry $Parsed)
        }
    } catch {
        Write-Warning "library.json is not valid JSON. Rebuilding the index from this run forward."
    }

    return @()
}

function Save-LibraryEntries {
    param($Entries)

    $Entries |
        ConvertTo-Json -Depth 6 |
        Out-File -LiteralPath $LibraryPath -Encoding utf8 -NoNewline
}

function Update-LibraryEntry {
    param(
        [string]$SourceName,
        [string]$PrimaryTopic,
        [string[]]$Subtopics,
        [string]$Reason,
        [string[]]$Tags,
        [string[]]$Links,
        [string]$Summary,
        [string]$MapEntry,
        [string]$ReviewFileName,
        [string]$ProcessedFileName,
        [string]$WikiFileName
    )

    $Entries = [System.Collections.ArrayList]@(Get-LibraryEntries)
    $Existing = $null
    foreach ($Entry in $Entries) {
        if ($Entry.source_name -eq $SourceName) {
            $Existing = $Entry
            break
        }
    }

    if ($Existing) {
        [void]$Entries.Remove($Existing)
    }

    $Record = [ordered]@{
        source_name     = $SourceName
        primary_topic   = $PrimaryTopic
        subtopics       = @($Subtopics)
        reason          = $Reason
        tags            = @($Tags)
        links           = @($Links)
        map_entry       = $MapEntry
        summary         = $Summary
        review_path     = "Review/$ReviewFileName"
        processed_path  = "Processed/$ProcessedFileName"
        wiki_path       = "Wiki/$WikiFileName"
        indexed_at      = (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
    }

    [void]$Entries.Add([pscustomobject]$Record)
    Save-LibraryEntries -Entries $Entries
}

function Update-WikiPage {
    param(
        [string]$Topic,
        [string]$Reason,
        [string]$Summary,
        [string]$MapEntry,
        [string[]]$Tags,
        [string[]]$Links,
        [string]$SourceName,
        [string]$ProcessedFileName,
        [string]$ReviewFileName
    )

    $WikiFileName = Convert-ToWikiFileName -Topic $Topic
    $WikiPath = Join-Path $Wiki $WikiFileName
    $Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $SourceLink = "[[Processed/$ProcessedFileName]]"
    $ReviewLink = "[[Review/$ReviewFileName]]"
    $TagLine = if ($Tags.Count -gt 0) { $Tags -join ", " } else { "None" }
    $LinkLine = if ($Links.Count -gt 0) { $Links -join ", " } else { "None" }
    $SourceMarker = "Source: $SourceName"

    if (Test-Path $WikiPath) {
        $Existing = Get-Content -LiteralPath $WikiPath -Raw
        if ($Existing -match [regex]::Escape($SourceMarker)) {
            return $WikiFileName
        }
    } else {
        $Header = @"
# $Topic

Purpose: $Reason

## Sources

"@
        $Header | Out-File -LiteralPath $WikiPath -Encoding utf8
    }

    $Block = @"
- Source: $SourceName
  - Processed: $SourceLink
  - Review: $ReviewLink
  - Added: $Timestamp
  - Tags: $TagLine
  - Related: $LinkLine
  - Map Entry: $MapEntry
  - Summary: $Summary

"@
    Add-Content -LiteralPath $WikiPath -Value $Block -Encoding utf8
    return $WikiFileName
}

Write-Host ""
Write-Host "Ariadne Prompt loaded."
Write-Host ""

$ProcessedThisRun = 0
$InboxItems = Get-ChildItem $Inbox -Filter *.md
if ($MaxItemsPerRun -gt 0) {
    $InboxItems = $InboxItems | Select-Object -First $MaxItemsPerRun
}

$AllowedTopicText = Get-AllowedPrimaryTopicsText

$InboxItems | ForEach-Object {

    $Document      = Get-Content -LiteralPath $_.FullName -Raw
    $KnowledgeMap  = Get-Content $KnowledgeMapPath -Raw
    $AriadnePrompt = Get-Content $AriadnePromptPath -Raw

    Write-Host "Processing: $($_.Name)"

    $Prompt = @"
$AriadnePrompt

Allowed primary topics:
$AllowedTopicText

$KnowledgeMap

----- DOCUMENT -----

$Document
"@

    $ModelResult = Invoke-AriadneModel -Prompt $Prompt -DocumentName $_.Name
    $RawReply = $ModelResult.RawReply
    $Parsed = $ModelResult.Parsed

    $ReviewFile = Join-Path $Review ($_.BaseName + ".review.md")

    if ($Parsed) {
        Update-KnowledgeMap -Topic $Parsed.primary_topic -Reason $Parsed.reason -MapEntry $Parsed.map_entry

        $NewTag = if ($Parsed.is_new_topic) { " (new topic)" } else { "" }
        $SubtopicLine = if (@($Parsed.subtopics).Count -gt 0) { $Parsed.subtopics -join ", " } else { "None" }

        $Header = @"
# Ariadne Review

Source:
$($_.Name)

Processed:
$(Get-Date -Format "yyyy-MM-dd HH:mm:ss")

Primary Topic:
$($Parsed.primary_topic)$NewTag

Subtopics:
$SubtopicLine

Tags:
$($Parsed.tags -join ", ")

Links:
$($Parsed.links -join ", ")

---

$($Parsed.summary)
"@
        $Header | Out-File -LiteralPath $ReviewFile -Encoding utf8

        $WikiFileName = Update-WikiPage `
            -Topic $Parsed.primary_topic `
            -Reason $Parsed.reason `
            -Summary $Parsed.summary `
            -MapEntry $Parsed.map_entry `
            -Tags @($Parsed.tags) `
            -Links @($Parsed.links) `
            -SourceName $_.Name `
            -ProcessedFileName $_.Name `
            -ReviewFileName ($_.BaseName + ".review.md")

        Update-LibraryEntry `
            -SourceName $_.Name `
            -PrimaryTopic $Parsed.primary_topic `
            -Subtopics @($Parsed.subtopics) `
            -Reason $Parsed.reason `
            -Tags @($Parsed.tags) `
            -Links @($Parsed.links) `
            -Summary $Parsed.summary `
            -MapEntry $Parsed.map_entry `
            -ReviewFileName ($_.BaseName + ".review.md") `
            -ProcessedFileName $_.Name `
            -WikiFileName $WikiFileName

        Write-Host "Filed under: $($Parsed.primary_topic)$NewTag"
    } else {
        $Header = @"
# Ariadne Review (UNPARSED -- Knowledge Map not updated)

Source:
$($_.Name)

Processed:
$(Get-Date -Format "yyyy-MM-dd HH:mm:ss")

---

$RawReply
"@
        $Header | Out-File -LiteralPath $ReviewFile -Encoding utf8

        Write-Host "WARNING: model reply was not valid JSON. Review file written, Knowledge Map left untouched."
    }

    $Destination = if ($Parsed) {
        Join-Path $Processed $_.Name
    } else {
        Join-Path $Failed $_.Name
    }
    Move-Item -LiteralPath $_.FullName -Destination $Destination -Force

    Write-Host "Saved : $ReviewFile"
    Write-Host "Moved : $Destination"
    Write-Host ""
    $ProcessedThisRun++
}

Write-Host "Processed $ProcessedThisRun document(s) this run."
if ($MaxItemsPerRun -gt 0 -and (Get-ChildItem $Inbox -Filter *.md | Measure-Object).Count -gt 0) {
    Write-Host "Paused after $MaxItemsPerRun items. Run Ariadne again to continue."
}
Write-Host "Finished."
