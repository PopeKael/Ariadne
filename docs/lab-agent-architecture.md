# The Lab coding agent — pipeline version 4

## References inspected before editing

See [the direct source comparison](lab-agent-reference-comparison.md). mini-SWE-agent supplies the query/execute/observation pattern; Aider supplies file-based targeted edits and concrete error feedback. No dependencies or unrestricted shell tools from either project were added.

## Removed orchestration

Task Contract extraction and revision, acceptance-planner instructions, implementation plans as required output, mandatory reviewer calls, semantic coverage verifier, and separate architect/editor/reviewer schemas and state transitions are removed. Old V2/V3 runs and JSON traces remain historical records, not evidence for V4.

## Loop

Original user task and generic system instructions initialize a conversation. Each iteration queries the selected model once for a short action rationale and one action, executes that action, appends the actual observation, and saves the trajectory. Repeat until the model selects a valid finish or a deterministic limit stops the run. Invalid actions/edits/test definitions return error observations; there is no additional repair planner. No-op repetitions (ignoring reworded rationales) and three consecutive malformed actions stop safely.

The controller knows only ModelAdapter.query(messages, schema, deadline) and Environment.execute(action, deadline). It does not know HTML, Ollama, model names, contracts, requirements or semantic reviewer roles.

## Browser V1 environment

| Action | Result |
|---|---|
| read | Actual owned index.html, content and SHA-256 revision |
| write | Initial complete single-file offline project; immutable candidate snapshot |
| patch | Unique exact search/replace edits against a previously read current revision; atomic validation and preserved patch/candidate |
| run | Syntax compilation and existing isolated browser startup/smoke execution; errors, visible text and controls |
| test | Model-selected declarative browser actions and expectations; each test action starts fresh; its assertions share state in sequence |
| finish | Summary and final candidate, gated on current successful execution and no failed/stale tests |

Interactive pages require a test action before finish. These checks prove the executed behaviours, not exhaustive satisfaction of every natural-language requirement. The model remains responsible for completeness and choosing useful tests. Controller acceptance cannot certify a game's entire state space.

The old per-assertion browser reload loop was also removed: one test action runs an ordinary sequence of actions/assertions on one fresh page. Another test action creates a separate case.

No autonomous shell, arbitrary paths, eval scripts, network access or extra models. Existing CSP, resource validation, GPU admission, model preparation, 32K coding context, Workbench/Runner, saved builds and chats retain their owners. A truthful browser-check count replaces the obsolete contract-coverage claim in the existing status line; layout is unchanged.

## Provider independence and accounting

The controller receives an injected adapter; the configured provider selects the model. Ollama's adapter sends native chat messages and the generic action schema, owns streaming/cancellation, and reports provider token/timing metrics. The worker name is absent from controller/environment policy. Compatible structured-output models can use the same interface; changing provider requires an adapter, not a new agent loop.

All conversation messages, exact request snapshots, raw model responses, full tool observations, steps, immutable candidates and patches are retained externally. The saved trajectory is an audit, not the next model input. Model context retains the original instructions/request, current workspace revision, concise action history, latest current read and latest current run/test receipts. Browser output sent to the model is bounded; raw evidence stays in run.json. Context preflight stops explicitly before overflow, rather than silently deleting the original task or adding a summarizer model. No extra semantic model calls are hidden.

Defaults: 24 model-action steps and 2 patches; no elapsed run/call cutoff for local Ollama. ARIADNE_LAB_WATCHDOG_SECONDS defaults to 900 seconds of model silence (configurable up to 3600). Existing MAX_STEPS and MAX_REPAIRS settings remain bounded. Legacy WALL_SECONDS, CALL_SECONDS and PROMPT_BYTES are not read. Context preflight reserves up to 8192 output tokens within the selected context. Tokens remain unavailable when the provider did not report them.

First-call generation telemetry is preserved; latest-call telemetry and aggregate totals are separate. Saved request messages are immutable snapshots, not references to a growing conversation. A disconnected browser can recover pending verification; a core restart marks an orphan explicitly and retains source.

## Verification

Regression suite, live-model timing and browser repair receipts are recorded in the accompanying V4 validation report. Earlier timing/contract-gap traces describe the removed V3 architecture.

## 2026-10-09 retry correction

Run `2424c5f57b6c404798fdbb6aa4aa7340` stopped before browser execution. The worker created a scaffold and repeatedly searched for an HTML comment where the file contained a JavaScript comment. Failed patches did not consume repair slots or modify the source. The old byte-as-token guard then rejected 27407 serialized bytes even though the last provider input was 4974 tokens.

Exact edit failures now report match count and bounded closest actual source lines; patches still require current read/revision and unique exact matches. Generic instructions explicitly request a complete initial implementation and verbatim search text.

Context preflight retains the configured absolute byte limit. With a successful previous provider count, it bounds the next input using that measured count plus every byte after the common serialized conversation prefix and a 1024-token framing margin. A changed action schema is charged in full as UTF-8 bytes. Without measurement it retains the UTF-8 byte bound. Full requests remain intact; no character/token ratio or silent truncation is used. The bound is a preflight estimate, not an exact tokenizer result. The actual failed next request replays at 10266 against 24576 available input tokens.

76 focused regressions passed, including exact-mismatch recovery and measured-prefix growth/overflow protection. The replay uses recorded inputs and a scripted transport; it proves the guard correction, not full live-model completion of the original application.

## 2026-10-09 total-deadline correction

Run `17341f7d7ee5463cbd637c450220eaab` created a candidate, ran it, read it, patched it and ran it again. It reached the 480-second total limit during model call 7; the call had only 55 seconds left, rather than the nominal 240 seconds. Context preflight passed. Source/browser startup passed twice, but no interaction test action completed. The candidate still missed original presentation/behaviour requirements; it was correctly not released as verified.

Patch was attempted once before read, wasting 92.391 seconds generating an action that could not execute. The model then read and repeated the patch (186.437 seconds). The environment now omits patch from the per-call action schema until the current file has been read, and removes it again after an edit. Execution still enforces the same revision checks. No planner or semantic stage was added. Generic editing instructions request short exact anchors rather than copying unchanged functions.

Default total time is now bounded at 1200 seconds, with 240 seconds per call and existing configurable ceilings unchanged. This gives a multi-action local-model run room to complete: measured output speed in this run fell from 20.59 to 5.33 tokens/s. That variance is observed, not attributed to a GPU fault. A call interrupted by the total deadline now reports WALL_TIME_LIMIT instead of CALL_TIME_LIMIT. Saved partial candidates remain available. These corrections do not assert that the worker will implement every requested feature; startup is not semantic acceptance.

Live boundary replay used the already-resident selected coding model and the recorded request immediately before its invalid edit. With the revised schema it chose `read`, returned the actual 6426-character file and then gained patch availability. One real model call: 3380 input / 61 output tokens, 51.188 seconds. This verifies the failed edit boundary with the live worker, not completion of the full original game. Empty workspaces now expose only create; existing workspaces cannot offer create again. Final focused suite: 79 tests passed.

## 2026-10-09 local idle-watchdog policy (supersedes timed-run entries above)

Removed total wall-clock and per-call elapsed limits from the local Ollama agent. The query/action/observation loop is unchanged. Context/output, steps, repair and no-progress checks remain. Timing is telemetry, not permission to continue. Legacy WALL_SECONDS/CALL_SECONDS settings no longer limit local runs.

The native HTTP transport waits up to 900 seconds for connection/headers or valid model progress. Parsed content, thinking output or completion refreshes the idle watchdog; empty chunks do not. A socket inactivity timeout and owned watchdog thread close a truly stalled connection with CONNECTION_WATCHDOG. A continuously productive call has no fixed maximum duration. Browser connection evidence uses the same generous emergency allowance; deterministic source checks and individual browser assertion/startup checks retain their existing test semantics. No paid provider or cost policy was added: this Lab adapter supports local Ollama only, and no monetary cap applies to it.

79 focused regressions passed after restart, including real HTTP streaming longer than the watchdog interval, silent/empty-chunk watchdog termination, and a simulated long productive agent operation. The exact saved Dungeon Test prompt was submitted through Chat in a fresh conversation; prompt SHA256 9d2f9ad4f7cf27cfb385bf983600030c359efeee44a9e3cbca2f0bc622df8ad5. Runtime record captures the final result separately.

The first watchdog-policy rerun (`6dd12297a6474592bebed3875f1f8c29`) completed a productive model call lasting 455.672 seconds and reached 780.938 seconds total without a time stop. It exposed the separate legacy 48000-byte prompt ceiling: 52435 bytes, estimated token bound 19233, available input allowance 24576. That ceiling is now removed; context preflight remains token-based with provider-prefix measurements and conservative additions. Regression coverage accepts a measured request above 48000 bytes while still rejecting a token-bound overflow. [First rerun receipt](lab-local-watchdog-first-rerun.json).

Final exact-prompt rerun `c34586535a5f4f908573d4cb62220cf2`, Chat `328448c4869d4bd490a77144a4dfbfde`: 7 calls, 805.531 seconds wall, no elapsed cutoff. Total reported provider counts: 44297 input / 5755 output across calls (not simultaneous context occupancy). The longest completed call exceeded the former 240-second limit. No monetary cap applied. It stopped NEEDS ATTENTION on conservative CONTEXT_BUDGET after repeated editing/read observations: 65577 serialized bytes, estimated input bound 26365 against 24576 reserved input capacity. The last actual reported input was 12198 tokens; the preflight estimate does not prove the 32768-token window was actually full. Source remained syntactically broken and no final browser verification was reached. The requested timeout policy is validated; the original application is not complete. [Final receipt](lab-local-watchdog-final-rerun.json).

## 2026-10-09 external workspace context

The controller still performs one model action, one execution and one observation per iteration. Its next input is now a deterministic working context separate from the complete saved trajectory. No extra model calls, planners, semantic checks, execution limits or GUI changes were added. Model adapters, context/output protections and the configured context size are unchanged.

`read` accepts 1-based `start_line`/`end_line`, literal `search` and character `offset` within the selected range. A range defaults to the next 80 lines, returns up to 6000 source characters, and reports total lines, truncation and a continuation offset. Search reports match count and up to three nearby 2000-character snippets. Locations are separate from exact source text; CRLF is preserved. These are observation sizes, not new execution budgets. Reads still verify the actual owned file and revision before enabling patches.

Failed exact patches report nearby committed source at duplicate matches or closest matching fragments. Atomic multi-edit failures never report an uncommitted intermediate file as the current source. Original failures and complete action bodies remain in the audit. Subsequent model inputs retain action identity/rationale and concise outcomes rather than repeated write/patch bodies. Each new read replaces the prior read; each run/test replaces the prior receipt of that kind. Successful edits retire every previous source/browser receipt. Instructions, the original request, current revision and concise action history remain throughout.

The deterministic multi-repair fixture uses a generic 300-section HTML page, three failed exact matches and three successful repairs, followed by successful startup/finish. It compares the former append-only/full-read policy with the new working context using identical scripted actions and browser observations. Serialized message bytes (excluding action schema): peak **226767 → 13552**, final **226767 → 12759** across 17 scripted requests. These are byte measurements, not tokenizer counts or live-model performance claims. Full candidates and audit entries remain available. [Per-request receipt](lab-context-growth-fixture.json); regression fixture: `control-plane/test_lab_context.py`.

The focused suite passes 84 tests. No Ollama request or Dungeon Test rerun was performed for this context-only change.

## 2026-10-10 live workshop verification and context-quality correction

Restarted the owned idle core to load compact context, reran the 84 focused tests successfully, and submitted the exact saved Dungeon prompt once through Chat. Prompt SHA256 remained `9d2f9ad4f7cf27cfb385bf983600030c359efeee44a9e3cbca2f0bc622df8ad5`. Run `ddd645bf2a3a458389e3e741e584577f` stopped at the existing two-patch repair limit after 372.844 seconds and nine calls: 17804 input / 2849 output tokens summed across calls. Maximum actual input was 2733 tokens and maximum serialized request was 13336 bytes. There was no context overflow or elapsed cutoff.

The model corrected a compiler-reported apostrophe using a targeted five-line read and exact patch. That candidate passed browser startup. It then misdiagnosed movement after reading only a function header, inserted a new function body before the existing body, reintroduced an apostrophe syntax error, and attempted another similar patch without executing the changed source. The third patch was refused by the unchanged repair limit. Final source is broken. Initial source also displayed the full grid, trapped the starting position in walls, and left combat as a placeholder. No complete dungeon or successful interaction-test claim is warranted. [Live receipt](lab-context-live-rerun.json).

This exposed a context-quality issue beyond byte growth. Search snippets now extend forward into matching code (up to the same 2000-character per-match observation size), with exact continuation offsets. Generic instructions explain that a truncated/header-only read does not establish function behaviour. Successful patch history now includes bounded actual change diffs, rather than only a pre-edit rationale; full bodies remain external. No workflow stages, execution limits, GUI changes, model changes or Dungeon-specific rules were added.

Updated scripted three-repair fixture: peak message bytes **227029 → 15636**, final **227029 → 13468**; sizes include useful longer search snippets and concise edit diffs. The receipt supersedes the earlier fixture measurements above. The final focused suite passes **85 tests**. These follow-up changes were loaded by another idle-core restart; no second live Dungeon run was launched. The live result proves one compiler/read/patch/startup cycle and bounded context, not reliable application completion.
