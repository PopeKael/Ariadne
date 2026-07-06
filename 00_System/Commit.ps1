# Commit.ps1

Set-Location "D:\Downloads\KnowledgeVault"

# Current timestamp
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

# Stage everything except what's in .gitignore
git add .

# Only commit if something has changed
if ((git status --porcelain).Length -gt 0) {

    git commit -m "KnowledgeVault Snapshot - $timestamp"

    git push origin main

    Write-Host ""
    Write-Host "✓ Snapshot committed and pushed."
    Write-Host "  $timestamp"
}
else {
    Write-Host ""
    Write-Host "No changes to commit."
}