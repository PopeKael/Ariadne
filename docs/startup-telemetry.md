# Startup telemetry, 3 October 2026

Startup behaviour is unchanged. This change observes existing work: it adds no dependency probes, model calls, waits, retries, or startup optimisations. Qwen and its preload/residency settings are unchanged. Existing uncommitted conversation orchestration work was preserved.

## Architecture and records

Each resident-host launch creates `%LOCALAPPDATA%\Ariadne\startup\<run-id>\host.jsonl`. The host passes that run ID to Python, which writes a separate `core-<pid>-<instance-id>.jsonl` in the same directory. Core restarts under a resident host create a new core instance/file. Browser acknowledgements must match the current core instance.

Rust uses `Instant`; Python uses `time.monotonic`; the browser uses `performance.now` since navigation. These clocks have separate origins, explicitly identified by `clock_scope`. Host wall timestamps are Unix milliseconds; Python timestamps are ISO timestamps with timezone; browser acknowledgements include Unix milliseconds. Wall times correlate processes; elapsed durations never subtract wall times. Windows Python monotonic timings can have coarse resolution, so a displayed zero means below clock resolution.

Records are append-only, flushed and closed after each milestone, independent of the truncated frame/avatar host log. Repeated poll/render/preload calls do not append duplicate Python milestones. Host readiness is recorded once per core launch. There is no automatic retention deletion yet. A typical launch creates approximately thirty small core records and six host records. Trace write failures do not stop startup; the status endpoint exposes Python write failures.

Milestones cover host entry, host configuration, Python spawn/entry/imports, configuration/service imports, inference configuration, TLS/gateway configuration, host IPC availability, owned Ollama listener and full catalogue validation, first Hera news and Signal requests/results, initial news/image background attempts, lifecycle worker registration, preload start/final outcome, listening socket, served readiness, browser rendering, and application readiness. A failed first Hera request is recorded separately from a later first success; its recovery duration spans the initial attempt and intervening wait.

`background_jobs_ready` means the workers were registered and their first sync attempts finished; failed syncs produce `degraded`, not a false success. `ui_rendered` follows session/history/attachment rendering; Home also waits for its initial local news snapshot attempt. The browser schedules two animation frames before acknowledging. This establishes a paint opportunity, not a physical compositor/display measurement. The event's server elapsed time is acknowledgement receipt; `browser_wall_time_ms` and `navigation_elapsed_ms` describe the browser's render boundary separately.

`application_ready` requires served core readiness, browser acknowledgement, completed initial background work, and a final preload outcome. Failed preload or initial background work produces `degraded`. It is an observed milestone, never a gate on existing behaviour. Without opening Home or Chat, UI/application readiness intentionally remains absent. Optional weather permission, markets, sidebar history and later diagnostic health/adaptive refreshes are outside this definition.

## Exactly what changed

| File under `control-plane/` | Change |
| --- | --- |
| `startup_telemetry.py` | New thread-safe startup-only recorder, bounded milestone deduplication, readiness accounting, instance validation and read-only snapshot. Disabled when server is imported by tests. |
| `host/src/startup_trace.rs` | New best-effort host recorder and correlation ID, using existing serde_json dependency. |
| `host/src/main.rs` | Capture main-entry clock, host configuration, Python launch and first successful readiness; pass launch ID to Python. |
| `server.py` | Observe import/config/startup/preload/worker boundaries; add GET `/api/startup`, POST `/api/startup/ui-rendered`, and observer asset route. Record fatal main startup errors. |
| `news_briefing_cache.py` | Optional observer for existing validated news request and first background sync outcome. |
| `signal_service_client.py` | Optional observer for existing Signal request/result; only RUN client receives it. |
| `startup-telemetry.js` | New failure-contained browser acknowledgement using live core instance and two animation frames. |
| `home.html`, `chat.html` | Load observer before existing Home code. |
| `home.js` | Acknowledge initial session render and, on Home, local snapshot attempt; no visual changes. |
| `test_startup_telemetry.py` | Nine tests for monotonic durations, persistence, concurrency/deduplication, degradation, stale/invalid UI reports, write failure containment, disabled recording, Hera recovery, actual background callback and HTTP/asset contracts. |
| `test_startup_telemetry.cjs` | Three tests for paint scheduling, request failure containment and both page entry points. |
| `test_home_model_preload.py` | Isolate the existing startup-order fixture from real Ollama process replacement and TLS/gateway startup. Production behaviour is unchanged. |

The normal ignored local host executable was rebuilt. The pre-change executable is retained at `.host-build-msvc/startup-telemetry/previous-ariadne-host.exe`. Documentation: this file. No commit or push was performed at the implementation handoff; the user subsequently authorised a checkpoint and push.

## Verification

After a normal launch, open Home or Chat, then inspect:

```powershell
$startup = Invoke-RestMethod 'http://localhost:8765/api/startup'
$startup.events | Select-Object phase,status,elapsed_ms,duration_ms
$startup.trace_path
$startup.write_error
Get-Content -LiteralPath $startup.trace_path
Get-Content -LiteralPath (Join-Path (Split-Path -Parent $startup.trace_path) 'host.jsonl')
```

Expect a new launch directory, both process traces, successful dependency/preload results or explicit failed/degraded results, `ui_rendered` after opening a page, and finally `application_ready`. Leave the application running: ordinary frame logging must not remove or grow these startup files. Previous launches remain available after restart. If startup stalls, the last start milestone without its corresponding completion identifies the unfinished phase; inspect the existing dependency diagnostics for further detail.

Validation passed: 75 relevant Python tests (startup telemetry, preload, news cache, Signal client, Home news integration, shutdown, Ollama runtime, conversation orchestration, evidence-first and Home source routing); 18 Rust tests, with the final host telemetry adjustment compiled and its focused test rerun; four Node tests (three new plus existing Selection Focus); JavaScript syntax and Git whitespace checks. Two Ollama tests initially errored because sandboxed Windows process inspection was denied; both passed with that access available. The old Cargo release cache contained an unwritable output directory; release build succeeded in the separate ignored `.host-build-msvc/startup-telemetry` directory.

## Live observations

Both launches used the normal resident host and the current live tree. Ariadne and Ollama were stopped before launch one. Before the final restart, model/GPU work was confirmed idle. The shared shutdown endpoint stopped Python and Ollama cleanly; it left the resident tray host running. After confirming no core/model processes remained, the idle host was closed separately and the final tested executable launched. Hera was not restarted or changed.

| Observed phase | First launch | Final restart |
| --- | ---: | ---: |
| Host configuration duration | 1.90 ms | 1.51 ms |
| Python imports/module initialization | 656 ms | 109 ms |
| Python configuration/service import segment | 109 ms | 32 ms |
| TLS/gateway configuration | 47 ms | 16 ms |
| Ollama ownership/full-catalogue validation | 5,062 ms | 3,000 ms |
| Host confirmation of core readiness, since host entry | 6,337 ms | 3,795 ms |
| Hera news first valid response | 16 ms | 1,516 ms |
| Hera Signal first successful response | 172 ms | 172 ms |
| Initial background completion | 188 ms | 1,609 ms |
| Qwen preload, including admission/catalogue/residency verification | 7,219 ms | 8,547 ms |
| Browser initial render acknowledgement boundary, since navigation | 3,897 ms | 6,876 ms |
| Application-ready receipt, since Python entry | 34,625 ms | 12,047 ms |

Final run: `1791012256297944400-16912`, core instance `31292-f429037a546049cc9af422089380daed`. Host wall time 14:24:16.298 Bangkok; application-ready receipt 14:24:28.580, about 12.28 seconds later. Both traces persisted successfully, with no Python write error. The final Home page was visibly populated with 100 cached articles and the selected Qwen model.

The first browser was opened later, so its 34.6-second application acknowledgement must not be called a 34.6-second backend startup. Browser navigation and acknowledgement receipt also differ because transmission/handling can take time. Phases overlap; do not add all durations.

The largest measured waits were Ollama startup/validation and Qwen preload. Hera news varied from 16 ms to 1.52 seconds and remained asynchronous. Neither launch showed Hera failures. These observations do not prove the cause of the unusually slow original boot: its overwritten/missing startup evidence cannot be reconstructed confidently, and these were application restarts after Windows had settled, not a new Windows boot.

Deferred: startup optimisation, Windows boot contention profiling, detailed subphases inside Ollama ownership/catalogue handling, detailed UI network/render breakdown, automatic trace retention, and compositor-level paint measurement. The earlier TLDR streaming and comparative-research diagnoses remain unchanged.
