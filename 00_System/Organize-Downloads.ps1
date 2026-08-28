[CmdletBinding(SupportsShouldProcess, ConfirmImpact = 'Medium')]
param(
    [Parameter()]
    [string]$Root = 'D:\Downloads',
    [Parameter()]
    [string]$ConfigPath = '',
    [Parameter()]
    [string]$ResultPath = ''
)

$ErrorActionPreference = 'Stop'
$configuration = $null
if ($ConfigPath) {
    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) { throw "Cleanup configuration file does not exist: $ConfigPath" }
    $configuration = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    if (-not $configuration.sources -or -not $configuration.filing_classes) { throw 'Cleanup configuration must contain sources and filing_classes.' }
    if ([string]$configuration.collision_policy -ne 'skip') { throw 'Only the skip collision policy is supported.' }
}

$imageExtensions = @('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tif', '.tiff', '.svg', '.heic', '.avif')
$videoExtensions = @('.mp4', '.mov', '.m4v', '.avi', '.mkv', '.webm', '.wmv', '.flv', '.mpeg', '.mpg')

function Get-NormalizedPath([string]$PathValue) { return [System.IO.Path]::GetFullPath($PathValue).TrimEnd('\') }
function Test-PathWithin([string]$Candidate, [string]$Parent) {
    $candidatePath = Get-NormalizedPath $Candidate
    $parentPath = Get-NormalizedPath $Parent
    return $candidatePath.Equals($parentPath, [System.StringComparison]::OrdinalIgnoreCase) -or $candidatePath.StartsWith($parentPath + '\', [System.StringComparison]::OrdinalIgnoreCase)
}

$plan = @()
$destinations = [ordered]@{}
$counts = @{}
$sourceRoots = @()
$exclusions = @()

if ($configuration) {
    foreach ($source in @($configuration.sources)) {
        if (-not [bool]$source.enabled) { continue }
        $sourcePath = [string]$source.path
        if (-not (Test-Path -LiteralPath $sourcePath -PathType Container)) { throw "Cleanup source folder does not exist: $sourcePath" }
        $sourceRoots += (Get-NormalizedPath $sourcePath)
    }
    foreach ($class in @($configuration.filing_classes)) {
        if (-not [bool]$class.enabled) { continue }
        $name = [string]$class.name
        $destinations[$name] = [string]$class.destination
        $counts[$name] = 0
    }
    if ($sourceRoots.Count -eq 0) { throw 'Enable at least one Cleanup source folder before running.' }
    if ($counts.Count -eq 0) { throw 'Enable at least one Cleanup filing class before running.' }
    $destinationRoots = @($destinations.Values | ForEach-Object { Get-NormalizedPath ([string]$_) })
    if ($configuration.exclusions) { $exclusions = @($configuration.exclusions | ForEach-Object { [string]$_ }) }
    foreach ($sourceRoot in $sourceRoots) {
        if ([bool]$configuration.recurse) {
            $files = @(Get-ChildItem -LiteralPath $sourceRoot -File -Recurse | Where-Object {
                $fullName = Get-NormalizedPath $_.FullName
                $inDestination = @($destinationRoots | Where-Object { Test-PathWithin $fullName $_ }).Count -gt 0
                $excluded = @($exclusions | Where-Object { Test-PathWithin $fullName (Join-Path $sourceRoot $_) }).Count -gt 0
                -not $inDestination -and -not $excluded
            })
        } else { $files = @(Get-ChildItem -LiteralPath $sourceRoot -File) }
        foreach ($file in $files) {
            $matchedClass = $null
            foreach ($class in @($configuration.filing_classes)) {
                if (-not [bool]$class.enabled) { continue }
                $patterns = @($class.patterns)
                $extensionMatch = @($class.extensions | Where-Object { [string]$_ -ieq $file.Extension }).Count -gt 0
                $patternMatch = @($patterns | Where-Object { $file.BaseName.IndexOf([string]$_, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 }).Count -gt 0
                $matches = if ($patterns.Count -gt 0) { $patternMatch } else { $extensionMatch }
                if ($matches) { $matchedClass = $class; break }
            }
            if ($matchedClass) { $plan += [PSCustomObject]@{ Kind = [string]$matchedClass.name; Name = $file.Name; Source = $file.FullName; SourceRoot = $sourceRoot; Target = [string]$matchedClass.destination } }
        }
    }
} else {
    $sourceRoots = @(Get-NormalizedPath $Root)
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) { throw "Root folder does not exist: $Root" }
    $destinations = [ordered]@{
        Markdown = Join-Path $Root 'KnowledgeVault\Inbox'; Email = Join-Path $Root 'Docs'; Screenshot = Join-Path $Root 'screenshots'; Image = Join-Path $Root 'Images'; Video = Join-Path $Root 'Videos'
    }
    foreach ($key in $destinations.Keys) { $counts[$key] = 0 }
    foreach ($file in @(Get-ChildItem -LiteralPath $Root -File)) {
        $kind = $null
        if ($file.Extension -ieq '.md') { $kind = 'Markdown' }
        elseif ($file.Extension -ieq '.eml') { $kind = 'Email' }
        elseif ($file.BaseName -match '(?i)screenshot') { $kind = 'Screenshot' }
        elseif ($imageExtensions -contains $file.Extension.ToLowerInvariant()) { $kind = 'Image' }
        elseif ($videoExtensions -contains $file.Extension.ToLowerInvariant()) { $kind = 'Video' }
        if ($kind) { $plan += [PSCustomObject]@{ Kind = $kind; Name = $file.Name; Source = $file.FullName; SourceRoot = $sourceRoots[0]; Target = $destinations[$kind] } }
    }
}

$planned = @($plan).Count; $moved = 0; $collisions = 0; $failed = 0; $results = @()
if (-not $WhatIfPreference) {
    foreach ($destination in $destinations.Values) { New-Item -ItemType Directory -Path $destination -Force | Out-Null }
}
foreach ($item in $plan) {
    $targetPath = Join-Path $item.Target $item.Name
    if (Test-Path -LiteralPath $targetPath) {
        $collisions++
        $results += [PSCustomObject]@{
            status = 'duplicate'; file = $item.Name; source = $item.SourceRoot; destination = $item.Target
            reason = 'Destination file already exists; existing file left untouched.'
        }
        Write-Output "SKIPPED collision [$($item.Kind)] $($item.Name)"
        continue
    }
    try {
        if ($PSCmdlet.ShouldProcess($item.Source, "Move to $($item.Target)")) {
            Move-Item -LiteralPath $item.Source -Destination $item.Target -ErrorAction Stop
            $counts[$item.Kind]++; $moved++
            $results += [PSCustomObject]@{
                status = 'moved'; file = $item.Name; source = $item.SourceRoot; destination = $item.Target
                reason = $null
            }
        } elseif ($WhatIfPreference) {
            $results += [PSCustomObject]@{
                status = 'planned'; file = $item.Name; source = $item.SourceRoot; destination = $item.Target
                reason = $null
            }
        } else {
            $results += [PSCustomObject]@{
                status = 'skipped'; file = $item.Name; source = $item.SourceRoot; destination = $item.Target
                reason = 'The move was not approved.'
            }
        }
    } catch {
        $failed++
        $results += [PSCustomObject]@{
            status = 'failed'; file = $item.Name; source = $item.SourceRoot; destination = $item.Target
            reason = $_.Exception.Message
        }
        Write-Output "FAILED [$($item.Kind)] $($item.Name): $($_.Exception.Message)"
    }
}
$unmatched = 0
foreach ($sourceRoot in $sourceRoots) { $unmatched += @(Get-ChildItem -LiteralPath $sourceRoot -File).Count }
Write-Output ''; Write-Output 'SUMMARY'; Write-Output "Planned:              $planned"; Write-Output "Moved:                $moved"
foreach ($key in $counts.Keys) { Write-Output "  $key`:              $($counts[$key])" }
Write-Output "Skipped collisions:   $collisions"; Write-Output "Failed:               $failed"; Write-Output "Unmatched left alone: $unmatched"

if ($ResultPath) {
    $resultPayload = [PSCustomObject]@{
        version = 1
        action = if ($WhatIfPreference) { 'preview' } else { 'apply' }
        results = @($results)
        summary = [ordered]@{
            planned = @($results).Count
            moved = @($results | Where-Object { $_.status -eq 'moved' }).Count
            skipped_collisions = @($results | Where-Object { $_.status -eq 'duplicate' }).Count
            failed = @($results | Where-Object { $_.status -eq 'failed' }).Count
            unmatched_left_alone = $unmatched
        }
    }
    $resultJson = $resultPayload | ConvertTo-Json -Depth 8
    $utf8 = New-Object System.Text.UTF8Encoding -ArgumentList $false
    [System.IO.File]::WriteAllText($ResultPath, $resultJson, $utf8)
    # Keep a tagged stdout fallback for hosts where the temporary result file
    # cannot be read after the child process exits. The host only parses this
    # explicit machine-readable marker, never the human WhatIf text.
    $resultMarker = $resultPayload | ConvertTo-Json -Depth 8 -Compress
    Write-Output "ARIADNE_RESULT_JSON:$resultMarker"
}
