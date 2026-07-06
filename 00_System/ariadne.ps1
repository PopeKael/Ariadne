# Ariadne v0.6
# Process Inbox, write review files, then move originals to Processed

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

$KnowledgeMap  = Get-Content (Join-Path $System "KnowledgeMap.md") -Raw
$AriadnePrompt = Get-Content (Join-Path $System "AriadnePrompt.md") -Raw

Write-Host ""
Write-Host "Knowledge Map loaded."
Write-Host "Ariadne Prompt loaded."
Write-Host ""

Get-ChildItem $Inbox -Filter *.md | ForEach-Object {

    $Document = Get-Content -LiteralPath $_.FullName -Raw

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

    $ReviewFile = Join-Path $Review ($_.BaseName + ".review.md")

    $Header = @"
# Ariadne Review

Source:
$($_.Name)

Processed:
$(Get-Date -Format "yyyy-MM-dd HH:mm:ss")

Model:
gpt-oss:20b

Ariadne Version:
0.6

---

"@

    ($Header + $Response.response) | Out-File -LiteralPath $ReviewFile -Encoding utf8

    $Destination = Join-Path $Processed $_.Name
    Move-Item -LiteralPath $_.FullName -Destination $Destination -Force

    Write-Host "Saved : $ReviewFile"
    Write-Host "Moved : $Destination"
    Write-Host ""
}

Write-Host "Finished."