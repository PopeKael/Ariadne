# Commit.ps1
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$message = "KnowledgeVault Snapshot - $timestamp"

git add -A

$status = git status --porcelain
if (-not $status) {
    Write-Host ""
    Write-Host "No changes to commit."
    exit
}

git commit -m $message

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERROR: Commit failed."
    exit 1
}

git push origin main

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "✓ Snapshot committed and pushed."
    Write-Host "  $timestamp"
}
else {
    Write-Host ""
    Write-Host "✗ Commit created, but push FAILED."
    Write-Host ""
    Write-Host "Your changes are safe on this computer."
    Write-Host "Run 'git push origin main' when the connection is working."
    exit 1
}