# Commit.ps1
#
# Desktop publish button for the selected Ariadne repository.
# Codex creates local commits; this script only publishes existing commits.

[CmdletBinding()]
param(
    [string]$RepoPath = 'D:\Downloads\Ariadne',
    [string]$Remote = 'origin'
)

$ErrorActionPreference = 'Stop'

function Invoke-GitChecked {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList
    )

    # Force a non-interactive pager for every Git invocation in this script.
    $output = & git -c core.pager=cat @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "git $($ArgumentList -join ' ') failed with exit code $LASTEXITCODE."
    }

    return $output
}

if (-not (Test-Path -LiteralPath $RepoPath -PathType Container)) {
    throw "Ariadne repository was not found: $RepoPath"
}

$gitDirectory = Join-Path $RepoPath '.git'
if (-not (Test-Path -LiteralPath $gitDirectory)) {
    throw "The selected path is not a Git repository: $RepoPath"
}

Push-Location -LiteralPath $RepoPath
try {
    $remoteUrl = ([string](Invoke-GitChecked -ArgumentList @('remote', 'get-url', $Remote))).Trim()
    if ([string]::IsNullOrWhiteSpace($remoteUrl)) {
        throw "Git remote '$Remote' is not configured in $RepoPath"
    }

    if ($remoteUrl -notmatch 'github\.com[:/]PopeKael/Ariadne(?:\.git)?$') {
        throw "Remote '$Remote' is not the expected Ariadne GitHub repository: $remoteUrl"
    }

    $branch = ([string](Invoke-GitChecked -ArgumentList @('branch', '--show-current'))).Trim()
    if ([string]::IsNullOrWhiteSpace($branch)) {
        throw 'The repository is in detached-HEAD state. Check out the intended branch before using the publish button.'
    }

    Write-Host "Repository: $RepoPath"
    Write-Host "Remote:     $remoteUrl"
    Write-Host "Branch:     $branch"
    Write-Host ''

    # Refresh remote-tracking refs before comparing local and remote history.
    Invoke-GitChecked -ArgumentList @('fetch', '--prune', $Remote)

    $upstreamOutput = & git -c core.pager=cat rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>$null
    $upstreamExitCode = $LASTEXITCODE
    $upstream = ([string]$upstreamOutput).Trim()
    $targetUpstream = "$Remote/$branch"
    $hasTargetUpstream = $upstreamExitCode -eq 0 -and $upstream -eq $targetUpstream

    $remoteRef = "refs/remotes/$Remote/$branch"
    $remoteCommit = ([string](& git -c core.pager=cat rev-parse --verify --quiet $remoteRef 2>$null)).Trim()
    $remoteRefExitCode = $LASTEXITCODE
    $hasRemoteBranch = $remoteRefExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($remoteCommit)

    if (-not $hasRemoteBranch) {
        $head = ([string](& git -c core.pager=cat rev-parse --verify --quiet HEAD 2>$null)).Trim()
        $headExitCode = $LASTEXITCODE
        if ($headExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($head)) {
            throw "Branch '$branch' has no local commit to push."
        }

        Invoke-GitChecked -ArgumentList @('push', '--set-upstream', $Remote, $branch)
        Write-Host "Published existing commits to $targetUpstream."
        return
    }

    $aheadCount = [int](([string](Invoke-GitChecked -ArgumentList @('rev-list', '--count', "$targetUpstream..HEAD"))).Trim())
    $behindCount = [int](([string](Invoke-GitChecked -ArgumentList @('rev-list', '--count', "HEAD..$targetUpstream"))).Trim())

    if ($aheadCount -eq 0 -and $behindCount -eq 0) {
        Write-Host 'Nothing to push.'
        return
    }

    if ($aheadCount -gt 0 -and $behindCount -gt 0) {
        throw "Local branch '$branch' and '$targetUpstream' have diverged ($aheadCount ahead, $behindCount behind). Nothing was pushed."
    }

    if ($behindCount -gt 0) {
        throw "Local branch '$branch' is behind '$targetUpstream' by $behindCount commit(s). Nothing was pushed."
    }

    if ($hasTargetUpstream) {
        Invoke-GitChecked -ArgumentList @('push', $Remote, $branch)
    }
    else {
        Invoke-GitChecked -ArgumentList @('push', '--set-upstream', $Remote, $branch)
    }

    Write-Host "Published $aheadCount existing commit(s) to $targetUpstream."
}
finally {
    Pop-Location
}
