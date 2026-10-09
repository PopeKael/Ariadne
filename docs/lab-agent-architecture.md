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

All conversation messages, exact request snapshots, raw model responses, full tool observations, steps, immutable candidates and patches are retained. Browser output sent to the model is bounded; raw evidence stays in run.json. Context preflight stops explicitly before overflow, rather than silently deleting the original task or adding a summarizer model. No extra semantic model calls are hidden.

Defaults: 24 model-action steps, 2 patches, 480 seconds total, 240 seconds per model call. Existing ARIADNE_LAB_MAX_STEPS, MAX_REPAIRS, WALL_SECONDS, CALL_SECONDS and PROMPT_BYTES settings remain bounded. The effective conservative input-byte budget also reserves 8192 output tokens within the selected context. Tokens remain unavailable when the provider did not report them.

First-call generation telemetry is preserved; latest-call telemetry and aggregate totals are separate. Saved request messages are immutable snapshots, not references to a growing conversation. A disconnected browser can recover pending verification; a core restart marks an orphan explicitly and retains source.

## Verification

Regression suite, live-model timing and browser repair receipts are recorded in the accompanying V4 validation report. Earlier timing/contract-gap traces describe the removed V3 architecture.
