param(
    [switch]$RemoveLegacyTask
)

$ErrorActionPreference = 'Stop'

$controlPlane = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $controlPlane
$startup = [Environment]::GetFolderPath('Startup')
$startMenu = [Environment]::GetFolderPath('Programs')
$startMenuShortcutPath = Join-Path $startMenu 'Ariadne.lnk'
$startupShortcutPath = Join-Path $startup 'Ariadne Host.lnk'
$iconPath = Join-Path $controlPlane 'host\assets\branding\ariadne.ico'
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
if (-not (Test-Path -LiteralPath $iconPath -PathType Leaf)) {
    throw "Ariadne icon was not found: $iconPath"
}

$legacyTask = Get-ScheduledTask -TaskName $legacyTaskName -ErrorAction SilentlyContinue
if ($legacyTask -and -not $RemoveLegacyTask) {
    throw "Legacy Scheduled Task '$legacyTaskName' exists. No Startup shortcut was installed, to avoid duplicate Ariadne instances. Re-run with -RemoveLegacyTask after confirming migration, or remove it manually."
}
if ($legacyTask -and $RemoveLegacyTask) {
    Unregister-ScheduledTask -TaskName $legacyTaskName -Confirm:$false
    Write-Output "Removed legacy Scheduled Task: $legacyTaskName"
}

New-Item -ItemType Directory -Force -Path $startMenu,$startup | Out-Null
$shell = New-Object -ComObject WScript.Shell
$resolvedHost = (Resolve-Path -LiteralPath $hostExe).Path
$resolvedIcon = (Resolve-Path -LiteralPath $iconPath).Path

$shortcutRoots = @($startMenu, $startup, (Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs')) |
    Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Container) } |
    Select-Object -Unique
$existingShortcuts = @(
    foreach ($root in $shortcutRoots) {
        Get-ChildItem -LiteralPath $root -Recurse -Force -File -Filter '*.lnk' -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '(?i)^ariadne.*\.lnk$' }
    }
) | Sort-Object -Property FullName -Unique
foreach ($existing in $existingShortcuts) {
    Remove-Item -LiteralPath $existing.FullName -Force
    Write-Output "Removed duplicate Ariadne shortcut: $($existing.FullName)"
}

function New-AriadneShortcut {
    param(
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] [string]$Description,
        [Parameter(Mandatory)] [string]$TargetPath,
        [Parameter(Mandatory)] [string]$WorkingDirectory,
        [Parameter(Mandatory)] [string]$IconLocation
    )

    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $TargetPath
    $shortcut.WorkingDirectory = $WorkingDirectory
    $shortcut.Description = $Description
    $shortcut.IconLocation = $IconLocation
    $shortcut.Save()
    $verification = $shell.CreateShortcut($Path)
    if ([string]$verification.TargetPath -ne $TargetPath) {
        throw "Shortcut target verification failed for $Path. Expected '$TargetPath', got '$($verification.TargetPath)'."
    }
}

$shortcutIconLocation = "$resolvedIcon,0"
New-AriadneShortcut -Path $startMenuShortcutPath -Description 'Ariadne local AI control plane' -TargetPath $resolvedHost -WorkingDirectory $projectRoot -IconLocation $shortcutIconLocation
New-AriadneShortcut -Path $startupShortcutPath -Description 'Ariadne Rust Host' -TargetPath $resolvedHost -WorkingDirectory $projectRoot -IconLocation $shortcutIconLocation

Write-Output "Installed per-user Start Menu shortcut: $startMenuShortcutPath"
Write-Output "Installed per-user Startup shortcut: $startupShortcutPath"
Write-Output "Target: $resolvedHost"
Write-Output "Icon: $resolvedIcon"
Write-Output "Legacy Scheduled Task removed or absent: $(-not [bool]$legacyTask -or $RemoveLegacyTask)"
