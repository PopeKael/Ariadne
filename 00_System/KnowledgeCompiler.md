# Ariadne Knowledge Compiler (v0.7)

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

Reports are written to `Reports/Ariadne/` as a human-readable Markdown summary and companion JSON. The report covers explicit wiki-link density, orphan and sparse pages, filename/heading mismatches, exact normalized duplicate titles, unresolved links, and link recommendations where historic library metadata names an existing wiki page.

The compiler deliberately treats link and merge recommendations as proposals. Canonical concepts must be decided across the whole vault, not inferred from a single source document.
