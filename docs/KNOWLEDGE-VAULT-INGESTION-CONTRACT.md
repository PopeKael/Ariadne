# Knowledge Vault ingestion plugin contract

## Ownership boundaries

Knowledge Vault ingestion is an Ariadne plugin with its own management
workspace at `/knowledge-vault` and focused runtime settings at
`/configuration?plugin=knowledge-vault`. Its saved values live under
`vault_runtime`, separately from Home chat and provider routing. The ingestion
plugin owns job lifecycle and requests the Rust Host's exclusive Vault GPU and
model leases. Knowledge questions continue through the separate retrieval and
MCP interfaces; the ingestion plugin does not advertise itself as an MCP
provider.

Three components have distinct responsibilities:

1. The local model catalogue in `control-plane/inference.py` declares model
   capability limits. GPT-OSS 20B has an 8,192-token maximum regardless of a
   larger context advertised by Ollama or a same-weight local alias.
2. The plugin manifest and runtime settings declare the job requirement:
   local `gpt-oss:20b` at exactly 8,192 context tokens.
3. Ariadne routes the job to that local runtime, and Rust Host admission
   refuses a larger local GPT-OSS 20B context. The cloud overflow route is a
   later project step and is not enabled by this contract yet.

## Context budget

8,192 tokens is the entire local context window. Instructions, source input,
model reasoning, and generated output all consume that same budget. The
worker retains the existing bounded overlapping source spans, reasoning,
validation, embedding handoff, and checkpoint rules. It subtracts the output
budget and a prompt margin before choosing source spans. Output configuration
is limited to 4,096 tokens so it cannot consume the whole window. Requests
above the local model limit fail before transport or Host admission; they are
not silently truncated or resized.

Saved legacy 16K context values are presented as 8K in the runtime snapshot.
The next ordinary configuration save persists the fixed model/context. An
explicit attempt to save another ingestion model or a context other than
8,192 is rejected. Legacy context environment variables do not override the
plugin requirement.

## Verification boundary

Part 1 has offline tests for configuration migration and rejection, catalogue
limits, plugin requirements, worker requests, focused settings UI, and Rust
Host admission. These checks do not establish a new live model residency or
full ingestion acceptance. The earlier ten-source, zero-failure 16K result is
preserved as recovery evidence; it is not the target configuration. No
accepted Vault data or checkpoint files are changed by the Part 1 code/tests.

Part 2 must run a small real Inbox batch and confirm the admitted model and
worker both report 8,192, accepted outcomes and checkpoints remain valid, and
embeddings are handed off successfully. Cloud routing and complete Ariadne
restoration have their own later acceptance steps.
