# Ariadne Knowledge Compiler (v0.8)

The Knowledge Compiler is separate from deterministic ingest. It analyses the existing Wiki and `library.json`, then produces evidence for human review. It does not rewrite pages, create concepts, merge aliases, or change the Knowledge Map.

Run it from the vault root:

```powershell
.\00_System\Compile-Knowledge.ps1
```

To inspect exact, non-writing link proposals:

```powershell
.\00_System\Compile-Knowledge.ps1 -Mode Proposal
```

Proposal mode writes nothing. It emits a Markdown report to standard output with the proposed insertion and a before/after diff for every safe link recommendation.

Apply approved, conservative link changes:

```powershell
.\00_System\Compile-Knowledge.ps1 -Mode Apply
```

Apply mode only adds sorted, unique links to `## Related Concepts` sections, and only where exact evidence from existing Wiki metadata or `library.json` names another existing Wiki page. It writes a post-compilation health report with before/after graph metrics.

The health report also lists report-only promotion candidates. These are derived from `library.json` evidence across at least three distinct document identities and are checked against the existing `Wiki/Concepts`, `People`, and `Entities` registries. The compiler never creates or changes nodes.

Reports are written to `Reports/Ariadne/` as a human-readable Markdown summary and companion JSON. The report covers explicit wiki-link density, orphan and sparse pages, filename/heading mismatches, exact normalized duplicate titles, unresolved links, and link recommendations where historic library metadata names an existing wiki page.

The compiler deliberately treats link and merge recommendations as proposals. Canonical concepts must be decided across the whole vault, not inferred from a single source document.
