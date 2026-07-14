# Ariadne v0.7
# Process Inbox, ask the model to file the document into the Knowledge Map,
# write a review file, update the Knowledge Map, then move the original to Processed.

$Vault = "D:\Downloads\KnowledgeVault"

$System    = Join-Path $Vault "00_System"
$Inbox     = Join-Path $Vault "Inbox"
$Review    = Join-Path $Vault "Review"
$Processed = Join-Path $Vault "Processed"
$Failed    = Join-Path $Vault "Failed"
$Duplicates = Join-Path $Vault "Archive\Duplicates"
$Wiki      = Join-Path $Vault "Wiki"
$Logs      = Join-Path $Vault "Logs"

foreach ($Folder in @($Review, $Processed, $Failed, $Duplicates, $Wiki, $Logs)) {
    if (!(Test-Path $Folder)) {
        New-Item -ItemType Directory -Path $Folder | Out-Null
    }
}

$KnowledgeMapPath  = Join-Path $System "KnowledgeMap.md"
$AriadnePromptPath = Join-Path $System "AriadnePrompt.md"
$DomainVocabularyPath = Join-Path $System "DomainVocabulary.json"
$LibraryPath       = Join-Path $System "library.json"
$MaxItemsPerRun    = 0
$RetryQueuePath    = Join-Path $Logs "IngestionRetryQueue.json"
$RetryLimit        = 4
$RetryDelayMinutes = 15
$LogPath           = Join-Path $Logs "Ariadne.log"
$MaxLogSizeBytes   = 2MB
if (!(Test-Path -LiteralPath $DomainVocabularyPath)) { throw "Domain vocabulary not found: $DomainVocabularyPath" }
$DomainVocabulary = Get-Content -LiteralPath $DomainVocabularyPath -Raw | ConvertFrom-Json
$AllowedPrimaryTopics = @($DomainVocabulary.domains | ForEach-Object { $_.name })

function Rotate-AriadneLogIfNeeded {
    if (!(Test-Path $LogPath)) {
        return
    }

    $LogFile = Get-Item -LiteralPath $LogPath
    if ($LogFile.Length -le $MaxLogSizeBytes) {
        return
    }

    $RotatedPath = "$LogPath.1"
    if (Test-Path $RotatedPath) {
        Remove-Item -LiteralPath $RotatedPath -Force
    }

    Move-Item -LiteralPath $LogPath -Destination $RotatedPath -Force
    New-Item -ItemType File -Path $LogPath -Force | Out-Null
}

function Write-AriadneLog {
    param(
        [string]$Level,
        [string]$Message
    )

    Rotate-AriadneLogIfNeeded
    $Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $Line = "$Timestamp [$Level] $Message"
    Add-Content -LiteralPath $LogPath -Value $Line -Encoding utf8
}

function Get-RetryQueue { if (!(Test-Path $RetryQueuePath)) { return @() }; try { return @(Get-Content -LiteralPath $RetryQueuePath -Raw | ConvertFrom-Json) } catch { Write-AriadneLog -Level "ERROR" -Message "Retry queue is invalid JSON: $($_.Exception.Message)"; return @() } }
function Save-RetryQueue($Queue) { @($Queue) | ConvertTo-Json -Depth 6 | Out-File -LiteralPath $RetryQueuePath -Encoding utf8 -NoNewline }
function Queue-IngestionFailure([string]$Name,[string]$Reason,[bool]$Retryable) {
    $Queue=[System.Collections.ArrayList]@(Get-RetryQueue); $record=$Queue | Where-Object {$_.source_name -eq $Name} | Select-Object -First 1
    if($record){[void]$Queue.Remove($record);$attempt=[int]$record.attempts+1}else{$attempt=1}
    $permanent=(-not $Retryable) -or $attempt -ge $RetryLimit; $next=(Get-Date).AddMinutes($RetryDelayMinutes)
    [void]$Queue.Add([pscustomobject]@{source_name=$Name;attempts=$attempt;status=$(if($permanent){'permanent'}else{'pending'});last_reason=$Reason;last_attempt=(Get-Date -Format 's');next_attempt=$next.ToString('s')})
    Save-RetryQueue $Queue
    Write-AriadneLog -Level $(if($permanent){'ERROR'}else{'WARNING'}) -Message "Ingestion failure queued: $Name attempts=$attempt status=$(if($permanent){'permanent'}else{'pending'}) reason=$Reason"
    return $permanent
}
function Restore-DueRetries {
    $Queue=[System.Collections.ArrayList]@(Get-RetryQueue); $changed=$false
    foreach($r in @($Queue)){if($r.status -eq 'pending' -and [datetime]$r.next_attempt -le (Get-Date)){$p=Join-Path $Failed $r.source_name;if(Test-Path -LiteralPath $p){Move-Item -LiteralPath $p -Destination (Join-Path $Inbox $r.source_name) -Force;Write-AriadneLog -Level 'INFO' -Message "Retrying queued ingest: $($r.source_name)";$changed=$true}else{ $r.status='permanent';$r.last_reason='Retry source missing from Failed folder';$changed=$true }}}
    if($changed){Save-RetryQueue $Queue}
}

function Get-NormalizedMarkdownContent {
    param([string]$Content)

    # Deliberately limited normalization: line endings, trailing whitespace, and
    # trailing blank lines should not create a new content identity.
    $Lines = (($Content -replace "`r`n?", "`n") -split "`n", -1) | ForEach-Object { $_.TrimEnd() }
    return (($Lines -join "`n").TrimEnd("`n"))
}

function Get-Sha256Hex {
    param([string]$Content)

    $Sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $Bytes = [System.Text.Encoding]::UTF8.GetBytes($Content)
        return ([System.BitConverter]::ToString($Sha256.ComputeHash($Bytes))).Replace("-", "").ToLowerInvariant()
    } finally {
        $Sha256.Dispose()
    }
}

function Get-FrontMatterValue {
    param([string]$Content, [string[]]$Keys)

    $FrontMatter = [regex]::Match($Content, '(?ms)^---\s*$\r?\n(.*?)^---\s*$')
    if (!$FrontMatter.Success) { return $null }
    foreach ($Key in $Keys) {
        $Pattern = ("(?im)^{0}\s*:\s*(.+?)\s*$" -f [regex]::Escape($Key))
        $Match = [regex]::Match($FrontMatter.Groups[1].Value, $Pattern)
        if ($Match.Success) { return $Match.Groups[1].Value.Trim().Trim([char]34, [char]39) }
    }
    return $null
}

function Get-CanonicalSourceUrl {
    param([string]$Url)

    if ([string]::IsNullOrWhiteSpace($Url)) { return $null }
    try {
        $Uri = [System.Uri]$Url
        $Builder = [System.UriBuilder]$Uri
        $Builder.Fragment = ""
        $Parameters = [System.Web.HttpUtility]::ParseQueryString($Uri.Query)
        foreach ($Key in @($Parameters.AllKeys)) {
            if ($Key -match '^(utm_.+|fbclid|gclid)$') { $Parameters.Remove($Key) }
        }
        $Builder.Query = $Parameters.ToString()
        return $Builder.Uri.AbsoluteUri.TrimEnd('/')
    } catch {
        return $Url.Trim()
    }
}

function Get-SourceIdentity {
    param([string]$Content)

    $FrontMatterUrl = Get-FrontMatterValue -Content $Content -Keys @('source_url', 'canonical_url', 'url', 'link')
    $Urls = @([regex]::Matches($Content, 'https?://[^\s\]>)"]+') | ForEach-Object { $_.Value.TrimEnd('.', ',', ';', ':') })
    $SourceUrl = if ($FrontMatterUrl) { $FrontMatterUrl } elseif ($Urls.Count -gt 0) { $Urls[0] } else { $null }
    $CanonicalUrl = Get-CanonicalSourceUrl -Url $SourceUrl
    $YouTubeMatch = [regex]::Match(($Urls + @($CanonicalUrl) -join "`n"), '(?i)(?:youtube\.com/(?:watch\?[^\s]*?v=|shorts/|embed/|live/)|youtu\.be/)([A-Za-z0-9_-]{11})')
    $YouTubeVideoId = if ($YouTubeMatch.Success) { $YouTubeMatch.Groups[1].Value } else { $null }
    $PageTitle = Get-FrontMatterValue -Content $Content -Keys @('title', 'page_title')
    if ([string]::IsNullOrWhiteSpace($PageTitle)) {
        $Heading = [regex]::Match($Content, '(?m)^#\s+(.+?)\s*$')
        if ($Heading.Success) { $PageTitle = $Heading.Groups[1].Value.Trim() }
    }
    $ChannelAuthor = Get-FrontMatterValue -Content $Content -Keys @('channel', 'author', 'creator', 'publisher')
    $PublicationDate = Get-FrontMatterValue -Content $Content -Keys @('publication_date', 'published_date', 'date', 'published')
    $ContentSha256 = Get-Sha256Hex -Content (Get-NormalizedMarkdownContent -Content $Content)
    $DocumentId = if ($YouTubeVideoId) { "youtube:$YouTubeVideoId" } elseif ($CanonicalUrl) { "url:$CanonicalUrl" } else { "sha256:$ContentSha256" }

    return [pscustomobject][ordered]@{
        document_id      = $DocumentId
        source_url       = $CanonicalUrl
        youtube_video_id = $YouTubeVideoId
        page_title       = $PageTitle
        channel_author   = $ChannelAuthor
        publication_date = $PublicationDate
        content_sha256   = $ContentSha256
    }
}

function Get-EntryDocumentId {
    param($Entry)

    if ($Entry.document_id) { return $Entry.document_id }
    $ProcessedPath = if ($Entry.processed_path) { Join-Path $Vault $Entry.processed_path } else { $null }
    if ($ProcessedPath -and (Test-Path -LiteralPath $ProcessedPath)) {
        return (Get-SourceIdentity -Content (Get-Content -LiteralPath $ProcessedPath -Raw)).document_id
    }
    if ($Entry.content_sha256) { return "sha256:$($Entry.content_sha256)" }
    return $null
}

function Find-ExistingDocument {
    param([string]$DocumentId)

    foreach ($Entry in @(Get-LibraryEntries)) {
        if ((Get-EntryDocumentId -Entry $Entry) -eq $DocumentId) { return $Entry }
    }
    return $null
}

function Get-AvailablePath {
    param([string]$Directory, [string]$FileName)

    $Candidate = Join-Path $Directory $FileName
    if (!(Test-Path -LiteralPath $Candidate)) { return $Candidate }
    return Join-Path $Directory ("{0}-{1}{2}" -f [System.IO.Path]::GetFileNameWithoutExtension($FileName), (Get-Date -Format 'yyyyMMdd-HHmmss'), [System.IO.Path]::GetExtension($FileName))
}

function Write-DuplicateReport {
    param($SourceIdentity, $ExistingEntry, [string]$SourceName)

    $ReportPath = Get-AvailablePath -Directory $Duplicates -FileName ("{0}.duplicate.md" -f [System.IO.Path]::GetFileNameWithoutExtension($SourceName))
    @"
# Ariadne Duplicate Document

Detected: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")

Incoming Source:
$SourceName

Document ID:
$($SourceIdentity.document_id)

Matching Original:
$($ExistingEntry.source_name)

Original Processed Path:
$($ExistingEntry.processed_path)

Source URL:
$($SourceIdentity.source_url)

YouTube Video ID:
$($SourceIdentity.youtube_video_id)

Page Title:
$($SourceIdentity.page_title)

Channel/Author:
$($SourceIdentity.channel_author)

Publication Date:
$($SourceIdentity.publication_date)

Normalized Markdown SHA-256:
$($SourceIdentity.content_sha256)
"@ | Out-File -LiteralPath $ReportPath -Encoding utf8
    return $ReportPath
}

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

    if ($null -eq $Parsed) {
        return @{
            IsValid = $false
            FailureReason = "Parsed response is null"
        }
    }

    $RequiredFields = @("primary_topic", "secondary_domains", "subtopics", "source_language", "is_new_topic", "reason", "tags", "links", "entities", "map_entry", "summary")
    foreach ($Field in $RequiredFields) {
        if ($null -eq $Parsed.$Field) {
            return @{
                IsValid = $false
                FailureReason = "Missing required field: $Field"
            }
        }
    }

    if ($Parsed.primary_topic -isnot [string] -or [string]::IsNullOrWhiteSpace($Parsed.primary_topic)) {
        return @{
            IsValid = $false
            FailureReason = "primary_topic is missing or not a non-empty string"
        }
    }
    if ($AllowedPrimaryTopics -notcontains $Parsed.primary_topic) {
        return @{
            IsValid = $false
            FailureReason = "Invalid primary_topic: $($Parsed.primary_topic)"
        }
    }
    if ($Parsed.secondary_domains -is [string]) {
        return @{ IsValid = $false; FailureReason = "secondary_domains must be an array, not a string" }
    }
    $SecondaryDomainList = @($Parsed.secondary_domains)
    if ($SecondaryDomainList.Count -gt 3) {
        return @{ IsValid = $false; FailureReason = "secondary_domains cannot contain more than three domains" }
    }
    foreach ($Domain in $SecondaryDomainList) {
        if ($Domain -isnot [string] -or $AllowedPrimaryTopics -notcontains $Domain -or $Domain -eq $Parsed.primary_topic) {
            return @{ IsValid = $false; FailureReason = "secondary_domains must contain distinct canonical domains other than primary_topic" }
        }
    }
    if (@($SecondaryDomainList | Select-Object -Unique).Count -ne $SecondaryDomainList.Count) {
        return @{ IsValid = $false; FailureReason = "secondary_domains contains duplicates" }
    }
    if ($Parsed.source_language -isnot [string] -or [string]::IsNullOrWhiteSpace($Parsed.source_language)) {
        return @{
            IsValid = $false
            FailureReason = "source_language is missing or not a non-empty string"
        }
    }
    if ($Parsed.reason -isnot [string] -or [string]::IsNullOrWhiteSpace($Parsed.reason)) {
        return @{
            IsValid = $false
            FailureReason = "reason is missing or not a non-empty string"
        }
    }
    if ($Parsed.map_entry -isnot [string] -or [string]::IsNullOrWhiteSpace($Parsed.map_entry)) {
        return @{
            IsValid = $false
            FailureReason = "map_entry is missing or not a non-empty string"
        }
    }
    if ($Parsed.summary -isnot [string] -or [string]::IsNullOrWhiteSpace($Parsed.summary)) {
        return @{
            IsValid = $false
            FailureReason = "summary is missing or not a non-empty string"
        }
    }
    if ($Parsed.is_new_topic -isnot [bool]) {
        return @{
            IsValid = $false
            FailureReason = "is_new_topic is not a boolean"
        }
    }
    if ($Parsed.subtopics -is [string]) {
        return @{
            IsValid = $false
            FailureReason = "subtopics must be an array, not a string"
        }
    }
    if ($Parsed.tags -is [string]) {
        return @{
            IsValid = $false
            FailureReason = "tags must be an array, not a string"
        }
    }
    if ($Parsed.links -is [string]) {
        return @{
            IsValid = $false
            FailureReason = "links must be an array, not a string"
        }
    }
    if ($Parsed.entities -is [string]) { return @{ IsValid = $false; FailureReason = "entities must be an array, not a string" } }

    $SubtopicList = @($Parsed.subtopics)
    $TagList = @($Parsed.tags)
    $LinkList = @($Parsed.links); $EntityList = @($Parsed.entities)
    foreach ($Subtopic in $SubtopicList) {
        if ($Subtopic -isnot [string]) {
            return @{
                IsValid = $false
                FailureReason = "subtopics contains non-string value"
            }
        }
    }
    foreach ($Tag in $TagList) {
        if ($Tag -isnot [string]) {
            return @{
                IsValid = $false
                FailureReason = "tags contains non-string value"
            }
        }
    }
    foreach ($Link in $LinkList) {
        if ($Link -isnot [string]) {
            return @{
                IsValid = $false
                FailureReason = "links contains non-string value"
            }
        }
    }
    foreach ($Entity in $EntityList) { if ($Entity -isnot [string]) { return @{ IsValid = $false; FailureReason = "entities contains non-string value" } } }

    if ($Parsed.map_entry -match '[\r\n]') {
        return @{
            IsValid = $false
            FailureReason = "map_entry contains newline"
        }
    }
    if ($Parsed.summary -match '(^|\n)\s*#') {
        return @{
            IsValid = $false
            FailureReason = "summary contains markdown heading"
        }
    }

    return @{
        IsValid = $true
        FailureReason = $null
    }
}

function ConvertTo-AriadneReply {
    param(
        [string]$RawReply,
        [string]$AttemptLabel = "Attempt"
    )

    $Candidate = $RawReply.Trim()
    $Candidate = $Candidate -replace '^```json\s*', '' -replace '^```\s*', '' -replace '\s*```$', ''

    $Parsed = $null
    try {
        $Parsed = $Candidate | ConvertFrom-Json
    } catch {
        Write-AriadneLog -Level "WARNING" -Message "Invalid JSON ($AttemptLabel)"
        return @{
            Parsed = $null
            FailureReason = "Invalid JSON"
        }
    }

    # Normalise recoverable schema drift before rejecting the document. These defaults
    # contain no inferred facts: they only make optional arrays empty and remove
    # duplicate/contradictory domain labels.
    foreach($Field in @('secondary_domains','subtopics','tags','links','entities')){
        if($null -eq $Parsed.$Field -or $Parsed.$Field -is [string]){$Parsed|Add-Member -NotePropertyName $Field -NotePropertyValue @() -Force}
    }
    $Parsed.secondary_domains=@($Parsed.secondary_domains|Where-Object {$_ -and $_ -ne $Parsed.primary_topic}|Select-Object -Unique|Select-Object -First 3)
    if($null -eq $Parsed.map_entry -or [string]::IsNullOrWhiteSpace("$($Parsed.map_entry)")){$Parsed|Add-Member -NotePropertyName map_entry -NotePropertyValue 'Document pending classification review.' -Force}
    if($null -eq $Parsed.summary -or [string]::IsNullOrWhiteSpace("$($Parsed.summary)")){$Parsed|Add-Member -NotePropertyName summary -NotePropertyValue 'The document was ingested successfully but did not include a model-generated summary.' -Force}
    if($null -eq $Parsed.reason -or [string]::IsNullOrWhiteSpace("$($Parsed.reason)")){$Parsed|Add-Member -NotePropertyName reason -NotePropertyValue 'Classification completed with the available model fields.' -Force}
    if($null -eq $Parsed.primary_topic -or [string]::IsNullOrWhiteSpace("$($Parsed.primary_topic)")){$Parsed|Add-Member -NotePropertyName primary_topic -NotePropertyValue 'Archive' -Force}
    $ValidationResult = Test-AriadneResponse -Parsed $Parsed
    if ($ValidationResult.IsValid) {
        return @{
            Parsed = $Parsed
            FailureReason = $null
        }
    }

    Write-AriadneLog -Level "WARNING" -Message "$($ValidationResult.FailureReason) ($AttemptLabel)"

    return @{
        Parsed = $null
        FailureReason = $ValidationResult.FailureReason
    }
}

function Get-AriadneSchemaExample {
    return '{"primary_topic":"AI & LLMs","secondary_domains":["Knowledge Management","Infrastructure"],"subtopics":["agent workflows","local models"],"source_language":"en","is_new_topic":false,"reason":"The document discusses AI tools and model behavior.","tags":["ai","llms"],"links":["Codex","Claude Code"],"entities":["Codex","Claude Code"],"map_entry":"Example document - concise description of the source.","summary":"This document discusses AI tools, model behavior, and practical workflow implications. It compares approaches and highlights relevant concepts. The source is useful as a reference for future retrieval."}'
}

function Get-AllowedPrimaryTopicsText {
    return ($AllowedPrimaryTopics -join ", ")
}

function Get-AriadneResponsePreview {
    param([string]$Response, [int]$MaximumLength = 1000)

    if ([string]::IsNullOrWhiteSpace($Response)) { return "<empty>" }
    $Preview = ($Response -replace '\s+', ' ').Trim()
    if ($Preview.Length -gt $MaximumLength) { return $Preview.Substring(0, $MaximumLength) + "..." }
    return $Preview
}

function Test-AriadnePermanentInvocationFailure {
    param([string]$Message)

    # Configuration/authentication failures will not improve with another call.
    return $Message -match '(?i)(model.*not found|unknown model|invalid model|unauthori[sz]ed|forbidden|authentication|invalid request|unsupported)'
}

function Invoke-AriadneModel {
    param(
        [string]$Prompt,
        [string]$DocumentName
    )

    $SchemaExample = Get-AriadneSchemaExample
    $AllowedTopicText = Get-AllowedPrimaryTopicsText
    $MaxAttempts = 3
    $Diagnostics = [System.Collections.Generic.List[object]]::new()
    $LastRawReply = ""
    $LastFailureReason = $null

    for ($Attempt = 1; $Attempt -le $MaxAttempts; $Attempt++) {
        if ($Attempt -gt 1) {
            $BackoffSeconds = [math]::Pow(2, $Attempt - 2)
            Write-AriadneLog -Level "INFO" -Message "Retrying ${DocumentName}: attempt $Attempt of $MaxAttempts after $BackoffSeconds second(s)"
            Start-Sleep -Seconds $BackoffSeconds
        }

        $AttemptKind = switch ($Attempt) { 1 { "initial" } 2 { "repair" } default { "strict-repair" } }
        $AttemptPrompt = $Prompt
        if ($Attempt -eq 2) {
            $AttemptPrompt = @"
$Prompt

----- RETRY: CORRECT THE PREVIOUS RESPONSE -----
Your previous response failed validation: $LastFailureReason
Return ONLY one valid JSON object. No markdown fences or explanations.
Use exactly these keys: primary_topic, secondary_domains, subtopics, source_language, is_new_topic, reason, tags, links, entities, map_entry, summary
primary_topic must be one of: $AllowedTopicText
Example:
$SchemaExample
Previous response preview:
$(Get-AriadneResponsePreview -Response $LastRawReply)
"@
        } elseif ($Attempt -eq 3) {
            $AttemptPrompt = @"
$Prompt

----- STRICT JSON RETRY -----
Previous failure: $LastFailureReason
Return exactly one JSON object and nothing else. Do not use markdown fences.
All of secondary_domains, subtopics, tags, links, and entities must be JSON arrays. is_new_topic must be true or false.
Use exactly these keys: primary_topic, secondary_domains, subtopics, source_language, is_new_topic, reason, tags, links, entities, map_entry, summary
primary_topic must be one of: $AllowedTopicText
If uncertain, return this valid fallback object exactly:
{"primary_topic":"Archive","secondary_domains":[],"subtopics":[],"source_language":"en","is_new_topic":false,"reason":"Could not confidently classify the document.","tags":[],"links":[],"entities":[],"map_entry":"Unclassified document pending review.","summary":"The document could not be confidently classified from the provided content."}
"@
        }

        $Stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
        $RawReply = ""
        $ReplyResult = $null
        $Retryable = $true
        try {
            $Body = @{ model = "gpt-oss:20b"; prompt = $AttemptPrompt; stream = $false } | ConvertTo-Json -Depth 5
            $Response = Invoke-RestMethod -Uri "http://localhost:11434/api/generate" -Method Post -ContentType "application/json" -Body $Body
            $RawReply = if ($null -eq $Response.response) { "" } else { $Response.response.Trim() }
            $ReplyResult = ConvertTo-AriadneReply -RawReply $RawReply -AttemptLabel "Attempt $Attempt"
            $LastFailureReason = $ReplyResult.FailureReason
        } catch {
            $LastFailureReason = "Model invocation failed: $($_.Exception.Message)"
            $Retryable = -not (Test-AriadnePermanentInvocationFailure -Message $_.Exception.Message)
        } finally {
            $Stopwatch.Stop()
        }

        $LastRawReply = $RawReply
        $Diagnostics.Add([pscustomobject]@{
            attempt = $Attempt; kind = $AttemptKind; duration_ms = $Stopwatch.ElapsedMilliseconds
            failure_reason = $LastFailureReason; response_length = $RawReply.Length
            response_preview = Get-AriadneResponsePreview -Response $RawReply; retryable = $Retryable
        })

        if ($ReplyResult -and $ReplyResult.Parsed) {
            Write-AriadneLog -Level "INFO" -Message "Model attempt $Attempt succeeded for $DocumentName in $($Stopwatch.ElapsedMilliseconds)ms"
            return @{ Parsed = $ReplyResult.Parsed; RawReply = $RawReply; FailureReason = $null; Diagnostics = @($Diagnostics) }
        }

        Write-AriadneLog -Level "WARNING" -Message "Model attempt $Attempt failed for ${DocumentName}: $LastFailureReason; duration=$($Stopwatch.ElapsedMilliseconds)ms; response_length=$($RawReply.Length); retryable=$Retryable"
        if (!$Retryable) { break }
    }

    return @{ Parsed = $null; RawReply = $LastRawReply; FailureReason = $LastFailureReason; Diagnostics = @($Diagnostics) }
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

function Resolve-ExistingWikiLinks {
    param([string[]]$Candidates)

    # Ingest never creates concept pages. It only turns an exact model suggestion
    # into a link when a matching wiki page already exists.
    $Pages = @{}
    Get-ChildItem -LiteralPath $Wiki -Recurse -File -Filter *.md | ForEach-Object {
        if ($_.Name -ne "README.md") {
            $Pages[$_.BaseName.Trim().ToLowerInvariant()] = $_.BaseName
        }
    }

    $Resolved = [System.Collections.Generic.List[string]]::new()
    foreach ($Candidate in @($Candidates)) {
        if ([string]::IsNullOrWhiteSpace($Candidate)) { continue }
        $Key = $Candidate.Trim().ToLowerInvariant()
        if ($Pages.ContainsKey($Key)) {
            $Target = "[[{0}]]" -f $Pages[$Key]
            if (-not $Resolved.Contains($Target)) { $Resolved.Add($Target) }
        }
    }

    return @($Resolved)
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
        document_id     = $Entry.document_id
        source_url      = $Entry.source_url
        youtube_video_id = $Entry.youtube_video_id
        page_title      = $Entry.page_title
        channel_author  = $Entry.channel_author
        publication_date = $Entry.publication_date
        content_sha256  = $Entry.content_sha256
        primary_topic   = $PrimaryTopic
        secondary_domains = if ($Entry.secondary_domains) { @($Entry.secondary_domains) } else { @() }
        subtopics       = @($Subtopics)
        source_language = if ($Entry.source_language) { $Entry.source_language } else { "en" }
        reason          = $Entry.reason
        tags            = @($Entry.tags)
        links           = @($Entry.links)
        entities        = @($Entry.entities)
        people          = @($Entry.people)
        related_notes   = @($Entry.related_notes)
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
        $SourceIdentity,
        [string]$PrimaryTopic,
        [string[]]$SecondaryDomains,
        [string[]]$Subtopics,
        [string]$SourceLanguage,
        [string]$Reason,
        [string[]]$Tags,
        [string[]]$Links,
        [string[]]$Entities,
        [string[]]$People,
        [string[]]$RelatedNotes,
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
        document_id     = $SourceIdentity.document_id
        source_url      = $SourceIdentity.source_url
        youtube_video_id = $SourceIdentity.youtube_video_id
        page_title      = $SourceIdentity.page_title
        channel_author  = $SourceIdentity.channel_author
        publication_date = $SourceIdentity.publication_date
        content_sha256  = $SourceIdentity.content_sha256
        primary_topic   = $PrimaryTopic
        secondary_domains = @($SecondaryDomains)
        subtopics       = @($Subtopics)
        source_language = $SourceLanguage
        reason          = $Reason
        tags            = @($Tags)
        links           = @($Links)
        entities        = @($Entities)
        people          = @($People)
        related_notes   = @($RelatedNotes)
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
        [string[]]$SecondaryDomains,
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
    $ResolvedLinks = @(Resolve-ExistingWikiLinks -Candidates @($SecondaryDomains + $Links))
    $LinkLine = if ($ResolvedLinks.Count -gt 0) { $ResolvedLinks -join ", " } else { "None" }
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
Write-AriadneLog -Level "INFO" -Message "Ariadne started"

$ProcessedThisRun = 0
$SucceededThisRun = 0
$FailedThisRun = 0
$DuplicatesThisRun = 0

try {
    Restore-DueRetries
    $InboxItems = Get-ChildItem $Inbox -Filter *.md
    if ($MaxItemsPerRun -gt 0) {
        $InboxItems = $InboxItems | Select-Object -First $MaxItemsPerRun
    }

    $AllowedTopicText = Get-AllowedPrimaryTopicsText

    foreach ($InboxItem in $InboxItems) {
        try {
            $Document      = Get-Content -LiteralPath $InboxItem.FullName -Raw
            $SourceIdentity = Get-SourceIdentity -Content $Document
            $ExistingDocument = Find-ExistingDocument -DocumentId $SourceIdentity.document_id

            if ($ExistingDocument) {
                $DuplicateReport = Write-DuplicateReport -SourceIdentity $SourceIdentity -ExistingEntry $ExistingDocument -SourceName $InboxItem.Name
                $DuplicateDestination = Get-AvailablePath -Directory $Duplicates -FileName $InboxItem.Name
                Move-Item -LiteralPath $InboxItem.FullName -Destination $DuplicateDestination
                Write-Host "Duplicate: $($InboxItem.Name) matches $($ExistingDocument.source_name)"
                Write-Host "Moved    : $DuplicateDestination"
                Write-Host "Report   : $DuplicateReport"
                Write-AriadneLog -Level "INFO" -Message "Duplicate skipped: $($InboxItem.Name) DocumentId=$($SourceIdentity.document_id) Original=$($ExistingDocument.source_name)"
                $ProcessedThisRun++
                $DuplicatesThisRun++
                continue
            }

            $KnowledgeMap  = Get-Content $KnowledgeMapPath -Raw
            $AriadnePrompt = Get-Content $AriadnePromptPath -Raw

            Write-Host "Processing: $($InboxItem.Name)"
            Write-AriadneLog -Level "INFO" -Message "Processing: $($InboxItem.Name)"

            $Prompt = @"
$AriadnePrompt

Allowed primary topics:
$AllowedTopicText

$KnowledgeMap

----- DOCUMENT -----

$Document
"@

            $ModelResult = Invoke-AriadneModel -Prompt $Prompt -DocumentName $InboxItem.Name
            $RawReply = $ModelResult.RawReply
            $Parsed = $ModelResult.Parsed
            $FailureReason = $ModelResult.FailureReason
            $AttemptDiagnostics = if ($ModelResult.Diagnostics) {
                (@($ModelResult.Diagnostics) | ForEach-Object {
                    "Attempt $($_.attempt) [$($_.kind)]: $($_.failure_reason)`n  Duration: $($_.duration_ms)ms; Response length: $($_.response_length); Retryable: $($_.retryable)`n  Preview: $($_.response_preview)"
                }) -join "`n`n"
            } else { "No attempt diagnostics available." }

            $ReviewFile = Join-Path $Review ($InboxItem.BaseName + ".review.md")

            if ($Parsed) {
                # Second pass: compare the classified note with the existing vault, then materialise
                # concept/entity hubs before any final graph records are written.
                $Parsed = & (Join-Path $System "Invoke-GraphLinking.ps1") -Classification $Parsed -SourceName $InboxItem.Name -Document $Document -Vault $Vault
                Update-KnowledgeMap -Topic $Parsed.primary_topic -Reason $Parsed.reason -MapEntry $Parsed.map_entry

                $NewTag = if ($Parsed.is_new_topic) { " (new topic)" } else { "" }
                $SubtopicLine = if (@($Parsed.subtopics).Count -gt 0) { $Parsed.subtopics -join ", " } else { "None" }

                $Header = @"
# Ariadne Review

Source:
$($InboxItem.Name)

Processed:
$(Get-Date -Format "yyyy-MM-dd HH:mm:ss")

Document ID:
$($SourceIdentity.document_id)

Source URL:
$($SourceIdentity.source_url)

YouTube Video ID:
$($SourceIdentity.youtube_video_id)

Page Title:
$($SourceIdentity.page_title)

Channel/Author:
$($SourceIdentity.channel_author)

Publication Date:
$($SourceIdentity.publication_date)

Normalized Markdown SHA-256:
$($SourceIdentity.content_sha256)

Primary Topic:
$($Parsed.primary_topic)$NewTag

Secondary Domains:
$(if (@($Parsed.secondary_domains).Count) { $Parsed.secondary_domains -join ", " } else { "None" })

Source Language:
$($Parsed.source_language)

Subtopics:
$SubtopicLine

Tags:
$($Parsed.tags -join ", ")

Links:
$($Parsed.links -join ", ")

Entities:
$($Parsed.entities -join ", ")

Related Notes:
$($Parsed.related_notes -join ", ")

---

$($Parsed.summary)
"@
                $Header | Out-File -LiteralPath $ReviewFile -Encoding utf8

                $WikiFileName = Update-WikiPage `
                    -Topic $Parsed.primary_topic `
                    -SecondaryDomains @($Parsed.secondary_domains) `
                    -Reason $Parsed.reason `
                    -Summary $Parsed.summary `
                    -MapEntry $Parsed.map_entry `
                    -Tags @($Parsed.tags) `
                    -Links @($Parsed.links) `
                    -SourceName $InboxItem.Name `
                    -ProcessedFileName $InboxItem.Name `
                    -ReviewFileName ($InboxItem.BaseName + ".review.md")

                Update-LibraryEntry `
                    -SourceName $InboxItem.Name `
                    -SourceIdentity $SourceIdentity `
                    -PrimaryTopic $Parsed.primary_topic `
                    -SecondaryDomains @($Parsed.secondary_domains) `
                    -Subtopics @($Parsed.subtopics) `
                    -SourceLanguage $Parsed.source_language `
                    -Reason $Parsed.reason `
                    -Tags @($Parsed.tags) `
                    -Links @($Parsed.links) `
                    -Entities @($Parsed.entities) `
                    -People @($Parsed.people) `
                    -RelatedNotes @($Parsed.related_notes) `
                    -Summary $Parsed.summary `
                    -MapEntry $Parsed.map_entry `
                    -ReviewFileName ($InboxItem.BaseName + ".review.md") `
                    -ProcessedFileName $InboxItem.Name `
                    -WikiFileName $WikiFileName

                Write-Host "Filed under: $($Parsed.primary_topic)$NewTag"
                Write-AriadneLog -Level "INFO" -Message "Filed under: $($Parsed.primary_topic)$NewTag"
                Write-AriadneLog -Level "INFO" -Message "Saved review: $([System.IO.Path]::GetFileName($ReviewFile))"
            } else {
                $Header = @"
# Ariadne Review (UNPARSED -- Knowledge Map not updated)

Source:
$($InboxItem.Name)

Processed:
$(Get-Date -Format "yyyy-MM-dd HH:mm:ss")

Document ID:
$($SourceIdentity.document_id)

Source URL:
$($SourceIdentity.source_url)

YouTube Video ID:
$($SourceIdentity.youtube_video_id)

Page Title:
$($SourceIdentity.page_title)

Channel/Author:
$($SourceIdentity.channel_author)

Publication Date:
$($SourceIdentity.publication_date)

Normalized Markdown SHA-256:
$($SourceIdentity.content_sha256)

Failure Reason:
$FailureReason

Attempt Diagnostics:
$AttemptDiagnostics

---

$RawReply
"@
                $Header | Out-File -LiteralPath $ReviewFile -Encoding utf8

                Write-Host "WARNING: model reply was not valid JSON. Review file written, Knowledge Map left untouched."
                Write-AriadneLog -Level "ERROR" -Message "Failed after retry: $FailureReason"
                $IsPermanentInvocationFailure = Test-AriadnePermanentInvocationFailure -Message $FailureReason
                $PermanentlyFailed = Queue-IngestionFailure -Name $InboxItem.Name -Reason $FailureReason -Retryable (-not $IsPermanentInvocationFailure)
                if ($PermanentlyFailed) { Write-AriadneLog -Level "ERROR" -Message "Permanent ingestion failure: $($InboxItem.Name) reason=$FailureReason" }
                Write-AriadneLog -Level "INFO" -Message "Saved review: $([System.IO.Path]::GetFileName($ReviewFile))"
            }

            $Destination = if ($Parsed) {
                Join-Path $Processed $InboxItem.Name
            } else {
                Join-Path $Failed $InboxItem.Name
            }
            Move-Item -LiteralPath $InboxItem.FullName -Destination $Destination -Force

            Write-Host "Saved : $ReviewFile"
            Write-Host "Moved : $Destination"
            Write-Host ""
            if ($Parsed) {
                Write-AriadneLog -Level "INFO" -Message "Moved to Processed: $($InboxItem.Name)"
                $SucceededThisRun++
            } else {
                Write-AriadneLog -Level "INFO" -Message "Moved to Failed: $($InboxItem.Name)"
                $FailedThisRun++
            }
            $ProcessedThisRun++
        } catch {
            Write-AriadneLog -Level "ERROR" -Message "Unexpected exception while processing $($InboxItem.Name): $($_.Exception.Message)"
            throw
        }
    }
} catch {
    Write-AriadneLog -Level "ERROR" -Message "Unexpected exception: $($_.Exception.Message)"
    throw
} finally {
    Write-AriadneLog -Level "INFO" -Message "Run complete. Processed=$ProcessedThisRun Succeeded=$SucceededThisRun Failed=$FailedThisRun Duplicates=$DuplicatesThisRun"
}

Write-Host "Processed $ProcessedThisRun document(s) this run."
if ($DuplicatesThisRun -gt 0) {
    Write-Host "Skipped $DuplicatesThisRun duplicate document(s); see Archive/Duplicates for reports."
}
if ($MaxItemsPerRun -gt 0 -and (Get-ChildItem $Inbox -Filter *.md | Measure-Object).Count -gt 0) {
    Write-Host "Paused after $MaxItemsPerRun items. Run Ariadne again to continue."
}
Write-Host "Finished."
Write-AriadneLog -Level "INFO" -Message "Ariadne finished"
