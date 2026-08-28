# Commit.ps1
#
# Desktop publish button for both local Ariadne repositories.
# Codex creates local commits; this script only publishes existing commits.

[CmdletBinding()]
param(
    [string]$RepoPath = 'D:\Downloads\Ariadne',
    [string]$Remote = 'origin',
    [string]$KnowledgeVaultRemote = 'origin',
    [switch]$DryRun
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

function Invoke-GitOptional {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList
    )

    $output = & git -c core.pager=cat @ArgumentList 2>$null
    $exitCode = $LASTEXITCODE
    $outputText = if ($null -eq $output) { '' } else { ([string]$output).Trim() }
    return [pscustomobject]@{
        Output   = $outputText
        ExitCode = $exitCode
    }
}

function Get-OutgoingPaths {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RemoteRef,
        [Parameter(Mandatory = $true)]
        [bool]$HasRemoteBranch
    )

    if ($HasRemoteBranch) {
        $commits = @(Invoke-GitChecked -ArgumentList @('rev-list', '--reverse', "$RemoteRef..HEAD"))
    }
    else {
        $commits = @(Invoke-GitChecked -ArgumentList @('rev-list', '--reverse', 'HEAD'))
    }

    $paths = foreach ($commit in $commits) {
        @(Invoke-GitChecked -ArgumentList @('diff-tree', '--no-commit-id', '--name-only', '--root', '-r', '-m', $commit, '--'))
    }

    return @($paths | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Sort-Object -Unique)
}

function Get-PrivacyFinding {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    # --no-index makes Git apply .gitignore, .git/info/exclude, and configured
    # global excludes even when the outgoing path is already tracked.
    $ignoreResult = Invoke-GitOptional -ArgumentList @('check-ignore', '--no-index', '-v', '--', $Path)
    if ($ignoreResult.ExitCode -gt 1) {
        throw "Unable to check privacy exclusions for '$Path'."
    }
    if ($ignoreResult.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($ignoreResult.Output)) {
        return [pscustomobject]@{
            Path   = $Path
            Reason = "Matched Git privacy/exclusion rule: $($ignoreResult.Output)"
        }
    }

    $normalized = $Path.Replace('\', '/')
    if ($normalized -match '(^|/)\.host-build-msvc(?:/|$)') {
        return [pscustomobject]@{
            Path   = $Path
            Reason = 'Known generated MSVC host-build output.'
        }
    }
    if ($normalized -match '(^|/)\.tmp-ui-[^/]*(?:/|$)') {
        return [pscustomobject]@{
            Path   = $Path
            Reason = 'Known generated UI temporary/runtime output.'
        }
    }
    if ($normalized -match '(^|/)target(?:-msvc)?(?:/|$)') {
        return [pscustomobject]@{
            Path   = $Path
            Reason = 'Known generated Rust target output.'
        }
    }

    return
}

function Invoke-RepositorySync {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$RemoteName
    )

    $result = [ordered]@{
        Name         = $Name
        Path         = $Path
        Branch       = '(unavailable)'
        Remote       = $RemoteName
        RemoteUrl    = '(unavailable)'
        Upstream     = '(none)'
        WouldPush    = $false
        Privacy      = 'not run'
        Outcome      = 'not processed'
    }

    try {
        if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
            throw "Repository directory was not found: $Path"
        }

        $gitDirectory = Join-Path $Path '.git'
        if (-not (Test-Path -LiteralPath $gitDirectory)) {
            throw "The selected path is not a Git repository: $Path"
        }

        Push-Location -LiteralPath $Path
        try {
            # Verify this repository's configured remote before fetching or
            # inspecting any remote-dependent state.
            $remoteUrl = ([string](Invoke-GitChecked -ArgumentList @('remote', 'get-url', $RemoteName))).Trim()
            if ([string]::IsNullOrWhiteSpace($remoteUrl)) {
                throw "Git remote '$RemoteName' is not configured in $Path"
            }
            $result.RemoteUrl = $remoteUrl

            $branch = ([string](Invoke-GitChecked -ArgumentList @('branch', '--show-current'))).Trim()
            if ([string]::IsNullOrWhiteSpace($branch)) {
                throw 'The repository is in detached-HEAD state.'
            }
            $result.Branch = $branch

            $targetUpstream = "$RemoteName/$branch"

            # Refresh remote-tracking refs before deciding whether anything is pushable.
            $null = Invoke-GitChecked -ArgumentList @('fetch', '--prune', $RemoteName)

            $upstreamInfo = Invoke-GitOptional -ArgumentList @('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{upstream}')
            if ($upstreamInfo.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($upstreamInfo.Output)) {
                $result.Upstream = $upstreamInfo.Output
                if ($upstreamInfo.Output -ne $targetUpstream) {
                    throw "Configured upstream '$($upstreamInfo.Output)' does not match '$targetUpstream'."
                }
            }

            $remoteRef = "refs/remotes/$RemoteName/$branch"
            $remoteCommitInfo = Invoke-GitOptional -ArgumentList @('rev-parse', '--verify', '--quiet', $remoteRef)
            $hasRemoteBranch = $remoteCommitInfo.ExitCode -eq 0 -and -not [string]::IsNullOrWhiteSpace($remoteCommitInfo.Output)

            if (-not $hasRemoteBranch) {
                $headInfo = Invoke-GitOptional -ArgumentList @('rev-parse', '--verify', '--quiet', 'HEAD')
                if ($headInfo.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($headInfo.Output)) {
                    throw "Branch '$branch' has no local commit to push."
                }

                $outgoingPaths = @(Get-OutgoingPaths -RemoteRef $targetUpstream -HasRemoteBranch $false)
                $findings = @(foreach ($outgoingPath in $outgoingPaths) {
                    Get-PrivacyFinding -Path $outgoingPath
                })
                if ($findings.Count -gt 0) {
                    $result.Privacy = "REFUSED: $($findings.Count) offending path(s)"
                    $result.Outcome = 'Push refused by privacy preflight.'
                    foreach ($finding in $findings) {
                        Write-Host "  Offending path: $($finding.Path) [$($finding.Reason)]"
                    }
                }
                elseif ($DryRun) {
                    $result.WouldPush = $true
                    $result.Privacy = "PASS: inspected $($outgoingPaths.Count) outgoing path(s)"
                    $result.Outcome = "Would push with --set-upstream to $targetUpstream."
                }
                else {
                    $null = Invoke-GitChecked -ArgumentList @('push', '--set-upstream', $RemoteName, $branch)
                    $result.WouldPush = $true
                    $result.Privacy = "PASS: inspected $($outgoingPaths.Count) outgoing path(s)"
                    $result.Outcome = "Pushed with --set-upstream to $targetUpstream."
                }
            }
            else {
                $aheadCount = [int](([string](Invoke-GitChecked -ArgumentList @('rev-list', '--count', "$targetUpstream..HEAD"))).Trim())
                $behindCount = [int](([string](Invoke-GitChecked -ArgumentList @('rev-list', '--count', "HEAD..$targetUpstream"))).Trim())

                if ($aheadCount -eq 0 -and $behindCount -eq 0) {
                    $result.Privacy = 'not required: no outgoing commits'
                    $result.Outcome = 'Nothing to push.'
                }
                elseif ($aheadCount -gt 0 -and $behindCount -gt 0) {
                    $result.Privacy = 'not run: no push attempted'
                    $result.Outcome = "STOP: branches diverged ($aheadCount ahead, $behindCount behind)."
                }
                elseif ($behindCount -gt 0) {
                    $result.Privacy = 'not run: no push attempted'
                    $result.Outcome = "STOP: local branch is behind by $behindCount commit(s)."
                }
                else {
                    $outgoingPaths = @(Get-OutgoingPaths -RemoteRef $targetUpstream -HasRemoteBranch $true)
                    $findings = @(foreach ($outgoingPath in $outgoingPaths) {
                        Get-PrivacyFinding -Path $outgoingPath
                    })
                    if ($findings.Count -gt 0) {
                        $result.Privacy = "REFUSED: $($findings.Count) offending path(s)"
                        $result.Outcome = 'Push refused by privacy preflight.'
                        foreach ($finding in $findings) {
                            Write-Host "  Offending path: $($finding.Path) [$($finding.Reason)]"
                        }
                    }
                    elseif ($DryRun) {
                        $result.WouldPush = $true
                        $result.Privacy = "PASS: inspected $($outgoingPaths.Count) outgoing path(s)"
                        if ($result.Upstream -eq $targetUpstream) {
                            $result.Outcome = "Would push $aheadCount existing commit(s) to $targetUpstream."
                        }
                        else {
                            $result.Outcome = "Would push $aheadCount existing commit(s) with --set-upstream to $targetUpstream."
                        }
                    }
                    else {
                        if ($result.Upstream -eq $targetUpstream) {
                            $null = Invoke-GitChecked -ArgumentList @('push', $RemoteName, $branch)
                            $result.Outcome = "Pushed $aheadCount existing commit(s) to $targetUpstream."
                        }
                        else {
                            $null = Invoke-GitChecked -ArgumentList @('push', '--set-upstream', $RemoteName, $branch)
                            $result.Outcome = "Pushed $aheadCount existing commit(s) with --set-upstream to $targetUpstream."
                        }
                        $result.WouldPush = $true
                        $result.Privacy = "PASS: inspected $($outgoingPaths.Count) outgoing path(s)"
                    }
                }
            }
        }
        finally {
            Pop-Location
        }
    }
    catch {
        $result.WouldPush = $false
        if ($result.Privacy -eq 'not run') {
            $result.Privacy = 'ERROR: preflight incomplete'
        }
        $result.Outcome = "STOP: $($_.Exception.Message)"
    }

    Write-Host "$Name repository -> branch: $($result.Branch); remote: $($result.Remote) ($($result.RemoteUrl)); would-push: $($result.WouldPush)"
    Write-Host "  privacy/preflight: $($result.Privacy)"
    Write-Host "  result: $($result.Outcome)"
    Write-Host ''

    return [pscustomobject]$result
}

$repositorySpecs = @(
    [pscustomobject]@{
        Name   = 'Ariadne'
        Path   = $RepoPath
        Remote = $Remote
    }
    [pscustomobject]@{
        Name   = 'KnowledgeVault'
        Path   = 'D:\Downloads\KnowledgeVault'
        Remote = $KnowledgeVaultRemote
    }
)

$results = foreach ($repository in $repositorySpecs) {
    Invoke-RepositorySync -Name $repository.Name -Path $repository.Path -RemoteName $repository.Remote
}

Write-Host 'Synchronization summary:'
foreach ($result in $results) {
    Write-Host "$($result.Name) repository -> branch: $($result.Branch); remote: $($result.Remote) ($($result.RemoteUrl)); would-push: $($result.WouldPush)"
    Write-Host "  privacy/preflight: $($result.Privacy)"
    Write-Host "  result: $($result.Outcome)"
}
