# Maintenance script audit — rebuild-v1

Audited: 2026-07-22. This review is read-only; no legacy script was executed.

## Decision

Use `Daily-Ingest.ps1` for ordinary ingestion. The old PowerShell maintenance
surface must not be treated as current. The rebuilt `Start-AriadneControl.ps1`
now exposes only the supported rebuild-v1 command allow-list.

## Supported commands

The definitive command list is [Supported Commands](../docs/Supported-Commands.md).

| Command | Status | Reason |
|---|---|---|
| `Daily-Ingest.ps1` | supported daily command | Rebuild-v1 `/api/chat` ingestion, validation, lock, integration, filing and embeddings. |
| `Build-Embeddings.ps1 -Status` | supported diagnostic | Read-only index status. |
| `Build-Embeddings.ps1 -Rebuild` | controlled maintenance | Regenerates derived index only; use after index loss/corruption or planned retrieval changes. |
| `evaluate_retrieval.py` | supported diagnostic | Versioned retrieval benchmark. |
| `audit_failed_ingestion.py` | supported diagnostic | Read-only audit of rebuild-v1 rejection artefacts. |
| `remediate_failed_rebuild.py` | controlled maintenance | Scoped, audited, idempotent recovery only. |
| `run_rebuild.py` | internal/operator-only | Full controlled rebuild; not daily intake. |
| `integrate_rebuild.py` | internal-only | Called by approved runners after terminal-state checks. |

## Retired or unsafe legacy writers

| Script | Finding | Disposition |
|---|---|---|
| `ariadne.ps1` | v0.7 pipeline; `/api/generate`; legacy retry queue; writes old library/review/wiki workflows. | Retain as historical; never run. |
| `Run Injest.ps1` | Wrapper for `ariadne.ps1`; typo preserved in filename. | Retain as historical; never run. |
| `Retry-FailedIngestion.ps1` | Moves all `Failed/` Markdown back to `Inbox/` with `-Force`, then tells operator to run `ariadne.ps1`. | Retain as historical; never run. |
| `Repair-RetryQueue.ps1` | Rewrites legacy `Logs/IngestionRetryQueue.json`. | Retain as historical; never run. |
| `Reclassify-All.ps1` | Uses `/api/generate` and mutates catalogue classifications. | Retain as historical; never run. |
| `Reclassification-Status.ps1` | Reports legacy log/queue semantics, not rebuild-v1 state. | Retain as historical. |
| `Capture-ControlledOllama.ps1` | Experimental `/api/generate` capture. | Retain as historical evidence only. |
| `Compile-Knowledge.ps1` | Can create or alter wiki concept/link material. | Retain as historical; conflicts with review-only candidates. |
| `Invoke-GraphLinking.ps1` | Creates `Entities/` and `Wiki/Concepts/` pages automatically. | Retain as historical; never run. |
| `Rebuild-GraphRelations.ps1`, `Reconcile-Graph.ps1` | Mutate `related_notes` in the active catalogue from legacy heuristics. | Retain as historical; never run. |
| `Migrate-LegacyGraph.ps1`, `Migrate-PersonEntities.ps1`, `Resolve-PersonIdentities.ps1` | Migration writers which create/alter legacy graph and People data. | Retain as historical; never run. |
| `GraphHealth.ps1` | Assumes visible legacy concept/entity pages and legacy retry queue. | Retain as historical; results are not meaningful for rebuild-v1. |
| `Publish-Knowledge.ps1` | Overwrites generated wiki from legacy `KnowledgeMap.md`. | Retain as historical; never run. |
| `Resort-Archive.ps1` | Hard-coded legacy reclassification and wiki rewrites. | Retain as historical; never run. |
| `Repair-LibraryIndex.ps1` | Deletes a named catalogue entry directly. | Retain as emergency historical material only. |
| `Commit.ps1` | Runs `git add -A` then pushes `main`; unsafe with generated/GUI changes. | Retain as historical; use explicit Git commands. |
| `Start-AriadneControl.ps1` | Rebuilt to expose only supported rebuild-v1 commands. | Supported loopback menu. |
| `cleanup_legacy_graph.py` | One-off completed cleanup. | Retain as completed migration record; do not rerun without fresh audit. |

## Confirmed architecture boundary

Rebuild-v1 owns active `library.json`, the active generated wiki, the MCP
catalogue/chunks, and the embedding index. It does **not** auto-create People,
Entities, or Concept pages. Any legacy tool that writes those areas is outside
the supported workflow.

## Next cleanup task

After the web-management page has been rebuilt to surface only supported
commands, the retired scripts can be moved—byte-for-byte—to an excluded
`Archive/LegacyMaintenance/` namespace with this report and their hashes.
Do not delete them until that archive movement has its own checkpoint and
idempotency check.
