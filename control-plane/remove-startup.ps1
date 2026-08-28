param(
    [switch]$RemoveLegacyTask
)

$ErrorActionPreference = 'Stop'
$startMenu = [Environment]::GetFolderPath('Programs')
$startup = [Environment]::GetFolderPath('Startup')
$shortcutPaths = @(
    (Join-Path $startMenu 'Ariadne.lnk'),
    (Join-Path $startup 'Ariadne Host.lnk')
)
$legacyTaskName = 'Ariadne Local Control Plane'

foreach ($shortcutPath in $shortcutPaths) {
    if (Test-Path -LiteralPath $shortcutPath -PathType Leaf) {
        Remove-Item -LiteralPath $shortcutPath -Force
        Write-Output "Removed Ariadne shortcut: $shortcutPath"
    } else {
        Write-Output "Ariadne shortcut not present: $shortcutPath"
    }
}

$legacyTask = Get-ScheduledTask -TaskName $legacyTaskName -ErrorAction SilentlyContinue
if ($legacyTask -and $RemoveLegacyTask) {
    Unregister-ScheduledTask -TaskName $legacyTaskName -Confirm:$false
    Write-Output "Removed legacy Scheduled Task: $legacyTaskName"
} elseif ($legacyTask) {
    Write-Warning "Legacy Scheduled Task still exists: $legacyTaskName. Re-run with -RemoveLegacyTask if it is no longer wanted."
}
