# Model Lab benchmark harness

Model Lab is Ariadne's native, repeatable local-model test surface. It is not
Open WebUI, a second production chat route, or a Docker-managed application.
It calls the configured Ollama provider directly and leaves Home routing
unchanged.

## Run contract

Each run is either:

- **STANDARD** — the selected recipe's canonical parameters and required source
  shape are intact.
- **ADAPTED** — the operator deliberately changed one or more canonical
  parameters; the changed fields are recorded.
- **STANDARD INCOMPATIBLE** — the model or supplied source cannot satisfy the
  recipe. The run must not silently lower context, output, or reasoning mode.

The recipe supplies **TEST INSTRUCTIONS** and Test 1 restores its canonical
two-file **SOURCE MATERIAL** bundle automatically. The bundle is the Ten Years
in Thailand transcript plus the YouTube Package instruction document. The
operator can inspect, reorder, or remove files for an Adapted run, but a New
Run restores the canonical pair without another file chooser operation.

## Test 1: 10 Years in Thailand / YouTube Packaging

The canonical recipe uses context 16,384, requested output 4,096, temperature
0, top-p 0.9, seed 42, one Markdown source, and fresh text-only execution with
web, tools, Vault retrieval, memory, and conversation history disabled. The
recipe records grounding, instruction following, completeness, and creative
synthesis for later human review.

The UI exposes OFF, LOW, MEDIUM, HIGH, and MAX as a provider-neutral control.
Only modes negotiated from the selected provider are enabled. Ollama currently
reports a boolean thinking capability for the verified Qwen model, so OFF maps
to think:false and LOW maps to think:true; the other discrete levels stay
disabled until the provider can represent them honestly.

## Observability and states

The authoritative states are READY, PREPARING, LOADING MODEL, THINKING,
GENERATING, RECORDING, COMPLETED, TRUNCATED, and FAILED. Context intelligence
shows the configured context, estimated instruction/source tokens, requested
output, model maximum, and remaining headroom. A response that ends because of
a context or output limit is TRUNCATED, never a successful run.

During a streamed run, the UI retains accumulated thinking and final output,
polls native GPU/model/host telemetry, and records Rust/avatar transition
acknowledgements through the existing host IPC path. A requested cue without a
host acknowledgement is not presented as proof of activity.

## Reset and export rules

**New run** clears source files, live output, and the current result while
preserving the selected model and recipe. **Reset test defaults** restores the
recipe's canonical parameters and preserves history. Completed runs can export
the final response or a complete Markdown record containing the recipe,
capabilities, source hashes, instructions, effective parameters, thinking,
answer, and telemetry.

## Runtime boundary

Normal Ariadne startup, Model Lab operation, profile transitions, gaming
shutdown, and Ariadne shutdown must not start, stop, restart, probe, recover,
repair, or wait for Docker. Open WebUI is retired from the normal runtime path.
Docker and Compose remain packaging/deployment artifacts for a future explicit
Build/Deploy-to-Hera workflow only. Local DEV Signal/Discovery is manual and
unavailable until that separate workflow exists; RUN · HERA continues to use
the remote Signal/Discovery endpoints.
