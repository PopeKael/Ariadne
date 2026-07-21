[CmdletBinding()]
param(
    [string]$Vault = "D:\Downloads\KnowledgeVault"
)

$ErrorActionPreference = 'Stop'
$Processed = Join-Path $Vault 'Processed'
$Inbox = Join-Path $Vault 'Inbox'
$Failed = Join-Path $Vault 'Failed'
$LogPath = Join-Path $Vault 'Logs\Ariadne.log'

$ProcessedCount = @(Get-ChildItem -LiteralPath $Processed -File -Filter '*.md' | Where-Object { $_.Name -ne 'README.md' }).Count
$InboxCount = @(Get-ChildItem -LiteralPath $Inbox -File -Filter '*.md').Count
$FailedCount = @(Get-ChildItem -LiteralPath $Failed -File -Filter '*.md').Count
$Total = $ProcessedCount + $InboxCount + $FailedCount
$Completed = $ProcessedCount + $FailedCount
$Percent = if ($Total) { [math]::Round(($Completed / $Total) * 100, 1) } else { 0 }

$Started = $null
$Current = $null
$LogLines = @()
if (Test-Path -LiteralPath $LogPath) {
    $LogLines = @(Get-Content -LiteralPath $LogPath)
    $StartLine = @($LogLines | Where-Object { $_ -match 'Ariadne started' }) | Select-Object -Last 1
    if ($StartLine -and $StartLine -match '^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})') {
        $Started = [datetime]::ParseExact($Matches[1], 'yyyy-MM-dd HH:mm:ss', $null)
        $RunLines = @($LogLines | Where-Object {
            $_ -match '^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})' -and
            ([datetime]::ParseExact($Matches[1], 'yyyy-MM-dd HH:mm:ss', $null) -ge $Started)
        })
        $CurrentLine = @($RunLines | Where-Object { $_ -match '\[INFO\] Processing: ' }) | Select-Object -Last 1
        if ($CurrentLine) { $Current = $CurrentLine -replace '^.*\] Processing: ', '' }
    }
}

Write-Host ''
Write-Host 'Ariadne Reclassification Status' -ForegroundColor Cyan
Write-Host '--------------------------------'
Write-Host ("Completed : {0} / {1} ({2}%)" -f $Completed, $Total, $Percent)
Write-Host ("Remaining : {0} in Inbox" -f $InboxCount)
Write-Host ("Failed    : {0}" -f $FailedCount)
if ($Started) {
    Write-Host ("Started   : {0}" -f $Started.ToString('yyyy-MM-dd HH:mm:ss'))
    Write-Host ("Elapsed   : {0}" -f ((Get-Date) - $Started).ToString('dd\.hh\:mm\:ss'))
}
if ($Current) { Write-Host ("Current   : {0}" -f $Current) }
Write-Host ''
