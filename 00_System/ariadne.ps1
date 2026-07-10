# Ariadne v0.7
# Process Inbox, ask the model to file the document into the Knowledge Map,
# write a review file, update the Knowledge Map, then move the original to Processed.

$Vault = "D:\Downloads\KnowledgeVault"

$System    = Join-Path $Vault "00_System"
$Inbox     = Join-Path $Vault "Inbox"
$Review    = Join-Path $Vault "Review"
$Processed = Join-Path $Vault "Processed"

foreach ($Folder in @($Review, $Processed)) {
    if (!(Test-Path $Folder)) {
        New-Item -ItemType Directory -Path $Folder | Out-Null
    }
}

$KnowledgeMapPath  = Join-Path $System "KnowledgeMap.md"
$AriadnePromptPath = Join-Path $System "AriadnePrompt.md"

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

Write-Host ""
Write-Host "Ariadne Prompt loaded."
Write-Host ""

Get-ChildItem $Inbox -Filter *.md | ForEach-Object {

    $Document      = Get-Content -LiteralPath $_.FullName -Raw
    $KnowledgeMap  = Get-Content $KnowledgeMapPath -Raw
    $AriadnePrompt = Get-Content $AriadnePromptPath -Raw

    Write-Host "Processing: $($_.Name)"

    $Prompt = @"
$AriadnePrompt

$KnowledgeMap

----- DOCUMENT -----

$Document
"@

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
    # Strip ```json fences in case the model adds them anyway
    $RawReply = $RawReply -replace '^```json\s*', '' -replace '^```\s*', '' -replace '\s*```$', ''

    $Parsed = $null
    try {
        $Parsed = $RawReply | ConvertFrom-Json
    } catch {
        $Parsed = $null
    }

    $ReviewFile = Join-Path $Review ($_.BaseName + ".review.md")

    if ($Parsed) {
        Update-KnowledgeMap -Topic $Parsed.topic -Reason $Parsed.reason -MapEntry $Parsed.map_entry

        $NewTag = if ($Parsed.is_new_topic) { " (new topic)" } else { "" }

        $Header = @"
# Ariadne Review

Source:
$($_.Name)

Processed:
$(Get-Date -Format "yyyy-MM-dd HH:mm:ss")

Topic:
$($Parsed.topic)$NewTag

Tags:
$($Parsed.tags -join ", ")

Links:
$($Parsed.links -join ", ")

---

$($Parsed.summary)
"@
        $Header | Out-File -LiteralPath $ReviewFile -Encoding utf8

        Write-Host "Filed under: $($Parsed.topic)$NewTag"
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

    $Destination = Join-Path $Processed $_.Name
    Move-Item -LiteralPath $_.FullName -Destination $Destination -Force

    Write-Host "Saved : $ReviewFile"
    Write-Host "Moved : $Destination"
    Write-Host ""
    Write-Host "Press Enter to process the next file (Ctrl+C to exit)."
    Read-Host | Out-Null
}

Write-Host "Finished."
