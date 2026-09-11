[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("up", "down", "status", "config", "hera-preflight", "hera-cutover", "hera-rollback")]
    [string]$Action = "status",
    [string]$BuildSha,
    [switch]$ConfirmCutover
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot ".."))
$BaseCompose = Join-Path $Root "compose.yaml"
$LocalCompose = Join-Path $Root "compose.local.yaml"
$HeraCompose = Join-Path $Root "compose.hera.yaml"
$DevProject = "ariadne-discovery-signal-dev"
$HeraProject = "ariadne-discovery-signal-prod"

function Invoke-Compose {
    param(
        [string]$Project,
        [string[]]$Files,
        [string[]]$Arguments
    )
    $composeArgs = @("-p", $Project)
    foreach ($file in $Files) {
        $composeArgs += @("-f", $file)
    }
    $composeArgs += $Arguments
    & docker compose @composeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed with exit code $LASTEXITCODE."
    }
}

function Invoke-Docker {
    param([string[]]$Arguments)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker failed with exit code $LASTEXITCODE."
    }
}

function Get-Health {
    param([string]$Label, [string]$Url, [string]$ExpectedEnvironment, [string]$ExpectedSha)
    try {
        $health = Invoke-RestMethod -Uri $Url -TimeoutSec 8
        $summary = "{0}: state={1}; environment={2}; instance={3}; build_sha={4}" -f $Label, $health.state, $health.environment, $health.instance, $health.build_sha
        Write-Host $summary
        if ($ExpectedEnvironment -and $health.environment -ne $ExpectedEnvironment) {
            throw "$Label reported environment '$($health.environment)', expected '$ExpectedEnvironment'."
        }
        if ($ExpectedSha -and $health.build_sha -ne $ExpectedSha) {
            throw "$Label reported build '$($health.build_sha)', expected '$ExpectedSha'."
        }
        return $health
    } catch {
        Write-Warning "$Label health unavailable: $($_.Exception.Message)"
        return $null
    }
}

function Ensure-DevDataDirectories {
    New-Item -ItemType Directory -Force (Join-Path $Root "runtime\discovery-signal-dev\signal") | Out-Null
    New-Item -ItemType Directory -Force (Join-Path $Root "runtime\discovery-signal-dev\discovery") | Out-Null
}

function Set-LocalBuildSha {
    $head = (& git -C $Root rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($head)) {
        throw "Unable to resolve the local git HEAD for health/build metadata."
    }
    $env:ARIADNE_BUILD_SHA = $head
}

function Set-HeraBuildSha {
    if ([string]::IsNullOrWhiteSpace($BuildSha)) {
        throw "-BuildSha is required for Hera operations. Use the full reviewed git SHA."
    }
    $head = (& git -C $Root rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0 -or $head -cne $BuildSha) {
        throw "Current checkout HEAD '$head' does not exactly match requested reviewed SHA '$BuildSha'."
    }
    $dirty = @(& git -C $Root status --porcelain)
    if ($dirty.Count -gt 0) {
        throw "Hera operations require a clean checkout at the reviewed SHA."
    }
    $env:ARIADNE_BUILD_SHA = $BuildSha
}

function Get-HeraDataPath {
    param([string]$Name, [string]$Default)
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
    return $value
}

function Assert-HeraProductionBoundary {
    $signalData = Get-HeraDataPath "HERA_SIGNAL_DATA_PATH" "/volume1/docker/ariadne-signal-service/data"
    $discoveryData = Get-HeraDataPath "HERA_DISCOVERY_DATA_PATH" "/volume1/docker/ariadne-discovery-service/data"
    foreach ($path in @($signalData, $discoveryData)) {
        if (-not (Test-Path -LiteralPath $path -PathType Container)) {
            throw "Required existing Hera data directory is missing: $path. Set the correct HERA_*_DATA_PATH; the cutover will not initialize a new path."
        }
    }
    foreach ($path in @((Join-Path $signalData "signals.sqlite3"), (Join-Path $discoveryData "discovery.sqlite3"))) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
            throw "Expected existing production database is missing: $path. The cutover will not replace it."
        }
    }
    foreach ($container in @("ariadne-signal-service", "ariadne-discovery-service")) {
        $exists = (& docker ps -a --filter "name=^/$container$" --format "{{.Names}}")
        if ($LASTEXITCODE -ne 0 -or $exists -notcontains $container) {
            throw "Expected existing Hera container '$container' was not found. Stop and inspect before cutting over."
        }
    }
}

function Show-HeraPreflight {
    Set-HeraBuildSha
    Assert-HeraProductionBoundary
    Invoke-Compose $HeraProject @($BaseCompose, $HeraCompose) @("config", "--quiet")
    Write-Host "Existing Hera services before cutover:"
    Get-Health "Hera Signal (current)" "http://localhost:8788/v1/health" "" "" | Out-Null
    Get-Health "Hera Discovery (current)" "http://localhost:8789/v1/health" "" "" | Out-Null
    Write-Host "Preflight passed. No production state was changed."
}

switch ($Action) {
    "up" {
        Ensure-DevDataDirectories
        Set-LocalBuildSha
        Invoke-Compose $DevProject @($BaseCompose, $LocalCompose) @("up", "-d", "--build")
        & $PSCommandPath status
    }
    "down" {
        # Deliberately omit -v: local data is retained for repeatable testing.
        Invoke-Compose $DevProject @($BaseCompose, $LocalCompose) @("down")
    }
    "status" {
        Invoke-Compose $DevProject @($BaseCompose, $LocalCompose) @("ps")
        Get-Health "DEV Signal" "http://localhost:18788/v1/health" "dev" "" | Out-Null
        Get-Health "DEV Discovery" "http://localhost:18789/v1/health" "dev" "" | Out-Null
    }
    "config" {
        Invoke-Compose $DevProject @($BaseCompose, $LocalCompose) @("config")
    }
    "hera-preflight" {
        Show-HeraPreflight
    }
    "hera-cutover" {
        if (-not $ConfirmCutover) {
            throw "Refusing Hera cutover without -ConfirmCutover. Run hera-preflight first."
        }
        Show-HeraPreflight
        Assert-HeraProductionBoundary
        Write-Host "Stopping only the two existing production containers; bind-mounted data is retained."
        Invoke-Docker @("stop", "--time", "30", "ariadne-signal-service", "ariadne-discovery-service")
        try {
            Invoke-Compose $HeraProject @($BaseCompose, $HeraCompose) @("up", "-d", "--build")
            $signal = Get-Health "Hera Signal (new)" "http://localhost:8788/v1/health" "prod" $BuildSha
            $discovery = Get-Health "Hera Discovery (new)" "http://localhost:8789/v1/health" "prod" $BuildSha
            if (-not $signal -or -not $discovery) { throw "New Hera health verification failed." }
            Write-Host "Hera cutover verified at build $BuildSha."
        } catch {
            Write-Warning "Cutover verification failed. Run: .\scripts\discovery-signal.ps1 hera-rollback -BuildSha $BuildSha"
            throw
        }
    }
    "hera-rollback" {
        if (-not $ConfirmCutover) {
            throw "Refusing Hera rollback without -ConfirmCutover."
        }
        Set-HeraBuildSha
        Invoke-Compose $HeraProject @($BaseCompose, $HeraCompose) @("down")
        Invoke-Docker @("start", "ariadne-signal-service", "ariadne-discovery-service")
        Get-Health "Hera Signal (rolled back)" "http://localhost:8788/v1/health" "" "" | Out-Null
        Get-Health "Hera Discovery (rolled back)" "http://localhost:8789/v1/health" "" "" | Out-Null
    }
}
