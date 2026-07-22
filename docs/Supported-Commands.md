# Supported Knowledge Vault commands

This is the operational command surface for rebuild-v1. Commands not listed
below are either internal helpers, one-off migration tools, or legacy writers.
Do not use them for ordinary intake, graph maintenance, publishing, retries, or
repair without a fresh, specifically scoped maintenance task.

## Normal daily operation

From the vault root, process genuinely new Markdown in `Inbox/`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\00_System\Daily-Ingest.ps1"
```

This is the only supported daily ingestion command. It uses the rebuild-v1
`/api/chat` adapter, stable identities and canonical hashes, review-only
candidates, atomic output writes, an exclusive ingestion lock, active-catalogue
merge, source filing, and incremental embeddings.

## Safe diagnostics

```powershell
# Inspect the current local embedding index.
powershell -NoProfile -ExecutionPolicy Bypass -File ".\00_System\Build-Embeddings.ps1" -Status

# Run the versioned chunk-retrieval benchmark.
py -3 .\00_System\evaluate_retrieval.py

# Run developer regression tests (uses stdlib unittest; pytest is not required).
py -3 -m unittest .\00_System\test_ollama_adapter.py .\00_System\test_rebuild_lock.py .\00_System\test_rebuild_foundation.py .\00_System\test_rebuild_safeguards.py .\00_System\test_citations.py .\00_System\test_retrieval_evaluation.py
```

## Controlled maintenance only

These are supported only when their stated preconditions are met. They are not
part of daily operation.

```powershell
# Recreate the derived embedding index after index loss/corruption or a planned
# retrieval-design change. Daily ingestion normally updates it incrementally.
powershell -NoProfile -ExecutionPolicy Bypass -File ".\00_System\Build-Embeddings.ps1" -Rebuild

# Produce a read-only audit of the completed rebuild-v1 rejection artefacts.
py -3 .\00_System\audit_failed_ingestion.py --vault . --stamp YYYYMMDD
```

`remediate_failed_rebuild.py` is a deliberately scoped recovery tool. Use it
only against a named, reviewed audit report and only after a Git checkpoint:

```powershell
py -3 .\00_System\remediate_failed_rebuild.py --vault . --audit ".\Reports\Failed-Ingestion-Audit-YYYYMMDD.json" --archive-obsolete
```

It refuses concurrent ingestion, backs up selected original bytes, and is
idempotent for the same selection. It is not a replacement for daily intake.

## Internal helpers — do not run directly for routine work

- `daily_rebuild_ingest.py` — implementation behind `Daily-Ingest.ps1`.
- `run_rebuild.py` — controlled full-rebuild runner, not a daily command.
- `integrate_rebuild.py` — low-level integration invoked by approved runners.
- `vault_rebuild.py`, `run_rebuild_pilot.py`, `ariadne_mcp.py`,
  `ariadne_embeddings.py`, and report helpers — modules/library interfaces.

## Explicitly unsupported / retired

Do not run `ariadne.ps1`, `Run Injest.ps1`, `Retry-FailedIngestion.ps1`, or
`Start-AriadneControl.ps1`: the current control menu routes to legacy writers.
The legacy graph, migration, classification, retry-queue, repair, compiler,
publisher, and blanket Git scripts are catalogued in the maintenance audit.
They are retained as historical material only.
