# Commit.ps1
#
# One-click Ariadne publish/synchronization.
# Codex creates local commits. This script never stages, commits, resets, merges, or rebases.
# It only publishes existing commits, verifies the remote result, and aligns upstream tracking.

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$Repositories = @(
    [pscustomobject]@{
        Name         = 'Ariadne'
        Path         = 'D:\Downloads\Ariadne'
        Remote       = 'origin'
        RemoteBranch = 'main'
        ExpectedUrl  = 'github\.com[:/]PopeKael/Ariadne(?:\.git)?$'
    },
    [pscustomobject]@{
        Name         = 'KnowledgeVault'
        Path         = 'D:\Downloads\KnowledgeVault'
        Remote       = 'origin'
        RemoteBranch = 'knowledge-vault'
        ExpectedUrl  = 'github\.com[:/]PopeKael/Ariadne(?:\.git)?$'
    }
)

function Invoke-GitChecked {
    param(
        [Parameter(Mandatory = $true)][string]$RepoPath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList
    )

    $output = & git -c core.pager=cat -C $RepoPath @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "git -C `"$RepoPath`" $($ArgumentList -join ' ') failed with exit code $LASTEXITCODE."
    }
    return @($output)
}

function Get-GitText {
    param(
        [Parameter(Mandatory = $true)][string]$RepoPath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList
    )
    return ([string](Invoke-GitChecked -RepoPath $RepoPath -ArgumentList $ArgumentList)).Trim()
}

function Test-OutgoingPrivacy {
    param(
        [Parameter(Mandatory = $true)][string]$RepoPath,
        [Parameter(Mandatory = $true)][string]$RemoteRef
    )

    $paths = @()
    $remoteExists = $false

    & git -c core.pager=cat -C $RepoPath rev-parse --verify --quiet $RemoteRef *> $null
    if ($LASTEXITCODE -eq 0) {
        $remoteExists = $true
    }

    if ($remoteExists) {
        $paths = @(Invoke-GitChecked -RepoPath $RepoPath -ArgumentList @('diff', '--name-only', "$RemoteRef..HEAD"))
    }
    else {
        $paths = @(Invoke-GitChecked -RepoPath $RepoPath -ArgumentList @('ls-tree', '-r', '--name-only', 'HEAD'))
    }

    $paths = @($paths | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Sort-Object -Unique)
    if ($paths.Count -eq 0) {
        return 'not required: no outgoing paths'
    }

    $blocked = New-Object System.Collections.Generic.List[string]

    foreach ($path in $paths) {
        $ignoredOutput = & git -c core.pager=cat -C $RepoPath check-ignore --no-index -- $path 2>$null
        $ignoredText = if ($null -eq $ignoredOutput) { '' } else { ([string]$ignoredOutput).Trim() }

        if (-not [string]::IsNullOrWhiteSpace($ignoredText)) {
            $blocked.Add("$path (ignored/private/generated)")
            continue
        }

        if ($path -match '(?i)(^|/)(\.env($|\.)|secrets?($|/)|credentials?($|/)|HomeSessions($|/)|WorldState($|/)|\.host-build-msvc($|/)|\.tmp-ui-[^/]*($|/)|node_modules($|/)|__pycache__($|/)|\.venv($|/))') {
            $blocked.Add("$path (sensitive/generated pattern)")
        }
    }

    if ($blocked.Count -gt 0) {
        $sample = ($blocked | Select-Object -First 20) -join "`n  - "
        throw "Privacy/preflight blocked outgoing content:`n  - $sample"
    }

    return "PASS: inspected $($paths.Count) outgoing path(s)"
}

function Sync-Repository {
    param(
        [Parameter(Mandatory = $true)]$Repo
    )

    if (-not (Test-Path -LiteralPath $Repo.Path -PathType Container)) {
        throw "$($Repo.Name) repository was not found: $($Repo.Path)"
    }

    if (-not (Test-Path -LiteralPath (Join-Path $Repo.Path '.git'))) {
        throw "$($Repo.Path) is not a Git repository."
    }

    $remoteUrl = Get-GitText -RepoPath $Repo.Path -ArgumentList @('remote', 'get-url', $Repo.Remote)
    if ($remoteUrl -notmatch $Repo.ExpectedUrl) {
        throw "$($Repo.Name) remote '$($Repo.Remote)' is unexpected: $remoteUrl"
    }

    $localBranch = Get-GitText -RepoPath $Repo.Path -ArgumentList @('branch', '--show-current')
    if ([string]::IsNullOrWhiteSpace($localBranch)) {
        throw "$($Repo.Name) is in detached-HEAD state."
    }

    if ($localBranch -ne 'main') {
        throw "$($Repo.Name) is on '$localBranch'. Expected local branch 'main'. Nothing was pushed."
    }

    $dirty = @(Invoke-GitChecked -RepoPath $Repo.Path -ArgumentList @('status', '--porcelain=v1', '--untracked-files=all'))
    if (@($dirty | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }).Count -gt 0) {
        Write-Warning "$($Repo.Name) working tree is dirty. Continuing; only existing commits will be pushed. Uncommitted files will not be staged."
    }

    Write-Host ''
    Write-Host "=== $($Repo.Name) ==="
    Write-Host "Local:      $($Repo.Path)"
    Write-Host "Remote:     $remoteUrl"
    Write-Host "Publish to: $($Repo.Remote)/$($Repo.RemoteBranch)"

    Invoke-GitChecked -RepoPath $Repo.Path -ArgumentList @('fetch', '--prune', $Repo.Remote) | Out-Null

    $remoteRef = "refs/remotes/$($Repo.Remote)/$($Repo.RemoteBranch)"
    $remoteExists = $false
    & git -c core.pager=cat -C $Repo.Path rev-parse --verify --quiet $remoteRef *> $null
    if ($LASTEXITCODE -eq 0) {
        $remoteExists = $true
    }

    if ($remoteExists) {
        $ahead = [int](Get-GitText -RepoPath $Repo.Path -ArgumentList @('rev-list', '--count', "$remoteRef..HEAD"))
        $behind = [int](Get-GitText -RepoPath $Repo.Path -ArgumentList @('rev-list', '--count', "HEAD..$remoteRef"))

        if ($ahead -eq 0 -and $behind -eq 0) {
            Invoke-GitChecked -RepoPath $Repo.Path -ArgumentList @(
                'branch', '--set-upstream-to', "$($Repo.Remote)/$($Repo.RemoteBranch)", $localBranch
            ) | Out-Null

            $head = Get-GitText -RepoPath $Repo.Path -ArgumentList @('rev-parse', 'HEAD')
            Write-Host "privacy/preflight: not required: no outgoing commits"
            Write-Host "result: synchronized at $head"
            return [pscustomobject]@{ Name=$Repo.Name; Result='Already synchronized'; Sha=$head }
        }

        if ($ahead -gt 0 -and $behind -gt 0) {
            throw "$($Repo.Name) local main and $($Repo.Remote)/$($Repo.RemoteBranch) have diverged ($ahead ahead, $behind behind). Nothing was pushed."
        }

        if ($behind -gt 0) {
            throw "$($Repo.Name) local main is behind $($Repo.Remote)/$($Repo.RemoteBranch) by $behind commit(s). Nothing was pushed."
        }

        $privacy = Test-OutgoingPrivacy -RepoPath $Repo.Path -RemoteRef $remoteRef
        Write-Host "privacy/preflight: $privacy"

        Invoke-GitChecked -RepoPath $Repo.Path -ArgumentList @(
            'push', $Repo.Remote, "HEAD:refs/heads/$($Repo.RemoteBranch)"
        ) | Out-Null
    }
    else {
        $privacy = Test-OutgoingPrivacy -RepoPath $Repo.Path -RemoteRef $remoteRef
        Write-Host "privacy/preflight: $privacy"

        Invoke-GitChecked -RepoPath $Repo.Path -ArgumentList @(
            'push', $Repo.Remote, "HEAD:refs/heads/$($Repo.RemoteBranch)"
        ) | Out-Null
    }

    # Refresh the remote-tracking ref and make Codex/Git compare against the correct destination.
    Invoke-GitChecked -RepoPath $Repo.Path -ArgumentList @('fetch', $Repo.Remote, $Repo.RemoteBranch) | Out-Null
    Invoke-GitChecked -RepoPath $Repo.Path -ArgumentList @(
        'branch', '--set-upstream-to', "$($Repo.Remote)/$($Repo.RemoteBranch)", $localBranch
    ) | Out-Null

    $localHead = Get-GitText -RepoPath $Repo.Path -ArgumentList @('rev-parse', 'HEAD')
    $remoteHead = Get-GitText -RepoPath $Repo.Path -ArgumentList @('rev-parse', $remoteRef)

    if ($localHead -ne $remoteHead) {
        throw "$($Repo.Name) post-push verification FAILED. Local=$localHead Remote=$remoteHead"
    }

    Write-Host "result: SYNCHRONIZED"
    Write-Host "verified: local HEAD == $($Repo.Remote)/$($Repo.RemoteBranch) == $localHead"

    return [pscustomobject]@{
        Name   = $Repo.Name
        Result = 'SYNCHRONIZED'
        Sha    = $localHead
    }
}

$results = New-Object System.Collections.Generic.List[object]
$failures = New-Object System.Collections.Generic.List[string]

foreach ($repo in $Repositories) {
    try {
        $results.Add((Sync-Repository -Repo $repo))
    }
    catch {
        $failures.Add("$($repo.Name): $($_.Exception.Message)")
        Write-Host ''
        Write-Host "ERROR [$($repo.Name)]: $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host ''
Write-Host '=== Synchronization summary ==='
foreach ($result in $results) {
    Write-Host "$($result.Name): $($result.Result) [$($result.Sha)]"
}

if ($failures.Count -gt 0) {
    Write-Host ''
    Write-Host 'FAILED:' -ForegroundColor Red
    foreach ($failure in $failures) {
        Write-Host " - $failure" -ForegroundColor Red
    }
    exit 1
}

Write-Host ''
Write-Host 'ALL REPOSITORIES SYNCHRONIZED AND VERIFIED.'
exit 0
