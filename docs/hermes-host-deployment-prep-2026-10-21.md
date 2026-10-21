# Hermes Host lease-cleanup deployment preparation

Date: 2026-10-21

## Candidate and current checkout

- The candidate was built in an isolated Cargo target directory. It has not
  replaced or been launched instead of the current resident Host.
- Candidate:
  `.host-build-msvc/hermes-lease-ownership/release/ariadne-host.exe`
- Candidate SHA-256:
  `23098a03e4ab65679d02c8e7560c7f9b4a69fdefdf12f3fa9b60206d72ee4a4b`
- Checkout's canonical Host executable, expected deployment target:
  `control-plane/host/target-msvc/release/ariadne-host.exe`
- Its pre-deployment SHA-256 is
  `16e394cca315e36acce8005bf58c434c4e32306e05ec3cb552a454174575adac`.
- Verified rollback copy:
  `.host-build-msvc/hermes-lease-ownership/rollback/ariadne-host.before-hermes-lease-ownership.exe`
  with the same SHA-256 as the canonical pre-deployment executable.
- The local Core readiness endpoint returned `{"ok":true}` during preparation.
  Process inventory did not provide a trustworthy active Host executable path
  in this sandbox; confirm the running Host path before replacing any file.
- No Host, Core, or Ollama process was stopped or restarted, and no model
  residency was changed.

## Controlled activation procedure

Do this only after explicit authorization. Keep Home, Vault ingestion, coding,
renderer and other GPU work idle for the full supervisor handoff. The Host's
lease table is in memory: orderly Host shutdown stops its owned Ollama child
and its Job Object, which releases model runners and closes workload-owner
handles. On new startup, lease state begins empty and the Host performs its
normal Ollama identity, runner and WDDM checks. Do not force-kill the Host or
delete/release a lease through an alternate path.

1. Confirm the active Host executable path and verify it is the canonical target
   above. Record the active Host PID and the ready state of
   `http://127.0.0.1:8765/api/core/ready` and `/api/status`.
2. Use the resident Host tray menu's **Exit Ariadne** action. This is the
   existing orderly shutdown path; it stops the Core, Ollama child and managed
   MCP services before the Host exits.
3. Confirm the Host process has exited and both local Core readiness and the
   Host-owned Ollama endpoint are down. If either stays up, stop here and
   diagnose ownership; do not overwrite the executable or terminate an unknown
   listener. Check the WDDM inventory and confirm no Ariadne model runner is
   still resident.
4. Copy the canonical executable to a timestamped backup outside Cargo output.
   Verify its SHA-256 equals the recorded pre-deployment hash above. Copy the
   candidate to a temporary filename in the canonical executable's directory,
   verify candidate SHA-256, then replace the canonical file with the temporary
   file. Keep the backup until activation and acceptance are complete.
5. Start Ariadne through the normal `control-plane/start-ariadne.ps1` path (or
   the existing Start Menu shortcut). Confirm `/api/core/ready` returns 200 and
   `ok: true`, `/api/status` is healthy, the tray/avatar is present, and the
   Host log records a new Host start and Ollama ownership. Compare the running
   executable hash with the candidate hash.
6. Only after those checks pass, verify Host diagnostics show no orphaned GPU
   leases, test a configured model acquire/release, and run the explicitly
   pilot-guard-permitted Home → Hermes → Qwen Code task. Require correlated
   receipts for file creation, code execution/verification, and Hermes accepting
   the final coding result. Then assess the configured 14B model only after the
   preceding task has released its model leases and the WDDM snapshot confirms
   clean competing residency; admission must retain the 1 GiB reserve.

## Recovery / rollback

If the updated Host fails either health check or the acceptance path:

1. Use the new Host's tray **Exit Ariadne** action and confirm its orderly exit.
   Do not use `taskkill /F` against a live supervisor.
2. Restore the verified rollback copy above to the canonical path (or use the
   timestamped pre-deployment copy created during promotion) and verify its hash is exactly
   `16e394cca315e36acce8005bf58c434c4e32306e05ec3cb552a454174575adac`.
3. Launch through `control-plane/start-ariadne.ps1` and repeat Core readiness,
   `/api/status`, tray/avatar and Ollama ownership checks. If rollback does not
   restore readiness, leave the Host/Core state visible and diagnose from the
   Host and startup logs; do not start a second supervisor or substitute a
   different model/provider.
4. Preserve the candidate, backup, failed-run receipts, Host log and startup
   traces for diagnosis. Remove the backup only after the restored or updated
   Host has been accepted.

## Deployment record — 2026-10-21

Warren authorized the controlled Host deployment and restart. The deployment
was performed through the normal supervisor lifecycle:

- The resident canonical Host (PID 7904) received the existing **Exit Ariadne**
  tray command. Host and Core exited cleanly; the Host log recorded
  `IPC receiver stopped` and `clean shutdown`, and both Core and Ollama endpoints
  were down before replacement.
- The candidate replaced the canonical executable atomically. The running
  canonical binary hash is
  `23098a03e4ab65679d02c8e7560c7f9b4a69fdefdf12f3fa9b60206d72ee4a4b`.
- The timestamped atomic rollback copy is
  `.host-build-msvc/hermes-lease-ownership/rollback/ariadne-host.atomic-backup-20261021-222101.exe`
  with the pre-deployment hash
  `16e394cca315e36acce8005bf58c434c4e32306e05ec3cb552a454174575adac`.
- Ariadne restarted through `control-plane/start-ariadne.ps1`. The resident
  Host is PID 420 and owns Ollama PID 14368. Core readiness returned `ok: true`;
  `/api/status` reports RUN, Rust Host online, Vault available, and all six
  bundled plugins healthy. No production configuration was changed.
- Live lease lifecycle verification passed: an `ai` GPU lease owned by a
  deliberately exiting test process (PID 8520) was reclaimed by the Host within
  three seconds. A subsequent lease was admitted and released. The configured
  Home model was admitted at its 32K profile and released. The configured
  `qwen2.5-coder:14b` 32K profile was first refused when a WDDM sample would have
  left 986 MiB (38 MiB below the required 1 GiB reserve); it was not forced.
  A later retry, after WDDM headroom increased, was admitted under the Rust Host
  lease and served local Ollama requests. The model profiles and aliases were
  unloaded after the turn; final Ollama residency is empty.

### Acceptance result: isolated 9B path accepted; production remains disabled

No production restart or configuration change was made. Hermes remains off in
normal Ariadne. The actual `home_chat_payload` controller ran in an isolated
process with Hermes enabled only in that process, avatar/Home activity IPC
suppressed, and the temporary ChatStore record purged. A temporary copy of the
registry selected the previously verified `ariadne-qwen-code:9b-32k` route for
both Home and coding at the configured 32K context. The normal Home UI, Rust
Host binary and production registry were unchanged.

Verified run `2426acb9951d4c9e905453b4b3b4e3ec`, Home test chat
`901ac04bacb44418b664b65208b73eda`, turn
`6dc3496d8f374787ad62e683b58b8c9c`, Hermes run
`74461706f42f4c889a2b30271946a2b6`:

- Qwen Code made 8 local Ollama `/v1/chat/completions` requests, all HTTP 200,
  using `ariadne-qwen-code:9b-32k`, `context_tokens=32768`, and the separately
  configured client `max_tokens=8192`.
- Qwen Code emitted native tool-use events and completed 3 coding calls. It
  created `pilot_hello.py`, read it back, and executed the exact command
  `"C:\Users\Warren\AppData\Local\Programs\Python\Python312\python.exe" pilot_hello.py`
  in its assigned workspace. The receipt records exit code 0 and stdout
  `Ariadne Hermes pilot OK`. An independent AST check confirmed the file is
  exactly one `print` call with that literal.
- The runner recorded `SUCCESS`, Rust Host admission for the full Qwen Code
  process lifetime, the changed-file receipt and execution receipt. Hermes'
  worker accepted the structured `SUCCESS` result before returning its final
  response. The final response was persisted to the Home chat and names the
  file, command, output and exit code. No legacy fallback or cloud route ran;
  Ariadne ChatStore remained the memory owner and Hermes-managed memory stayed
  disabled.
- During the first 9B attempt, the Host correctly rejected an unverified
  context-qualified alias after its observed resident context was 0. Testing
  the exact pre-verified 9B tag avoided creating another alias. A later guard
  denial exposed that Qwen's shell could not resolve bare `python` even though
  the bridge had resolved Python. The bridge now passes its exact interpreter
  path and the pilot guard permits only that configured interpreter with one
  workspace-contained `.py` file. Rejected paths/commands were not executed.

### Qwen 2.5 Coder 14B tool format

Local `/api/show` reports the 14B model's `tools` capability and a template
that asks for a `<tool_call>...</tool_call>` wrapper. The actual 14B Qwen Code
run `476106dd4ba34423a90f61ff61050b7f` returned a fenced JSON `write_file`
object as assistant text; there were no native tool-use receipts, file writes,
or executions. This matches [Ollama issue #12174](https://github.com/ollama/ollama/issues/12174),
which documents `message.content` containing JSON while `message.tool_calls`
is absent and later reports that the coder model fails to emit the template's
required wrappers. It supports a model/tool-format compatibility diagnosis,
not a conclusion that the coder lacks code-generation quality.

A fresh raw 14B OpenAI-compatible response was not captured: the Rust Host
refused the diagnostic request because no verified full-GPU residency profile
exists for the canonical 14B model at 32K. No inference was sent and no
calibration was forced. The earlier CLI transcript is the observed local
response evidence, but it is not a raw Ollama response body. Therefore reserve
14B for non-tool code generation until the tool-format compatibility is fixed
and validated; keep coding-model selection configurable. The verified local
9B acceptance does not validate the production 14B coding route.

The deployment rollback copies remain available. Keep production Hermes
disabled until the production route itself is intentionally selected and
verified; that activation is not authorized by this isolated acceptance.
