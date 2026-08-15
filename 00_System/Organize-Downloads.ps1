[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'Medium')]
param(
    [Parameter()]
    [string]$Root = 'D:\Downloads'
)

$ErrorActionPreference = 'Stop'

# Files are classified in this order. Markdown and email files therefore win
# over filename or image/video classification.
$destinations = @{
    Markdown   = Join-Path $Root 'KnowledgeVault\Inbox'
    Email      = Join-Path $Root 'Docs'
    Screenshot = Join-Path $Root 'screenshots'
    Image      = Join-Path $Root 'Images'
    Video      = Join-Path $Root 'Videos'
}

$imageExtensions = @(
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tif', '.tiff',
    '.svg', '.heic', '.avif'
)

$videoExtensions = @(
    '.mp4', '.mov', '.m4v', '.avi', '.mkv', '.webm', '.wmv', '.flv',
    '.mpeg', '.mpg'
)

if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
    throw "Root folder does not exist: $Root"
}

$files = Get-ChildItem -LiteralPath $Root -File
$plan = foreach ($file in $files) {
    $kind = $null

    if ($file.Extension -ieq '.md') {
        $kind = 'Markdown'
    }
    elseif ($file.Extension -ieq '.eml') {
        $kind = 'Email'
    }
    elseif ($file.BaseName -match '(?i)screenshot') {
        $kind = 'Screenshot'
    }
    elseif ($imageExtensions -contains $file.Extension.ToLowerInvariant()) {
        $kind = 'Image'
    }
    elseif ($videoExtensions -contains $file.Extension.ToLowerInvariant()) {
        $kind = 'Video'
    }

    if ($kind) {
        [PSCustomObject]@{
            Kind   = $kind
            Name   = $file.Name
            Source = $file.FullName
            Target = $destinations[$kind]
        }
    }
}

$counts = @{
    Markdown   = 0
    Email      = 0
    Screenshot = 0
    Image      = 0
    Video      = 0
}

$planned = @($plan).Count
$moved = 0
$collisions = 0
$failed = 0

if (-not $WhatIfPreference) {
    foreach ($destination in $destinations.Values) {
        New-Item -ItemType Directory -Path $destination -Force | Out-Null
    }
}

foreach ($item in $plan) {
    $targetPath = Join-Path $item.Target $item.Name

    if (Test-Path -LiteralPath $targetPath) {
        $collisions++
        Write-Output "SKIPPED collision [$($item.Kind)] $($item.Name)"
        continue
    }

    try {
        if ($PSCmdlet.ShouldProcess($item.Source, "Move to $($item.Target)")) {
            Move-Item -LiteralPath $item.Source -Destination $item.Target -ErrorAction Stop
            $counts[$item.Kind]++
            $moved++
        }
    }
    catch {
        $failed++
        Write-Output "FAILED [$($item.Kind)] $($item.Name): $($_.Exception.Message)"
    }
}

$unmatched = @(Get-ChildItem -LiteralPath $Root -File).Count

Write-Output ''
Write-Output 'SUMMARY'
Write-Output "Planned:              $planned"
Write-Output "Moved:                $moved"
Write-Output "  Markdown:           $($counts.Markdown)"
Write-Output "  Emails:             $($counts.Email)"
Write-Output "  Screenshots:        $($counts.Screenshot)"
Write-Output "  Images:             $($counts.Image)"
Write-Output "  Videos:             $($counts.Video)"
Write-Output "Skipped collisions:   $collisions"
Write-Output "Failed:               $failed"
Write-Output "Unmatched left alone: $unmatched"
