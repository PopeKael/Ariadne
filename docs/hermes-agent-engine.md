# Ariadne internal agent engine

## Status

The Hermes integration is staged in Ariadne's existing Home/Chat path, but it is **not activated by default**. The live application was not restarted. No cloud route was used.

The current local Home route is `ollama-desktop` / `qwen3.5:9b-q4_K_M` at Ariadne's configured 32,768-token context. Hermes now accepts Ariadne's explicit registry budget without changing the 64K default for other Hermes callers. The context builder reserves output tokens and a 5% safety margin, accounts for the rendered system prompt and tool schemas, and prunes only complete oldest exchanges. A real local Hermes turn reached generation at 32K.

The engine remains disabled by default in normal production. `ARIADNE_HERMES_ENGINE` defaults to `legacy`; `legacy` remains the rollback value. An isolated process using a temporary registry and suppressed avatar/Home activity completed the 9B acceptance below. The current production process has not loaded this source change.

The isolated Hermes checkout's model-context-budget changes are preserved as a patch against its pinned upstream revision in `docs/patches/hermes-context-budget.patch`; the patch is tracked here because that checkout's only remote is upstream Hermes.

## Intended runtime contract

- The existing `home_chat` inference registry route supplies model, provider, endpoint, credentials, context and output budgets. The agent engine has no pinned model, provider, context, or cloud requirement.
- The Home controller still owns identity, prepared conversation state, evidence policy, one-pass search, attachments, citations, durable ChatStore memory, and Rust Host admission.
- Hermes runs one turn in a temporary profile. Hermes memory, soul, context files, trajectory persistence and background review are disabled. Ariadne ChatStore remains the durable interaction-memory owner.
- The Hermes MCP profile retains the five read-only Knowledge Vault tools. The second Ariadne-owned MCP bridge adds `run_coding_task` only for a coding request/selection. It dispatches through Ariadne's existing Qwen Code runner, child-workspace checks and Rust Host leases; it never grants direct shell or filesystem tools to Hermes.
- `get_web_research_evidence` is exposed only when the Home controller has already authorized and retrieved live sources for that turn. It returns those sources and does not initiate another search.
- Generic plugin/Toolshed action dispatch is not exposed yet. Mutating plugin actions require their existing explicit confirmation, and the current Home chat does not pass the plugin session/confirmation boundary into this bridge.
- Successful diagnostics include the selected route, context, MCP receipts and execution path. Engine failure records the bounded reason and `legacy-home-model-adapter` fallback path. Coding failure is surfaced as a failed turn instead of being presented as ordinary prose. A coding turn uses Hermes' supported scripted tool prelude and normal tool executor, so the capability call does not depend on provider-specific `tool_choice` support.

## Verification record — 21 October 2026

- **Context budget:** the real local Home turn reported a 32,768-token window, 4,096 output reserve, 24,371 safe input budget, 5,286 estimated prompt/tool tokens, and zero pruned history messages. The prior Hermes 64K startup floor no longer blocks Ariadne's explicit budget. No model or provider name is embedded in the engine logic.
- **Rust Host:** an admission attempt initially measured 7,678 MiB free against 8,908 MiB required. The existing Host idle-release operation unloaded one idle `qwen3.5:9b-q4_K_M`; Ollama then reported no loaded models. Later Rust Host-admitted local model calls completed while retaining the configured reserve. No Host memory limit was bypassed.
- **MCP/tool path:** Hermes discovered the five read-only Vault tools and the per-turn coding bridge. A real Hermes scripted prelude dispatched `run_coding_task` through the established Toolshed MCP boundary. The bridge used the registry-selected local model and Rust Host lease. There was no legacy response fallback for the authorized coding request.
- **Qwen Code runtime:** the first bridge run found Node missing from the MCP child `PATH`. The bridge now resolves Node 22+ from the configured executable, system `PATH`, or the isolated worker's pinned tools, and passes that directory to Qwen Code. The installed launcher reports Qwen Code `0.25.1`; its real Node runtime is `C:\Program Files\nodejs\node.exe` (24.18.0).
- **Coding acceptance:** the later isolated run `2426acb9951d4c9e905453b4b3b4e3ec` used the temporary registry's configured `ariadne-qwen-code:9b-32k` route at 32K context and separately configured `max_tokens=8192`. Qwen Code made 8 local HTTP 200 requests and completed 3 native tool calls: create `pilot_hello.py`, read it back, and run it with the configured Python 3.12 interpreter. The execution receipt records exit code 0 and `Ariadne Hermes pilot OK`; an independent AST check verified the file. Rust Host admission covered the Qwen Code process lifetime, and Hermes accepted the structured success result before its final Home response. No cloud route or legacy fallback ran. See `docs/hermes-host-deployment-prep-2026-10-21.md` for run, turn and receipt identifiers.
- **14B compatibility:** the observed Qwen 2.5 Coder 14B CLI response was fenced JSON in `message.content`, without native tool-use receipts. The model template requires a `<tool_call>` wrapper. A raw API response was not captured because Rust Host correctly refused an unverified 32K residency profile, so 14B remains unsuitable for tool execution pending a validated format fix; its code-generation quality is a separate capability.
- **Tests:** Ariadne `test_coding_agent` and `test_hermes_pilot`: 19 passed. Hermes repository runner on `tests/agent/test_ollama_num_ctx.py`: 14 passed, including the 32K admission/budget case. Python compilation and `git diff --check` passed. These focused checks supplement, but do not replace, the isolated live acceptance above.
- **Memory and UI:** no Hermes memory was written; test chats were purged. The existing ChatStore remains the only durable interaction-memory owner. No Home/Chat interface or model registry was changed.
- **Activation:** not verified, so not enabled. No production restart occurred.

## Remaining blockers

1. Generic Toolshed/plugin actions still need a turn-scoped bridge that preserves existing plugin session and explicit confirmation rules.
2. The 14B route needs a validated tool-format compatibility fix before it can be selected for coding tool execution; the isolated 9B acceptance does not validate 14B.

The isolated 9B acceptance is complete, but production Hermes remains disabled pending deliberate production configuration and activation authorization. The separate 14B tool-format issue and generic mutating Toolshed integration remain open.

## Rollback

The default already selects the existing path. To force rollback in an environment that explicitly enabled Hermes, set `ARIADNE_HERMES_ENGINE=legacy` in the Rust Host child-process environment and restart Ariadne through its normal supervisor when a restart is authorized. No data migration is involved; ChatStore, Vault, model registry and existing Home interface are unchanged. To remove this source integration, revert the Hermes changes in `control-plane/server.py`, `control-plane/hermes_pilot.py`, `control-plane/hermes_pilot_worker.py`, `control-plane/ariadne_agent_tools.py`, `control-plane/coding_agent.py`, the focused tests, and this document. Keep unrelated `control-plane/test_dungeon_lifecycle (1).py` untouched. The isolated Hermes engine checkout is separately pinned; revert only its context-budget changes if removing the engine. No production restart was performed.
