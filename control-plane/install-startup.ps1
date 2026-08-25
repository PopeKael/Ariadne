param(
    [switch]$RemoveLegacyTask
)

$ErrorActionPreference = 'Stop'

$controlPlane = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $controlPlane
$startup = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startup 'Ariadne Host.lnk'
$legacyTaskName = 'Ariadne Local Control Plane'
$hostCandidates = @(
    (Join-Path $controlPlane 'host\target-msvc\release\ariadne-host.exe'),
    (Join-Path $controlPlane 'host\target\release\ariadne-host.exe'),
    (Join-Path $controlPlane 'host\ariadne-host.exe')
)
$hostExe = $hostCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1

if (-not $hostExe) {
    throw "Ariadne Host executable was not found. Build it first with: cargo build --release --manifest-path `"$controlPlane\host\Cargo.toml`""
}

$legacyTask = Get-ScheduledTask -TaskName $legacyTaskName -ErrorAction SilentlyContinue
if ($legacyTask -and -not $RemoveLegacyTask) {
    throw "Legacy Scheduled Task '$legacyTaskName' exists. No Startup shortcut was installed, to avoid duplicate Ariadne instances. Re-run with -RemoveLegacyTask after confirming migration, or remove it manually."
}
if ($legacyTask -and $RemoveLegacyTask) {
    Unregister-ScheduledTask -TaskName $legacyTaskName -Confirm:$false
    Write-Output "Removed legacy Scheduled Task: $legacyTaskName"
}

New-Item -ItemType Directory -Force -Path $startup | Out-Null
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$resolvedHost = (Resolve-Path -LiteralPath $hostExe).Path
$shortcut.TargetPath = $resolvedHost
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description = 'Ariadne Rust Host'
$shortcut.IconLocation = "$resolvedHost,0"
$shortcut.Save()

Write-Output "Installed per-user Startup shortcut: $shortcutPath"
Write-Output "Target: $resolvedHost"
Write-Output "Legacy Scheduled Task removed or absent: $(-not [bool]$legacyTask -or $RemoveLegacyTask)"
