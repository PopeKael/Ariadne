# Run Ingest.ps1

$ScriptPath = "D:\Downloads\KnowledgeVault\00_System"

Set-Location $ScriptPath

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force

try {
    .\ariadne.ps1
}
catch {
    Write-Host ""
    Write-Host "Ingest failed:"
    Write-Host $_.Exception.Message -ForegroundColor Red
}

Write-Host ""
Read-Host "Press Enter to exit"