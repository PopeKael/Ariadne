# Ariadne v0.7 Knowledge Compiler
# Report-only graph analysis. It never changes Wiki pages, metadata, or sources.

[CmdletBinding()]
param(
    [string]$Vault = "D:\Downloads\KnowledgeVault",
    [string]$OutputDirectory,
    [ValidateSet('Report', 'Proposal')]
    [string]$Mode = 'Report'
)

$Wiki = Join-Path $Vault "Wiki"
$LibraryPath = Join-Path $Vault "00_System\library.json"
if ($Mode -eq 'Report' -and [string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $Vault "Reports\Ariadne"
}

function Get-CanonicalKey {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return "" }
    return (($Value.ToLowerInvariant() -replace '[^\p{L}\p{N}]+', ' ').Trim())
}

function Get-WikiLinks {
    param([string]$Content)
    return @([regex]::Matches($Content, '\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]') |
        ForEach-Object { $_.Groups[1].Value.Trim() } |
        # Workflow artefacts are valid vault links, but not wiki graph edges.
        Where-Object { $_ -and $_ -notmatch '^(Processed|Review|Inbox|Failed)/' })
}

function New-ExistingLinkProposal {
    param(
        [pscustomobject]$Page,
        [string]$TargetPath
    )

    $TargetTitle = [System.IO.Path]::GetFileNameWithoutExtension($TargetPath)
    $Link = "[[${TargetTitle}]]"
    if ($Page.Content -match [regex]::Escape($Link)) { return $null }

    # Related concepts are maintained as page-level links, rather than attached to
    # one ingest block, so the relationship remains valid as sources evolve.
    $Before = $Page.Content.TrimEnd()
    $After = "$Before`n`n## Related Concepts`n`n- $Link"
    $AddedLines = @('## Related Concepts', '', "- $Link")

    return [pscustomobject]@{
        page = $Page.RelativePath
        target = $TargetPath
        link = $Link
        before = $Before
        after = $After
        added_lines = $AddedLines
        diff = @(
            "--- a/Wiki/$($Page.RelativePath)",
            "+++ b/Wiki/$($Page.RelativePath)",
            "@@ -$((($Before -split "`r?`n").Count + 1)),0 +$((($Before -split "`r?`n").Count + 1)),3 @@",
            '+',
            '+## Related Concepts',
            '+',
            "+- $Link"
        ) -join "`n"
    }
}

if (!(Test-Path -LiteralPath $Wiki)) { throw "Wiki directory not found: $Wiki" }
if ($Mode -eq 'Report') {
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
}

$PageFiles = @(Get-ChildItem -LiteralPath $Wiki -Recurse -File -Filter *.md |
    Where-Object { $_.Name -ne "README.md" })
$Pages = @{}
$KeyToPage = @{}
foreach ($File in $PageFiles) {
    $RelativePath = $File.FullName.Substring($Wiki.Length).TrimStart('\', '/') -replace '\\', '/'
    $Title = $File.BaseName
    $Content = Get-Content -LiteralPath $File.FullName -Raw
    $Heading = ([regex]::Match($Content, '(?m)^#\s+(.+?)\s*$')).Groups[1].Value.Trim()
    $Page = [pscustomobject]@{
        Title = $Title; RelativePath = $RelativePath; Heading = $Heading
        Content = $Content; Links = @(Get-WikiLinks $Content)
        SourceCount = ([regex]::Matches($Content, '(?m)^- Source:')).Count
        SizeBytes = $File.Length
    }
    $Pages[$RelativePath] = $Page
    $KeyToPage[(Get-CanonicalKey $Title)] = $RelativePath
    $KeyToPage[(Get-CanonicalKey ($RelativePath -replace '\.md$', ''))] = $RelativePath
}

$Edges = [System.Collections.Generic.List[object]]::new()
$Unresolved = [System.Collections.Generic.List[object]]::new()
foreach ($Page in $Pages.Values) {
    foreach ($Target in $Page.Links) {
        $TargetKey = Get-CanonicalKey ($Target -replace '^Wiki/', '')
        if ($KeyToPage.ContainsKey($TargetKey)) {
            $Edges.Add([pscustomobject]@{ from = $Page.RelativePath; to = $KeyToPage[$TargetKey] })
        } else {
            $Unresolved.Add([pscustomobject]@{ page = $Page.RelativePath; target = $Target })
        }
    }
}

$Inbound = @{}; $Outbound = @{}
foreach ($Path in $Pages.Keys) { $Inbound[$Path] = 0; $Outbound[$Path] = 0 }
foreach ($Edge in $Edges) { $Outbound[$Edge.from]++; $Inbound[$Edge.to]++ }
$Orphans = @($Pages.Keys | Where-Object { $Inbound[$_] -eq 0 -and $Outbound[$_] -eq 0 } | Sort-Object)
$Sparse = @($Pages.Values | Where-Object { $_.SourceCount -eq 0 -or $_.Content.Trim().Length -lt 300 } |
    ForEach-Object { [pscustomobject]@{ page = $_.RelativePath; sources = $_.SourceCount; characters = $_.Content.Trim().Length } } |
    Sort-Object characters, page)
$Naming = @($Pages.Values | Where-Object {
    $_.Title -ne $_.Title.Trim() -or ($_.Heading -and $_.Heading -ne $_.Title)
} | ForEach-Object { [pscustomobject]@{ page = $_.RelativePath; filename = $_.Title; heading = $_.Heading } })

$DuplicateGroups = @($Pages.Values | Group-Object { Get-CanonicalKey $_.Title } | Where-Object { $_.Count -gt 1 } |
    ForEach-Object { [pscustomobject]@{ canonical_key = $_.Name; pages = @($_.Group.RelativePath | Sort-Object) } })

$SuggestedLinks = @()
if (Test-Path -LiteralPath $LibraryPath) {
    try {
        $Entries = @(Get-Content -LiteralPath $LibraryPath -Raw | ConvertFrom-Json)
        $SuggestedLinks = @($Entries | ForEach-Object {
            $SourcePage = $_.wiki_path -replace '^Wiki/', ''
            foreach ($Candidate in @($_.links)) {
                $Key = Get-CanonicalKey $Candidate
                if ($KeyToPage.ContainsKey($Key) -and $SourcePage -and $SourcePage -ne $KeyToPage[$Key]) {
                    [pscustomobject]@{ source = $SourcePage; target = $KeyToPage[$Key]; suggested_by = $_.source_name }
                }
            }
        } | Sort-Object source, target -Unique)
    } catch {
        Write-Warning "library.json could not be parsed; link recommendations were skipped."
    }
}

$Proposals = @($SuggestedLinks | ForEach-Object {
    New-ExistingLinkProposal -Page $Pages[$_.source] -TargetPath $_.target
} | Where-Object { $_ })

$Report = [ordered]@{
    generated_at = (Get-Date -Format 'o')
    mode = if ($Mode -eq 'Proposal') { 'proposal-only' } else { 'report-only' }
    scope = 'Wiki markdown pages excluding README.md'
    summary = [ordered]@{
        pages = $Pages.Count; explicit_links = $Edges.Count; unresolved_links = $Unresolved.Count
        orphan_pages = $Orphans.Count; sparse_pages = $Sparse.Count; duplicate_concept_groups = $DuplicateGroups.Count
        graph_density = if ($Pages.Count -gt 1) { [math]::Round($Edges.Count / ($Pages.Count * ($Pages.Count - 1)), 4) } else { 0 }
    }
    orphan_pages = $Orphans
    sparse_pages = $Sparse
    inconsistent_naming = $Naming
    duplicate_concepts = $DuplicateGroups
    unresolved_wiki_links = @($Unresolved | Sort-Object page, target)
    recommended_existing_links = $SuggestedLinks
    link_proposals = $Proposals
}

if ($Mode -eq 'Proposal') {
    $ProposalMarkdown = @"
# Ariadne Knowledge Link Proposals

Generated: $($Report.generated_at)

Mode: proposal-only. This command did not write any files.

## Proposed Changes

$(if ($Proposals.Count) { ($Proposals | ForEach-Object {
@"
### ``Wiki/$($_.page)`` → ``Wiki/$($_.target)``

Exact markdown to add:

~~~markdown
$($_.added_lines -join "`n")
~~~

Before (end of file):

~~~markdown
$((($_.before -split "`r?`n" | Select-Object -Last 8) -join "`n"))
~~~

After (end of file):

~~~markdown
$((($_.after -split "`r?`n" | Select-Object -Last 11) -join "`n"))
~~~

Diff:

~~~diff
$($_.diff)
~~~
"@
}) -join "`n" } else { 'No safe existing-page links are currently eligible for proposal.' })

## Guardrails

- Only exact matches to existing wiki pages are proposed.
- No pages, aliases, source metadata, or Knowledge Map entries are created or changed.
- Apply remains a separate future action.
"@
    Write-Output $ProposalMarkdown
    return
}

$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$JsonPath = Join-Path $OutputDirectory "knowledge-health-$Stamp.json"
$MarkdownPath = Join-Path $OutputDirectory "knowledge-health-$Stamp.md"
$Report | ConvertTo-Json -Depth 8 | Out-File -LiteralPath $JsonPath -Encoding utf8 -NoNewline

$Markdown = @"
# Ariadne Knowledge Health Report

Generated: $($Report.generated_at)

Mode: report-only. No wiki, library, or source files were changed.

## Summary

| Metric | Value |
| --- | ---: |
| Wiki pages | $($Report.summary.pages) |
| Explicit internal links | $($Report.summary.explicit_links) |
| Graph density | $($Report.summary.graph_density) |
| Orphan pages | $($Report.summary.orphan_pages) |
| Sparse pages | $($Report.summary.sparse_pages) |
| Duplicate concept groups | $($Report.summary.duplicate_concept_groups) |
| Unresolved wiki links | $($Report.summary.unresolved_links) |

## Recommended Existing Links

$(if ($SuggestedLinks.Count) { ($SuggestedLinks | ForEach-Object { "- ``$($_.source)`` → ``$($_.target)`` (suggested by $($_.suggested_by))" }) -join "`n" } else { 'None.' })

## Orphan Pages

$(if ($Orphans.Count) { ($Orphans | ForEach-Object { "- ``$_``" }) -join "`n" } else { 'None.' })

## Sparse Pages

$(if ($Sparse.Count) { ($Sparse | ForEach-Object { "- ``$($_.page)`` — $($_.sources) source(s), $($_.characters) characters" }) -join "`n" } else { 'None.' })

## Follow-up

Review the companion JSON for duplicate candidates, naming inconsistencies, and unresolved links. Recommendations are evidence only; canonicalization and merge decisions remain human-controlled.
"@
$Markdown | Out-File -LiteralPath $MarkdownPath -Encoding utf8 -NoNewline

Write-Host "Knowledge health report written: $MarkdownPath"
Write-Host "Structured report written: $JsonPath"
