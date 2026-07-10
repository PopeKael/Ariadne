# Ariadne v0.7
# Interactive Ingestion Service

$Vault = "D:\Downloads\KnowledgeVault"

$System    = Join-Path $Vault "00_System"
$Inbox     = Join-Path $Vault "Inbox"
$Review    = Join-Path $Vault "Review"
$Processed = Join-Path $Vault "Processed"

$LogFile   = Join-Path $System "ingest.log"

foreach ($Folder in @($Review, $Processed)) {
    if (!(Test-Path $Folder)) {
        New-Item -ItemType Directory -Path $Folder | Out-Null
    }
}

$KnowledgeMap  = Get-Content (Join-Path $System "KnowledgeMap.md") -Raw
$AriadnePrompt = Get-Content (Join-Path $System "AriadnePrompt.md") -Raw

Clear-Host

Write-Host ""
Write-Host "========================================="
Write-Host " Ariadne v0.7 Interactive Ingest Service "
Write-Host "========================================="
Write-Host ""
Write-Host "Knowledge Map loaded."
Write-Host "Ariadne Prompt loaded."
Write-Host ""
Write-Host "Press Ctrl+C at any time to exit."
Write-Host ""

while ($true) {

    $NextFile = Get-ChildItem $Inbox -Filter *.md |
                Sort-Object LastWriteTime |
                Select-Object -First 1

    if ($null -eq $NextFile) {

        Write-Host ""
        Write-Host "Inbox empty."
        Write-Host ""
        Read-Host "Press ENTER to check again"
        continue
    }

    Write-Host ""
    Write-Host "Processing: $($NextFile.Name)"
    $Start = Get-Date

    try {

        $Document = Get-Content -LiteralPath $NextFile.FullName -Raw

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

        $ReviewFile = Join-Path $Review ($NextFile.BaseName + ".review.md")

        $Header = @"
# Ariadne Review

Source:
$($NextFile.Name)

Processed:
$(Get-Date -Format "yyyy-MM-dd HH:mm:ss")

Model:
gpt-oss:20b

Ariadne Version:
0.7

---

"@

        ($Header + $Response.response) |
            Out-File -LiteralPath $ReviewFile -Encoding utf8

        $Destination = Join-Path $Processed $NextFile.Name

        Move-Item `
            -LiteralPath $NextFile.FullName `
            -Destination $Destination `
            -Force

        $Elapsed = ((Get-Date) - $Start).TotalSeconds

        Add-Content $LogFile @"

=================================================
$(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
Document : $($NextFile.Name)
Status   : SUCCESS
Elapsed  : $([Math]::Round($Elapsed,2)) sec
=================================================

"@

        Write-Host ""
        Write-Host "✓ Review saved."
        Write-Host "✓ Original moved to Processed."
    }
    catch {

        Add-Content $LogFile @"

=================================================
$(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
Document : $($NextFile.Name)
Status   : ERROR
Reason   : $($_.Exception.Message)
=================================================

"@

        Write-Host ""
        Write-Host "ERROR: $($_.Exception.Message)"
    }

    Write-Host ""
    Read-Host "Press ENTER to process the next document (Ctrl+C to exit)"
}