# System memory display checkpoint — 10 October 2026

The System page previously polled status independently from the shared header.
Live inspection reproduced different free-VRAM readings on the two surfaces.
Both now consume one shared status snapshot, with overlapping requests coalesced,
five-second polling, and immediate refresh on window focus or tab visibility.

Snapshots older than 30 seconds show refreshing rather than old memory numbers.
Failed requests clear numeric memory readings and retry through the normal poll;
requests have an eight-second timeout. The System page labels whole-machine RAM
and total VRAM explicitly, and displays Ollama-reported model VRAM separately.
The model allocation is not a measurement of the entire inference process.

## Verification

- `node control-plane/test_runtime_memory.cjs`: passed shared-request, stale
  snapshot, HTTP failure/recovery, model allocation, unload and clearing checks.
- `node control-plane/test_vault_heartbeat.cjs`: passed related session checks.
- Node syntax checks for `app.js` and `page-shell.js`, and `git diff --check` passed.
- Verified rendered labels, model allocation and matching header/gauge free VRAM
  on https://ariadne.dia.net.au/system-details; observed subsequent updated values.
- Warren accepted the improved display before requesting this checkpoint.

Browser evidence remains in ignored local runtime storage:
`control-plane/runtime/system-memory-refresh-2026-10-10.png`.
No model selection, running application or backend telemetry calculation changed.
This frontend change required a browser refresh and no core restart.

The checkpoint excludes runtime data and screenshots. Exact local/GitHub commit
parity and clean status are checked after push and reported in the handoff.
