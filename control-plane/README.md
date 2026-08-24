# Ariadne local control plane

This is Ariadne's local browser interface and Windows tray companion. It is
part of the same repository as the Knowledge Vault; the Vault system files are
in `../00_System/` and the private Markdown knowledge folders remain excluded
from Git.

- Address: `http://localhost:8765`
- Host: Windows
- Runtime: Python standard library, plus Pillow and pystray for the tray companion
- Network exposure: loopback only; it is not published to the LAN
- Current capabilities: Ariadne Home, local Qwen chat, cited Vault retrieval, temporary Document Analysis for .md/.txt attachments, episodic activity, drives, WSL registrations, and Docker container metadata
- Tray companion: open, restart, or exit Ariadne without a console window
- Open WebUI launch: starts Docker Desktop when needed, opens the local UI, and
  preloads `gpt-oss:20b` into Ollama memory with a five-minute keep-alive.

The active Ollama model store is `F:\AI\Models\Ollama`. The previous C: store is kept
as `C:\Users\Warren\.ollama\models.rollback-20260817` until the new location
has had normal use. The dashboard launch behaviour can be adjusted with
`ARIADNE_OPEN_WEBUI_URL`, `ARIADNE_OPEN_WEBUI_CONTAINER`,
`ARIADNE_CHAT_MODEL`, and `ARIADNE_OLLAMA_PRELOAD_KEEP_ALIVE`.

Ariadne treats the browser page as the workload boundary. Closing the last
active page session, or losing its heartbeat, cancels its local jobs, unloads
all Ollama models, and stops managed WSL/rendering workloads. Keep the Ariadne
page open when a deliberate background task must continue.

Reference architecture and the public/private boundary are documented in
`docs/`. The Vault root can be overridden temporarily with
`ARIADNE_VAULT_ROOT`; by default it is the configured live store
`D:\Downloads\KnowledgeVault`. The Ariadne repository is application code,
not an implicit Vault root.

For somebody deploying their own copy, start with
[`../docs/CLONE-AND-DEPLOY.md`](../docs/CLONE-AND-DEPLOY.md). The tray
dependencies are listed in [`requirements.txt`](requirements.txt).

Run it from PowerShell:

```powershell
py -3 .\control-plane\server.py
```

Then open `http://localhost:8765` in a browser.

## Ariadne Tools v1

The Home composer exposes a registry-driven Tools palette. The first tool is
Document Analysis, which accepts .md and .txt attachments as temporary working
context. Markdown front matter is preserved as metadata, and larger documents
are split with the same heading-aware chunking used by Vault retrieval.
Temporary attachment workspaces live under the ignored
control-plane/runtime/document_contexts/ path, keyed by chat id; they are
removed when the chat is archived or purged and are never automatically
promoted to the Knowledge Vault.

The Home response diagnostics report the attachment handling mode, retrieved
temporary chunks, and context contribution. Web research, PDF, DOCX, image,
spreadsheet, OCR, browser automation, code execution, source comparison, and
audio tools remain deferred.

## Ariadne Planner v1

Home now sends each request through a small semantic planner before the existing
controller branches run. The planner returns a strict JSON-Schema decision with
`intent`, `primary_source`, registered `tools`, `use_vault`,
`needs_current_information`, `use_heavy_model`, bounded `tasks`, and
`confidence`. The controller validates the decision, applies explicit
`vault_mode` and user-selected tools as hard constraints, and then executes
Document Analysis, Vault retrieval, or normal conversation. The planner never
answers the user and never executes a tool directly.

The planner receives only compact runtime context: the authoritative local date,
time, timezone, recent conversation messages, attachment metadata, active Vault
mode, registered tools, and configured model roles. Attachment content remains
with Document Analysis; full Vault content is never sent to the planner.

Configuration defaults are:

- `ARIADNE_PLANNER_MODEL=qwen3:0.6b`
- `ARIADNE_PLANNER_KEEP_ALIVE=-1` (request persistent Ollama residency)
- `ARIADNE_PLANNER_NUM_CTX=4096`
- `ARIADNE_PLANNER_NUM_PREDICT=256`

Planner telemetry is stored with the Home turn and includes model, planning and
load duration, approximate prompt/output token counts, whether a load occurred,
and `/api/ps` residency evidence. If Ollama is unavailable, the model returns
malformed JSON, or a plan selects an unavailable tool, Home logs the failure and
uses the existing deterministic Vault/document fallback. External research is
not currently registered; a planner request for current reporting is therefore
reported as unsupported rather than being presented as completed research.
