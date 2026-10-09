# The Lab V4 validation — 2026-10-09

The source comparison was performed before refactoring: [reference comparison](lab-agent-reference-comparison.md). The final core implements one query/execute/observation loop. [Architecture and limits](lab-agent-architecture.md).

## Live selected-model acceptance

Run `aba078ff738d403c94ff66197736c505`, saved chat `117b6b96cf654833a10b1fb8066e88a2`. Worker `qwen2.5-coder:14b`, context 32768, native Ollama chat. Original request: create an offline dark-background counter with heading, initial zero, Add and Reset, both usable repeatedly. No test-specific branch in production.

Model-selected trajectory: **write → run → test → finish**. Four model calls, zero repairs. The model selected four browser assertions in one ordinary test script: initial 0, Add → 1, Add again → 2, Reset → 0. All passed. Final READY/VERIFIED was saved and imported into normal Chat.

| Measurement | Actual |
|---|---:|
| Model calls | 4 |
| Input tokens, summed across calls | 5989 |
| Output tokens, summed across calls | 959 |
| Model time | 73.594 s |
| Tool time | 2.844 s |
| Wall time | 76.500 s |
| Repairs | 0 |

Token totals are reported provider counts, not character estimates or simultaneous context occupancy. [Per-call timings, actions and browser evidence](lab-agent-v4-live-trace.json).

Manual Opera Preview also confirmed 0 → 1 → 2 → 0 with zero runtime errors. The conversation was saved through the existing Workbench Save to Inbox action: `Inbox/2026-10-09_1546_The Lab Agent Counter_117b6b96.md`. Workbench dimensions were unchanged. Runner launch code was unchanged; its regressions passed rather than launching an unnecessary full-screen session.

## Repair and bounded-failure acceptance

The isolated fixture used scripted model actions but the real source compiler, nonce transport, sandboxed browser and actual application execution. Its 10-action trajectory was write, run, read, patch, run, read, patch, run, test, finish. It caught the deliberate apostrophe syntax error, then the missing handler ReferenceError, and reached READY only after two preserved patches and three passing sequential browser assertions (0 → 1 → 3 → 0). Wall time: 3.797 s. This is environment/controller evidence, not live-model repair intelligence. [Receipt](lab-agent-v4-fixture-trace.json).

A deliberate non-working control remained NEEDS ATTENTION and retained candidates and diagnostics. The final real-browser failure fixture reached the conservative CONTEXT_BUDGET stop before another model action; unit regressions separately exercise repair, step, format, no-progress, context and absolute time limits. [Failure receipt](lab-agent-v4-failure-fixture-trace.json).

Earlier live trials are retained in the other V4 trace files. They failed safely while exposing inadequate diagnostics and an inherited browser-test lifecycle mismatch: the model wrote sequential assertions, while the old harness reloaded between each assertion. The final environment removes that per-assertion reset loop. A test action starts one fresh page; assertions within it share state. Separate test actions start separate cases. No extra planner/reviewer call or task-specific validator was added to resolve this.

## Regression checks

**74 Python tests passed**, covering controller action choice/observations, error recovery, fresh-file/revision checks, exact patches and Windows line endings, immutable request snapshots, provider metrics, nonce/report safety, shared test-script configuration, failed/stale execution gates, step/repair/context/time/no-progress limits, interrupted-run recovery, existing model preparation, Runner state/focus protocol and canonical conversation saving.

Command, from `control-plane`: `python -m unittest test_lab_agent test_code_lab test_lab_verification test_lab_runner test_home_chat_store test_lab_model_preparation -q` (5.723 s in the final run).

`node --check` passed for code-lab.js and lab-verification.js. Python compilation and `git diff --check` passed. The 2026-10-09 checkpoint combines this refactor with the reviewed Lab Runner, progress and conversation-saving infrastructure. See [checkpoint record](checkpoint-2026-10-09-lab-agent.md) for scope and publication verification.

These results establish a working conventional loop and a live simple interactive application. They do not establish exhaustive correctness for arbitrary generated applications; choosing meaningful tests and interpreting observations remain model responsibilities. Context preflight remains deliberately conservative and reports its stop explicitly; full trajectory/raw evidence is retained.
