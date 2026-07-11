# Ariadne v0.8 Knowledge Compiler
# Conservative wiki-link compiler. It only owns ## Related Concepts sections.

[CmdletBinding()]
param(
    [string]$Vault = "D:\Downloads\KnowledgeVault",
    [string]$OutputDirectory,
    [ValidateSet('Report', 'Proposal', 'Apply', 'Promote')]
    [string]$Mode = 'Report',
    [string[]]$ApprovedCandidates = @()
)

$Wiki = Join-Path $Vault "Wiki"
$LibraryPath = Join-Path $Vault "00_System\library.json"
if ($Mode -ne 'Proposal' -and [string]::IsNullOrWhiteSpace($OutputDirectory)) {
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

    $TargetLink = $TargetPath -replace '\.md$', ''
    $Link = "[[${TargetLink}]]"
    if ($Page.Content -match [regex]::Escape($Link)) { return $null }

    $NewLine = if ($Page.Content -match "`r`n") { "`r`n" } else { "`n" }
    $Section = [regex]::Match($Page.Content, '(?ms)^## Related Concepts\s*\r?\n(.*?)(?=^##\s|\z)')
    if ($Section.Success) {
        # Do not touch a hand-authored section. A compiler-owned section contains
        # only blank lines and simple wiki-link bullets.
        $ExistingLines = @($Section.Groups[1].Value -split "`r?`n" | Where-Object { $_.Trim() })
        if (@($ExistingLines | Where-Object { $_ -notmatch '^\s*-\s+\[\[[^\]|#]+(?:\|[^\]]+)?\]\]\s*$' }).Count -gt 0) {
            return $null
        }
        $ExistingLinks = @($ExistingLines | ForEach-Object { ([regex]::Match($_, '\[\[([^\]|#]+)')).Groups[1].Value.Trim() })
        $AllLinks = @($ExistingLinks + $TargetLink | Sort-Object -Unique)
        $Replacement = "## Related Concepts$NewLine$NewLine" + (($AllLinks | ForEach-Object { "- [[$_]]" }) -join $NewLine) + $NewLine
        $Before = $Page.Content
        $After = $Page.Content.Substring(0, $Section.Index) + $Replacement + $Page.Content.Substring($Section.Index + $Section.Length)
        $AddedLines = @("- $Link")
    } else {
        $Before = $Page.Content
        $Separator = if ($Before.EndsWith("`n")) { $NewLine } else { "$NewLine$NewLine" }
        $After = "$Before$Separator## Related Concepts$NewLine$NewLine- $Link$NewLine"
        $AddedLines = @('## Related Concepts', '', "- $Link")
    }

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
            "@@ Related Concepts @@",
            $(($AddedLines | ForEach-Object { "+$_" }) -join "`n")
        ) -join "`n"
    }
}

function Get-GraphAnalysis {
    param($Pages, $KeyToPage)

    $Edges = [System.Collections.Generic.List[object]]::new()
    $Unresolved = [System.Collections.Generic.List[object]]::new()
    foreach ($Page in $Pages.Values) {
        foreach ($Target in @(Get-WikiLinks $Page.Content)) {
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
    return [pscustomobject]@{
        edges = @($Edges)
        unresolved = @($Unresolved | Sort-Object page, target)
        orphans = @($Pages.Keys | Where-Object { $Inbound[$_] -eq 0 -and $Outbound[$_] -eq 0 } | Sort-Object)
        density = if ($Pages.Count -gt 1) { [math]::Round($Edges.Count / ($Pages.Count * ($Pages.Count - 1)), 4) } else { 0 }
    }
}

function ConvertTo-PromotionName {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) { return $null }
    $Name = $Value -replace '^\s*-\s*', '' -replace '\[\[|\]\]', '' -replace '^["'']|["'']$', ''
    $Name = $Name.Trim()
    if ($Name -match '^(published|created)\s*:' -or $Name.Length -lt 3) { return $null }
    return $Name
}

function Get-AuthorNamespace {
    param([string]$Name)
    # Deterministic and conservative: organisations/channels are entities; a
    # short title-cased personal name is the only automatic People classification.
    if ($Name -match '(?i)\b(news|media|tv|channel|podcast|studio|official|daily|company|inc|ltd|university|institute|legal|law|consulting|group|network|foundation|club)\b') {
        return 'Entities'
    }
    if ($Name -match '^(?:[A-Z][\p{L}''-]+\s+){1,3}[A-Z][\p{L}''-]+$') { return 'People' }
    return 'Entities'
}

function Add-PromotionEvidence {
    param(
        [hashtable]$Candidates,
        [hashtable]$NodeRegistry,
        [string]$Name,
        [string]$Namespace,
        $Entry,
        [string]$StableIdentifier
    )

    $Name = ConvertTo-PromotionName -Value $Name
    if (!$Name -or !$Entry.document_id) { return }
    $Key = "$Namespace|$(Get-CanonicalKey $Name)"
    if ($NodeRegistry.ContainsKey($Key)) { return }
    if (!$Candidates.ContainsKey($Key)) {
        $Candidates[$Key] = [pscustomobject]@{
            candidate_name = $Name; recommended_namespace = $Namespace
            evidence_count = 0
            document_ids = [System.Collections.Generic.HashSet[string]]::new()
            stable_identifiers = [System.Collections.Generic.HashSet[string]]::new()
            related_existing_nodes = [System.Collections.Generic.HashSet[string]]::new()
            source_documents = @{}
        }
    }
    $Candidate = $Candidates[$Key]
    $Candidate.evidence_count++
    [void]$Candidate.document_ids.Add([string]$Entry.document_id)
    $Candidate.source_documents[[string]$Entry.document_id] = [string]$Entry.source_name
    if ($StableIdentifier) { [void]$Candidate.stable_identifiers.Add($StableIdentifier) }
    if ($Entry.wiki_path) { [void]$Candidate.related_existing_nodes.Add([string]$Entry.wiki_path) }
}

function Get-NodeId {
    param([string]$Namespace, [string]$Name)
    $Input = "$Namespace|$(Get-CanonicalKey $Name)"
    $Sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $Hash = [System.BitConverter]::ToString($Sha256.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Input))).Replace('-', '').ToLowerInvariant()
        return "node:$($Namespace.ToLowerInvariant()):$($Hash.Substring(0, 16))"
    } finally { $Sha256.Dispose() }
}

function ConvertTo-NodeFileName {
    param([string]$Name)
    $Safe = $Name.Trim()
    foreach ($Character in [System.IO.Path]::GetInvalidFileNameChars()) { $Safe = $Safe.Replace($Character, '-') }
    return (($Safe -replace '\s+', ' ').Trim() + '.md')
}

if (!(Test-Path -LiteralPath $Wiki)) { throw "Wiki directory not found: $Wiki" }
if ($Mode -ne 'Proposal') {
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

$BeforeGraph = Get-GraphAnalysis -Pages $Pages -KeyToPage $KeyToPage
$Orphans = $BeforeGraph.orphans
$Sparse = @($Pages.Values | Where-Object { $_.SourceCount -eq 0 -or $_.Content.Trim().Length -lt 300 } |
    ForEach-Object { [pscustomobject]@{ page = $_.RelativePath; sources = $_.SourceCount; characters = $_.Content.Trim().Length } } |
    Sort-Object characters, page)
$Naming = @($Pages.Values | Where-Object {
    $_.Title -ne $_.Title.Trim() -or ($_.Heading -and $_.Heading -ne $_.Title)
} | ForEach-Object { [pscustomobject]@{ page = $_.RelativePath; filename = $_.Title; heading = $_.Heading } })

$DuplicateGroups = @($Pages.Values | Group-Object { Get-CanonicalKey $_.Title } | Where-Object { $_.Count -gt 1 } |
    ForEach-Object { [pscustomobject]@{ canonical_key = $_.Name; pages = @($_.Group.RelativePath | Sort-Object) } })

$NodeRegistry = @{}
$NamespacePaths = @{
    'Wiki/Concepts' = (Join-Path $Wiki 'Concepts')
    'People' = (Join-Path $Vault 'People')
    'Entities' = (Join-Path $Vault 'Entities')
}
foreach ($Registry in @($NamespacePaths.GetEnumerator() | ForEach-Object {
    [pscustomobject]@{ namespace = $_.Key; path = $_.Value }
})) {
    if (Test-Path -LiteralPath $Registry.path) {
        Get-ChildItem -LiteralPath $Registry.path -Recurse -File -Filter *.md | Where-Object { $_.Name -ne 'README.md' } | ForEach-Object {
            $NodeRegistry["$($Registry.namespace)|$(Get-CanonicalKey $_.BaseName)"] = $_.FullName
        }
    }
}

# The compiler writes only Wiki pages, but existing People and Entities are valid
# link targets. Qualified links keep namespaces unambiguous.
$KeyToLinkTarget = @{}
foreach ($Key in $KeyToPage.Keys) { $KeyToLinkTarget[$Key] = $KeyToPage[$Key] }
foreach ($RegistryKey in $NodeRegistry.Keys) {
    $Parts = $RegistryKey -split '\|', 2
    if ($Parts[0] -in @('People', 'Entities')) {
        $KeyToLinkTarget[$Parts[1]] = "$($Parts[0])/$([System.IO.Path]::GetFileNameWithoutExtension($NodeRegistry[$RegistryKey]))"
    }
}

$LibraryEntries = @()
$SuggestedLinks = @()
if (Test-Path -LiteralPath $LibraryPath) {
    try {
        $LibraryEntries = @(Get-Content -LiteralPath $LibraryPath -Raw | ConvertFrom-Json)
        $SuggestedLinks = @($LibraryEntries | ForEach-Object {
            $SourcePage = $_.wiki_path -replace '^Wiki/', ''
            foreach ($Candidate in @($_.links)) {
                $Key = Get-CanonicalKey $Candidate
                if ($KeyToLinkTarget.ContainsKey($Key) -and $SourcePage -and $SourcePage -ne $KeyToLinkTarget[$Key]) {
                    [pscustomobject]@{ source = $SourcePage; target = $KeyToLinkTarget[$Key]; suggested_by = $_.source_name }
                }
            }
            foreach ($Candidate in @($_.subtopics) + @(ConvertTo-PromotionName -Value $_.channel_author)) {
                $Key = Get-CanonicalKey $Candidate
                if ($KeyToLinkTarget.ContainsKey($Key) -and $SourcePage -and $SourcePage -ne $KeyToLinkTarget[$Key]) {
                    [pscustomobject]@{ source = $SourcePage; target = $KeyToLinkTarget[$Key]; suggested_by = "$($_.source_name) (structured node reference)" }
                }
            }
        } | Sort-Object source, target -Unique)
    } catch {
        Write-Warning "library.json could not be parsed; link recommendations were skipped."
    }
}

# Wiki source blocks are evidence too: historical `Related:` values that exactly
# name an existing page are eligible, without inventing a new concept.
$WikiSuggestedLinks = @($Pages.Values | ForEach-Object {
    $SourcePage = $_.RelativePath
    foreach ($Match in [regex]::Matches($_.Content, '(?m)^\s*- Related:\s*(.+?)\s*$')) {
        foreach ($Candidate in ($Match.Groups[1].Value -split ',')) {
            $Key = Get-CanonicalKey ($Candidate.Trim() -replace '^\[\[|\]\]$', '')
            if ($KeyToPage.ContainsKey($Key) -and $SourcePage -ne $KeyToPage[$Key]) {
                [pscustomobject]@{ source = $SourcePage; target = $KeyToPage[$Key]; suggested_by = 'wiki Related metadata' }
            }
        }
    }
})
$SuggestedLinks = @($SuggestedLinks + $WikiSuggestedLinks | Sort-Object source, target -Unique)

$PromotionEvidence = @{}
foreach ($Entry in $LibraryEntries) {
    # Subtopics are the only model-produced field treated as a concept candidate.
    # Tags are intentionally excluded: they are descriptive labels, not nodes.
    foreach ($Subtopic in @($Entry.subtopics)) {
        Add-PromotionEvidence -Candidates $PromotionEvidence -NodeRegistry $NodeRegistry -Name $Subtopic -Namespace 'Wiki/Concepts' -Entry $Entry
    }

    $AuthorName = ConvertTo-PromotionName -Value $Entry.channel_author
    if ($AuthorName) {
        Add-PromotionEvidence -Candidates $PromotionEvidence -NodeRegistry $NodeRegistry -Name $AuthorName -Namespace (Get-AuthorNamespace -Name $AuthorName) -Entry $Entry
    }

    # Domains are conservative Entity candidates because their normalized origin
    # is also a stable, directly inspectable identifier.
    foreach ($Url in @($Entry.links)) {
        try {
            $Uri = [System.Uri]$Url
            if ($Uri.Scheme -in @('http', 'https')) {
                $Host = $Uri.Host.ToLowerInvariant()
                Add-PromotionEvidence -Candidates $PromotionEvidence -NodeRegistry $NodeRegistry -Name $Host -Namespace 'Entities' -Entry $Entry -StableIdentifier ("{0}://{1}" -f $Uri.Scheme, $Host)
            }
        } catch { }
    }
}

$PromotionCandidates = @($PromotionEvidence.Values | Where-Object { $_.document_ids.Count -ge 3 } | ForEach-Object {
    $Confidence = 0.65
    if ($_.evidence_count -ge 5) { $Confidence += 0.1 }
    if ($_.stable_identifiers.Count -gt 0) { $Confidence += 0.15 }
    [pscustomobject]@{
        candidate_name = $_.candidate_name
        recommended_namespace = $_.recommended_namespace
        evidence_count = $_.evidence_count
        distinct_source_count = $_.document_ids.Count
        stable_identifiers = @($_.stable_identifiers | Sort-Object)
        related_existing_nodes = @($_.related_existing_nodes | Sort-Object)
        source_documents = @($_.source_documents.GetEnumerator() | Sort-Object Key | ForEach-Object {
            [pscustomobject]@{ document_id = $_.Key; source_name = $_.Value }
        })
        confidence = [math]::Round([math]::Min($Confidence, 0.9), 2)
    }
} | Sort-Object recommended_namespace, candidate_name)

$Proposals = @($SuggestedLinks | ForEach-Object {
    New-ExistingLinkProposal -Page $Pages[$_.source] -TargetPath $_.target
} | Where-Object { $_ })

$Promoted = @()
if ($Mode -eq 'Promote') {
    if ($ApprovedCandidates.Count -eq 0) {
        throw 'Promote requires explicit approval: pass one or more exact candidate names with -ApprovedCandidates.'
    }
    foreach ($ApprovedName in $ApprovedCandidates) {
        $Matches = @($PromotionCandidates | Where-Object { $_.candidate_name -ieq $ApprovedName })
        if ($Matches.Count -ne 1) {
            throw "Approved candidate '$ApprovedName' did not match exactly one current promotion candidate."
        }
        $Candidate = $Matches[0]
        $RegistryKey = "$($Candidate.recommended_namespace)|$(Get-CanonicalKey $Candidate.candidate_name)"
        if ($NodeRegistry.ContainsKey($RegistryKey)) {
            Write-Warning "Skipped existing canonical node: $($Candidate.candidate_name)"
            continue
        }
        $Directory = $NamespacePaths[$Candidate.recommended_namespace]
        if (!(Test-Path -LiteralPath $Directory)) { throw "Canonical namespace directory not found: $Directory" }
        $RecordPath = Join-Path $Directory (ConvertTo-NodeFileName -Name $Candidate.candidate_name)
        if (Test-Path -LiteralPath $RecordPath) { throw "Refusing to overwrite existing node record: $RecordPath" }

        $StableIdentifiers = if ($Candidate.stable_identifiers.Count) { ($Candidate.stable_identifiers | ForEach-Object { "- $_" }) -join "`n" } else { '- None' }
        $SourceDocuments = ($Candidate.source_documents | ForEach-Object { "- $($_.document_id) — $($_.source_name)" }) -join "`n"
        @"
# $($Candidate.candidate_name)

NodeId: $(Get-NodeId -Namespace $Candidate.recommended_namespace -Name $Candidate.candidate_name)

Namespace: $($Candidate.recommended_namespace)

Stable Identifiers:
$StableIdentifiers

Created: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')

Evidence Summary: $($Candidate.evidence_count) references across $($Candidate.distinct_source_count) distinct source documents; compiler confidence $($Candidate.confidence).

## Source Documents

$SourceDocuments
"@ | Out-File -LiteralPath $RecordPath -Encoding utf8 -NoNewline

        $Promoted += [pscustomobject]@{ candidate_name = $Candidate.candidate_name; namespace = $Candidate.recommended_namespace; node_id = (Get-NodeId -Namespace $Candidate.recommended_namespace -Name $Candidate.candidate_name); record_path = $RecordPath }
        $NodeRegistry[$RegistryKey] = $RecordPath
    }
}

$Applied = @()
if ($Mode -eq 'Apply') {
    foreach ($Proposal in $Proposals) {
        $PagePath = Join-Path $Wiki ($Proposal.page -replace '/', '\\')
        $CurrentContent = Get-Content -LiteralPath $PagePath -Raw
        if ($CurrentContent -ne $Proposal.before) {
            Write-Warning "Skipped changed page: $($Proposal.page)"
            continue
        }
        $Proposal.after | Out-File -LiteralPath $PagePath -Encoding utf8 -NoNewline
        $Pages[$Proposal.page].Content = $Proposal.after
        $Pages[$Proposal.page].Links = @(Get-WikiLinks $Proposal.after)
        $Applied += $Proposal
    }
}
$AfterGraph = Get-GraphAnalysis -Pages $Pages -KeyToPage $KeyToPage

$Report = [ordered]@{
    generated_at = (Get-Date -Format 'o')
    mode = switch ($Mode) { 'Proposal' { 'proposal-only' } 'Apply' { 'applied' } 'Promote' { 'promoted' } default { 'report-only' } }
    scope = 'Wiki markdown pages excluding README.md'
    summary = [ordered]@{
        pages = $Pages.Count; links_added = $Applied.Count; pages_modified = @($Applied.page | Sort-Object -Unique).Count
        explicit_links_before = $BeforeGraph.edges.Count; explicit_links_after = $AfterGraph.edges.Count
        unresolved_links = $AfterGraph.unresolved.Count; orphan_pages = $AfterGraph.orphans.Count
        sparse_pages = $Sparse.Count; duplicate_concept_groups = $DuplicateGroups.Count
        graph_density_before = $BeforeGraph.density; graph_density_after = $AfterGraph.density
        promotion_candidates = $PromotionCandidates.Count
        nodes_promoted = $Promoted.Count
    }
    orphan_pages = $AfterGraph.orphans
    sparse_pages = $Sparse
    inconsistent_naming = $Naming
    duplicate_concepts = $DuplicateGroups
    unresolved_wiki_links = $AfterGraph.unresolved
    recommended_existing_links = $SuggestedLinks
    link_proposals = $Proposals
    applied_links = $Applied
    promotion_candidates = $PromotionCandidates
    promoted_nodes = $Promoted
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

Mode: $($Report.mode).

## Summary

| Metric | Value |
| --- | ---: |
| Wiki pages | $($Report.summary.pages) |
| Links added | $($Report.summary.links_added) |
| Pages modified | $($Report.summary.pages_modified) |
| Explicit internal links (before → after) | $($Report.summary.explicit_links_before) → $($Report.summary.explicit_links_after) |
| Graph density (before → after) | $($Report.summary.graph_density_before) → $($Report.summary.graph_density_after) |
| Orphan pages | $($Report.summary.orphan_pages) |
| Sparse pages | $($Report.summary.sparse_pages) |
| Duplicate concept groups | $($Report.summary.duplicate_concept_groups) |
| Unresolved wiki links | $($Report.summary.unresolved_links) |
| Promotion candidates | $($Report.summary.promotion_candidates) |
| Nodes promoted | $($Report.summary.nodes_promoted) |

## Recommended Existing Links

$(if ($SuggestedLinks.Count) { ($SuggestedLinks | ForEach-Object { "- ``$($_.source)`` → ``$($_.target)`` (suggested by $($_.suggested_by))" }) -join "`n" } else { 'None.' })

## Applied Links

$(if ($Applied.Count) { ($Applied | ForEach-Object { "- ``$($_.page)`` → ``$($_.target)``" }) -join "`n" } else { 'None.' })

## Promotion Candidates

$(if ($PromotionCandidates.Count) { ($PromotionCandidates | ForEach-Object {
@"
### $($_.candidate_name)

- Recommended namespace: ``$($_.recommended_namespace)``
- Evidence count: $($_.evidence_count)
- Distinct sources: $($_.distinct_source_count)
- Stable identifiers: $(if ($_.stable_identifiers.Count) { $_.stable_identifiers -join ', ' } else { 'None' })
- Related existing nodes: $(if ($_.related_existing_nodes.Count) { $_.related_existing_nodes -join ', ' } else { 'None' })
- Confidence: $($_.confidence)
"@
}) -join "`n" } else { 'None. Candidates require evidence from at least three distinct document IDs.' })

## Promoted Nodes

$(if ($Promoted.Count) { ($Promoted | ForEach-Object { "- ``$($_.candidate_name)`` → ``$($_.namespace)`` (`$($_.node_id)`)" }) -join "`n" } else { 'None.' })

## Orphan Pages

$(if ($Orphans.Count) { ($Orphans | ForEach-Object { "- ``$_``" }) -join "`n" } else { 'None.' })

## Sparse Pages

$(if ($Sparse.Count) { ($Sparse | ForEach-Object { "- ``$($_.page)`` — $($_.sources) source(s), $($_.characters) characters" }) -join "`n" } else { 'None.' })

## Follow-up

Promotion candidates are report-only and use `library.json` evidence across distinct document IDs. The compiler never creates, modifies, renames, merges, or deletes canonical nodes.
"@
$Markdown | Out-File -LiteralPath $MarkdownPath -Encoding utf8 -NoNewline

if ($Mode -eq 'Promote') {
    $PromotionReportPath = Join-Path $OutputDirectory "promotion-$Stamp.md"
    @"
# Ariadne Promotion Report

Generated: $($Report.generated_at)

Approved candidates: $($ApprovedCandidates -join ', ')

## Nodes Created

$(if ($Promoted.Count) { ($Promoted | ForEach-Object { "- ``$($_.candidate_name)`` → ``$($_.namespace)`` — NodeId: ``$($_.node_id)`` — Record: ``$($_.record_path)``" }) -join "`n" } else { 'None. No node records were created.' })
"@ | Out-File -LiteralPath $PromotionReportPath -Encoding utf8 -NoNewline
    Write-Host "Promotion report written: $PromotionReportPath"
}

Write-Host "Knowledge health report written: $MarkdownPath"
Write-Host "Structured report written: $JsonPath"
