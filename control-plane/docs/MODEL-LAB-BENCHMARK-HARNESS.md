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

The recipe supplies **TEST INSTRUCTIONS** from the canonical `2. YouTube
Package.md` file. Test 1 restores its unchanged two-file bundle automatically:
`1. Ten Years in Thailand.md` is **SOURCE MATERIAL** and `2. YouTube
Package.md` is **TEST INSTRUCTIONS**. The model receives each document once in
this exact assembly:

```text
TEST INSTRUCTIONS
<exact content of 2. YouTube Package.md>

SOURCE MATERIAL
<exact content of 1. Ten Years in Thailand.md>
```

New Run and Reset test defaults restore both files. A Test 1 run is STANDARD
only when the canonical filename, role, order, SHA-256, and numeric parameters
all match; a changed, replaced, missing, or reordered file is
STANDARD INCOMPATIBLE rather than silently STANDARD.

## Benchmark generations and history

Test 1's current generation is identified deterministically from its test case,
canonical document filename/role/order/SHA-256 identity, and canonical numeric
parameters. New run records store that generation ID and a separate benchmark
status: **CANONICAL** for an exact match, **CURRENT / ADAPTED** for a run made
under the current generation with deliberate parameter changes, and **LEGACY /
PRE-FREEZE** for records without the current generation identity. The existing
STANDARD, ADAPTED, and STANDARD INCOMPATIBLE classification remains separate.

The history view defaults to **Current benchmark**. **Legacy** and **All**
filters keep earlier records available for forensic reference without including
them in normal comparison views. Old JSONL records are never rewritten or
deleted; their legacy status is derived when history is read.

## Test 1: 10 Years in Thailand / YouTube Packaging

The canonical recipe uses context 16,384, requested output 4,096, temperature
0, top-p 0.9, seed 42, two canonical Markdown files, and fresh text-only execution with
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

**New run** clears live output and the current result, then restores Test 1's
canonical files while preserving the selected model and recipe. **Reset test
defaults** restores the recipe's canonical parameters and both canonical files
while preserving history. Completed runs can export
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
