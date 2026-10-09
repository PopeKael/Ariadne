# The Lab: browser builds in Chat

The Lab is an explicit experimental/build mode in normal Ariadne Chat. V1's
workflow generates one self-contained browser application. Chat opens in normal
conversation mode; The Lab loads its coding model immediately when enabled and
reloads the conversational model when disabled. A temporary loading dialog shows
the model, parameter count, quantisation, size and configured context. The top-right
model display changes after Ollama confirms residency; runtime dials refresh from
the existing telemetry. Failed loads retain the previous mode and restore its model. It has no
separate page, intent classifier, agents, tools, retrieval or conversation history.
Model Lab remains the controlled benchmark surface.

## Architecture and configuration

`code_lab.py` owns validation, run persistence, metrics and preview instrumentation.
Thin server routes reuse Model Lab's Ollama streaming adapter, `ai_gpu_admission`,
`model_activity`, avatar states and existing GPU/RAM/resident-model telemetry.
`code-lab.js` adds the Chat mode and Preview / Code / Metrics to the Workbench.
The ordinary Home request still follows its existing handler when The Lab is off.

The inference registry's separate `coding` task defaults to the desktop
`ollama-coding` provider. Existing saved provider configurations acquire this
provider without changing their conversational route. Its model is configurable
in the existing inference configuration, with `ARIADNE_CODING_MODEL` taking
precedence. Default: `qwen2.5-coder:14b`. Missing models produce an explicit failure;
there is no pull/download call. Install a model explicitly through the existing
owned model-store workflow before attempting the experiment.

`ARIADNE_CODING_CONTEXT` defaults to 32768 tokens and must be at least 32K.
The configured context is checked against Ollama's reported native model limit
before loading or generation; it is never silently lowered. Output budget is half the context,
capped at 8192 tokens. Temperature is 0 and seed is 42. The exact request, fixed
instructions, schema and user prompt are retained in each run record.

The coding system instructions require the model to identify all functional and
presentation requirements, implement them, then review controls, visible state,
initialization, reachable outcomes and completeness before returning the project.
It must correct omissions internally and return only the existing structured
single-file project. This review is part of the same generation request; it does
not add a separate model call or change the UI, schema or runtime validation.

`ARIADNE_LAB_ROOT` defaults to **F:\AriadneLab**. An optional `code_lab` object in
the existing local configuration can specify `root` and `context_tokens`; environment
variables take precedence. Relative paths and roots overlapping Ariadne source or
the Vault are rejected. There is no fallback directory. Each UUID run directory
contains `run.json` and, for a successful generation, `index.html`. File writes
use a same-directory temporary file and atomic replacement. Validation precedes
project writes. Failures retain a failed run record without a successful project
path. Run IDs and resolved paths are checked against the configured root.

## Contract and preview boundary

Ollama receives a JSON schema via `format`. The object contains `project_name`,
`summary`, `entrypoint`, and a `files` array. V1 requires exactly one file with path
`index.html` and entrypoint `index.html`. Content includes all HTML/CSS/JavaScript;
the server rejects extra keys, other filenames, multiple files, empty/non-HTML
content and content over 2 MB. External resource attributes, nested frames,
plugins, meta refresh and CSS imports are also rejected. The model is instructed to avoid dependencies and
network resources. CSP enforces the runtime resource boundary; validation does
not attempt to prove arbitrary JavaScript correct.

Run loads a preview copy through `/api/code-lab/preview` with a CSP response header
and `sandbox allow-scripts`. The iframe also has `sandbox="allow-scripts"` and a
required CSP. No same-origin, forms, popups, top navigation, downloads or device
permissions are granted. Inline scripts/styles and data images/media work;
connections, external resources, frames, workers, plugins, fonts and form actions
are denied. The bootstrap also disables WebRTC constructors; a `webrtc 'block'`
directive adds protection in browsers that implement it. Required iframe CSP blocks
rendering navigation responses that do not accept the restrictive policy. Preview fails closed in browsers lacking this
capability: use a current Chromium browser, such as Chrome or Edge.

**Network limitation:** iframe CSP is not a network firewall for hostile arbitrary
JavaScript. A self-navigation can send a request before embedded-CSP response
enforcement rejects rendering. Ordinary links/forms are prevented, but this does
not prove zero network contact from all possible generated code. Strict network
isolation is therefore an outstanding safety acceptance item. See the
[W3C embedded-enforcement specification](https://w3c.github.io/webappsec-cspee/).

Only the preview copy receives a bootstrap, before model markup. It reports
DOMContentLoaded, JS errors and unhandled promise rejections; the parent also
reports iframe errors and a 15-second load timeout. Messages must come from the
current frame, opaque origin and current preview token. Source `index.html` remains
exactly as returned by the model. Reset/reload starts a fresh preview and retains
prior preview reports in `run.json`. Runtime reporting is diagnostic, not proof of
application correctness or a defence against browser vulnerabilities.

## Metrics and experiment

Records retain Ollama's authoritative total/load/prefill/generation durations
(nanoseconds), input/output token counts, completion reason and completion flag.
Rates use count divided by duration in seconds; missing/zero durations produce
null rates. Wall time includes Ariadne validation and hardware sampling. Hardware
is sampled after generation using existing collectors, with resident models.
It is not peak VRAM: peak VRAM and GPU utilisation remain null when unavailable.
Preview outcome, error messages and timestamped preview history are persisted.

For the Garage Alchemy test:

1. Start the updated Ariadne control plane and open Chat.
2. Click The Lab; watch its loading dialog, then check the coding model and 32K
   context. Ollama residency is verified before Build becomes available.
3. Describe a simple browser game in ordinary language and click Build.
4. Check READY TO RUN and the exact generated source in Workbench's Code view.
5. Select Preview and click Run. Interact with the game, then test Reset/reload.
6. Inspect Metrics and any runtime errors. Match the run ID to its folder under
   F:\AriadneLab and retain the source and run.json for the comparison.
7. Toggle The Lab off and verify conversational Chat; Model Lab is unchanged.

V1 has no shell, OS execution, package manager, filesystem tools, deployment,
autonomous repair, multi-file projects or cloud coding models. A future workflow
may extend The Lab's capabilities while preserving explicit mode selection.

## Initial fixture verification on 8 October 2026

The updated handler was tested on isolated loopback servers, leaving the running
production control plane untouched. The live owned Ollama catalogue did not contain
`qwen2.5-coder:14b`. The default-model browser run correctly failed with no download
and retained a failed record under F:\AriadneLab.

A clearly labelled UI fixture (no inference, null token/hardware metrics) verified
Workbench Code / Preview / Metrics, interactive score changes, successful load,
JS error and promise-rejection capture, blocked fetch, reset to initial state and
persisted preview history. Its retained evidence is
`F:\AriadneLab\verification\8d141024baa448ee99de03996c66b29c\`.
Temporary verification servers/scripts were stopped/removed after testing.

The focused Code Lab, inference, Model Lab, Home generation and Home source-routing
suites passed 42 tests; JS syntax and `git diff --check` passed. The broader
workspace-shell run found an existing assertion expecting
`http://localhost:8766/` in unchanged `create.html`, which now uses the canonical
HTTPS workspace URL. That unrelated assertion was left untouched.

At this initial stage model installation and activation were still pending; see
the live verification below. Strict network isolation remains limited as described
above. Fixture testing does not establish the coding model's generated-game quality
or performance.

## Eager loading update

The Lab now uses `/api/code-lab/prepare` for a temporary GPU residency switch.
It reuses the existing selection lock, GPU transition state, preload/unload
functions, avatar states and residency trace. It never calls configuration save
or changes Home/planner model routing. The Lab model is preserved by the existing
residency monitor while selected. Returning to Chat unloads the coder and preloads
the ordinary conversational model. Busy renderers, Vault reservations and active
model requests block switching. Load failures attempt to restore prior models with
their previous resident context settings.


## Live eager-loading verification on 8 October 2026

The installed qwen2.5-coder:14b model was verified in F:\AI\Models\Ollama.
Its native maximum is 32,768 tokens. The Lab now defaults to 32,768 tokens,
rejects settings below that minimum and validates the installed model's native
limit before loading or generating.

After an idle canonical host/core restart, the HTTPS Chat interface was tested
against the real local runtime. Clicking The Lab immediately showed a modal
loading splash with model, 14.8B parameters, Q4_K_M quantisation, model size and
32K context. After loading, the header showed qwen2.5-coder:14b, the composer
showed Build, and the context label showed 32K loaded. Ollama /api/ps confirmed
only the coder resident with context_length 32768 and size_vram 15,136,603,503
bytes (the entire reported allocation). The residency trace measured about
16 seconds for the first coder load; no fixed five-to-eight-second promise is
made and the splash remains until readiness is verified.

Clicking The Lab again showed Returning to Chat, unloaded the coder and restored
qwen3.5:9b-q4_K_M with 16,384 context tokens and conversational Ask. Its first
return load took about 11 seconds. The header matched the actual conversational
model. The ordinary model configuration and routing were preserved.

The focused Code Lab, model preparation, inference, Model Lab, Home generation,
source-routing and model preload suites passed 58 tests. JavaScript syntax and
git diff --check passed. No actual generated-game quality benchmark was run in
this switching check. Refresh an already-open Chat tab to receive the new assets.


## Interrupted build delivery and recovery

The first real Dungeon Test run (8e3cabfba2424dd89d7468d4dd115946) succeeded
and persisted index.html and run.json after 100.781 seconds. It generated 2,215
tokens at 23.09 tokens/second. The browser showed GENERATING and a network error,
while the avatar later showed Done. The old stream emitted only state transitions,
leaving roughly 96 seconds without bytes while the model generated; an intermediate
proxy idle timeout is consistent with this evidence but its exact timeout was not
confirmed. The GPU/model and persistence did complete successfully.

The stream now emits five-second heartbeats during silent generation, including
prefill, and sends completion only after saving the run. A saved-result endpoint
retrieves the exact source and metadata without invoking inference. If delivery
breaks after receiving the run ID, Chat polls for that run's saved result; if it
cannot reconcile, it explicitly marks the result unconfirmed and offers Recover
saved build. Workbench also offers recovery of the latest saved run after refresh.
The recovery preserves failure records and validates run paths.

29 focused Lab, model-preparation and HTTPS gateway tests passed, including silent
inference heartbeat and exact-source recovery coverage. The actual saved Dungeon
Test was recovered through the canonical HTTPS Chat UI and Preview reported LOADED
with zero runtime errors. This check did not repeat a full long production build.
The generated game itself places the player at (0,0), also a wall, so movement is
blocked; its model output was preserved rather than silently repaired.


## Lab conversation save repair

The last Dungeon Test run (47391d9684bd49fbb1bbed825c5cdb61) successfully saved
its generated files and loaded Preview, but the Lab submit branch returned before
the normal Chat turn-recording flow. Its prompt and source therefore never reached
Chat history, Inbox save or Markdown export. This was a conversation integration
omission, separate from the Workbench presentation layout.

Completed and recovered results now import their authoritative saved prompt,
summary, model, 32K context, run ID, project path and exact source into the existing
ChatStore through a session-checked endpoint. The import is atomic and idempotent
by run ID within the selected chat, preserving existing dialogue. The Workbench
has its own Save to Inbox button, using the same canonical transcript export and
Inbox save as ordinary Chat. Reopened Chat messages offer Open Lab build to restore
Preview / Code / Metrics. No additional coding page or alternative transcript store
was added. The half-screen preview presentation is still a separate pending review.

Forty focused ChatStore, Lab and model-preparation tests passed, including exact
source in Inbox/export, duplicate recovery and preservation of existing dialogue.

Live HTTPS verification recovered that original run, created The Lab: Dungeon Test
in Recent Chats with one turn, saved it through Workbench Save to Inbox, refreshed
the page, reselected the conversation and reopened its Lab build. The Inbox copy
contains the original prompt, exact generated source and run ID. Repeated recovery
and save retained one turn. The reference chat ID is 5449a0355b974bc1885f8e9cef05eb1f.


## Separate Lab Runner

Workbench remains the development preview. Launch Lab Runner requests a distinct
full-screen session from the Windows resident host over the existing named pipe.
The host constructs only the canonical Runner URL from validated identifiers and
launches installed Microsoft Edge in its supported fullscreen kiosk mode. It does
not open arbitrary executable paths, invoke a shell, or execute generated code on
the operating system. The Runner has no navigation, Chat, Workbench or metrics UI.
Its iframe fills the viewport with the same sandbox, CSP and device restrictions
as the development preview. Preview's network-isolation limitation described above
also applies here; fullscreen does not create an OS sandbox or repair generated
application logic.

The launch remains pending until the host starts Edge and the loaded iframe
reports readiness. Missing host/browser, a failed build, runtime errors and load
timeouts are displayed as failures in Chat. A private launch token correlates the
host and browser acknowledgements. Alt+F4 closes the Runner session. The host
allows one Runner at a time, hides its avatar while it runs and restores the
previously visible overlay on exit. A per-launch Edge profile under
%LOCALAPPDATA%/Ariadne/LabRunner/Sessions/<launch_id> is retained as owned runtime state,
separate from the user's normal browsing profile. No windows security settings,
default browser settings or kiosk account configuration are changed.

The host owns the Edge process tree through a kill-on-close Windows job. Host
exit closes the presentation session; a later launch cannot delegate to a stale
Edge profile. FAILED/CLOSED states cannot be revived by delayed browser callbacks.
Runner progress and errors remain beside the Workbench launch button, independent
of normal Chat activity updates.

Microsoft documents the launch options at
https://learn.microsoft.com/en-us/deployedge/microsoft-edge-configure-kiosk-mode.


Runner verification used saved build 47391d9684bd49fbb1bbed825c5cdb61 through
Chat's Launch Lab Runner button. The actual Windows host launched Edge and the
sandboxed Runner reported READY with viewport 1920x1080 and screen 1920x1080.
Chat displayed its ready message. The session was left open for user inspection;
manual Alt+F4 exit/overlay restoration remains to be accepted by the user.
The browser automation surface cannot capture this standalone native Edge window,
so the fullscreen measurement is runtime telemetry, not screenshot verification.
The exact previously generated game was used and was not repaired by the Runner.

2026-10-09 launch repair: an old Edge kiosk survived a host restart, and the
shared profile allowed later Edge starter processes to delegate to it. The old
owned kiosk was closed. The replacement host binary was installed with a rollback
copy; its hash matched the tested release build. The saved Dungeon Test launched
through Chat's button with a new host-owned Edge PID and READY viewport 1920x1080.
A duplicate click displayed the already-open error in Workbench while Chat
returned to Idle. Exiting the host closed the Runner PID; after restarting, the
same button launched another fresh READY session. 46 focused Python tests,
the Rust identifier test, release compilation, JS syntax and diff checks passed.
Native kiosk screenshot/foreground placement still require manual observation;
the browser automation surface can verify Chat's status and viewport telemetry.

Follow-up acceptance: Warren reported immediate CLOSED launches with no window.
The host was waiting only for the Edge starter, then closing its kill-on-close
job. Lifecycle tracking now waits for the job's entire process tree to end, and
records the resolved Edge/profile paths, starter exit status and remaining process
count. The updated host was installed and Warren confirmed a window appeared
instantly. A controlled process-tree close reported CLOSED, then the next host
launch returned READY at 1920x1080 and stayed open. This confirms visible-window
acceptance for the new launch; the specific earlier starter exit reason was not
captured and should not be inferred from the old generic CLOSED message.
An attempted command-shell descendant regression test produced a Windows
cannot-find-path dialog because of quoting. It was removed; it is not part of
the installed release host. The existing Rust identifier test passes. Acceptance
for the process lifecycle is the actual host close/relaunch check described above.

Foreground acceptance: the host now activates only a visible window belonging
to its Runner process job. It raises the window without resizing or permanently
pinning it, and briefly joins the current foreground and Edge UI input queues
when direct activation is denied. Both queues are detached immediately; retries
stop on success or after ten seconds. Foreground ownership is confirmed with
GetForegroundWindow and reported separately from iframe READY. Workbench waits
for both results and shows an honest fallback if Windows refuses activation.
The installed release returned READY, viewport 1920x1080 and foreground=true;
Warren independently confirmed that it came to the front. 47 focused Python
tests and release compilation passed. Opera remains the default browser; Edge
continues to provide the separate fullscreen Runner.

## Coding agent (pipeline version 4, 2026-10-09)

The model now chooses read, write, patch, run, test and finish actions in one conversation. Ariadne executes safe operations and returns concrete observations. Mandatory acceptance planning, Task Contracts, contract revisions and semantic reviewer stages have been removed. Existing historical builds are retained.

The browser tools compile source, capture runtime errors, inspect visible controls and execute model-selected declarative test scripts in fresh isolated pages, with sequential assertions sharing state. Patches require a read of the current file and exact unique matches. Finish requires current successful execution; failed/stale tests cannot be overridden, and interactive pages require interaction tests.

The Chat toggle, model loading/context, GPU lifecycle, Workbench layout and development preview, full-screen Runner, saves/history and sandbox retain their existing boundaries. No model or dependency was added. See [the current architecture](lab-agent-architecture.md) and [source comparison](lab-agent-reference-comparison.md).

Run focused regressions from control-plane with `python -m unittest test_lab_agent test_code_lab test_lab_verification test_lab_runner test_home_chat_store test_lab_model_preparation -q`. An isolated real-browser repair fixture is available with `python test_lab_verification.py --serve` on localhost:8799, using temporary owned storage and scripted model actions. It tests the environment and controller, not live model intelligence.
