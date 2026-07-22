# Checkpointed rebuild runner

`00_System/run_rebuild.py` is the review-only, resumable runner for rebuild-v1. It reads canonical source Markdown and writes only beneath `00_System/Data/rebuild-v1/`.

Start or resume the full rebuild:

```powershell
py -3 .\00_System\run_rebuild.py --vault .
```

The checkpoint is `00_System/Data/rebuild-v1/bulk/state.json`. After every source the runner atomically replaces this checkpoint and its derived catalogue/capture files. Pressing `Ctrl+C` prints confirmation once the current checkpoint is safe. Running the same command resumes automatically; `--retry-recorded` explicitly retries prior rejected or transport-failed records.

All people, entities, concepts and links are review candidates only. Generic candidates are marked as held rather than promoted. No command in this runner changes source Markdown, identity records, graph pages, active wiki output, or MCP paths.

The Ollama request uses temperature zero and a fixed seed (`42`) so an unchanged source set and model installation produce repeatable advisory results.

## Normal daily ingestion

Do not use `00_System/ariadne.ps1` for daily intake. That script is the legacy v0.7 pipeline and uses `/api/generate`.

Use the rebuild-v1 daily runner from the vault root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\00_System\Daily-Ingest.ps1"
```

The daily runner reads only new Markdown in `Inbox/`, creates stable source IDs and canonical content hashes, calls local GPT-OSS through `/api/chat`, reads `message.content`, applies the rebuild-v1 schema and semantic validators, and records accepted/rejected outcomes in a checkpoint under `00_System/Data/rebuild-v1/`. It then merges accepted records into the active catalogue, keeps uncertain candidates in the review queue, updates the local embedding index, and files sources into `Processed/` or `Failed/` without changing Markdown bytes. Active JSON/checkpoint/index replacements are atomic and retry bounded Windows sharing violations.

The `pytest` command is not part of normal operation. The repository tests use Python's built-in `unittest`; `pytest` is optional developer tooling only.
