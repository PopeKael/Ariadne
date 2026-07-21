# Checkpointed rebuild runner

`00_System/run_rebuild.py` is the review-only, resumable runner for rebuild-v1. It reads canonical source Markdown and writes only beneath `00_System/Data/rebuild-v1/`.

Start or resume the full rebuild:

```powershell
py -3 .\00_System\run_rebuild.py --vault .
```

The checkpoint is `00_System/Data/rebuild-v1/bulk/state.json`. After every source the runner atomically replaces this checkpoint and its derived catalogue/capture files. Pressing `Ctrl+C` prints confirmation once the current checkpoint is safe. Running the same command resumes automatically; `--retry-recorded` explicitly retries prior rejected or transport-failed records.

All people, entities, concepts and links are review candidates only. Generic candidates are marked as held rather than promoted. No command in this runner changes source Markdown, identity records, graph pages, active wiki output, or MCP paths.

The Ollama request uses temperature zero and a fixed seed (`42`) so an unchanged source set and model installation produce repeatable advisory results.
