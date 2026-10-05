# IPv4 model connections — 5 October 2026

The public Ariadne address remains `https://ariadne.dia.net.au`. Both the Windows host dashboard URL and the control-plane public origin already use this hostname. The HTTPS gateway's private core and Ollama origins already use `127.0.0.1`.

Knowledge Vault ingest failed because `localhost:11434` reached the IPv6 listener containing only `nomic-embed-text:latest`. Read-only catalogue requests confirmed that `127.0.0.1:11434` exposes `gpt-oss:20b` and the other desktop models. The exact event that changed which listener an earlier run reached has not been established.

The separate KnowledgeVault repository now defaults enrichment preflight and chat to `http://127.0.0.1:11434`, retaining the required GPT-OSS model and an explicit endpoint override. Its report is `Reports/ingest-ollama-endpoint-fix-2026-10-05.md`.

This Ariadne change replaces remaining local Ollama defaults in inference routing, startup catalogue checks, Signal's optional local provider, and the planner benchmark. Desktop routing also converts saved or environment-supplied Ollama loopback hosts (`localhost` and `::1`) to IPv4 when loading them. Named public and remote provider URLs are preserved. The saved desktop configuration was verified to load all three local model providers with IPv4 endpoints.

Validation: nine desktop inference/runtime tests, five Signal inference tests, and twenty KnowledgeVault adapter/ingest tests passed. Runtime restart tests now inject the supervisor lookup as well as the listener and restart callbacks, so tests do not inspect or control real Ollama processes. A live read-only KnowledgeVault model preflight passed at the IPv4 endpoint.

These are local source changes. They do not disable IPv6 in Windows, restart services, perform model inference, or deploy the Signal source change to Hera. The already-running public gateway and core Ollama route use IPv4; the new desktop defaults and saved-route conversion apply when that source is next loaded. No full vault rebuild was started.

## Accepted checkpoint

On 5 October 2026 Warren ran Process Inbox and confirmed that ingestion completed successfully. He then requested a checkpoint, GitHub synchronization and updated project notes.

The Ariadne checkpoint contains the four model-endpoint source changes, desktop and Signal regression tests, isolated runtime tests and this handover. The separate KnowledgeVault checkpoint contains its shared enrichment adapter, actual-endpoint ingest logging, regression tests and acceptance report. Generated Vault data and unrelated Vault changes are excluded. The public Home address was also verified with an HTTPS 200 response at `https://ariadne.dia.net.au/home`.
