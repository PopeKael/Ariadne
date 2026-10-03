# Conversation orchestration: first bounded slice

Base: `b3225bb706f374ca46c1630d4ef2531c7c5741d0`. Implemented 2026-10-02; the following records the original implementation handoff before the 2026-10-03 checkpoint.

## Architecture

The existing semantic planner and deterministic evidence router remain the only intent mechanisms. Explicit current/web research reads current attachments or Focus, searches web, optionally retrieves personal/planner-required Vault context, then generates. Generic verification still retains its existing local precedence. Local only, Conversation only, forced Vault, personal-fact checks, verified Hera TLDR, compact source query construction, provider relevance validation and zero-source citation guards remain in place. Current project research can combine live evidence with personal context without being mistaken for verification of a personal act.

Personality percentages live inside the existing saved Personality / Voice profile, under `personality.intensity`: factual 10, normal 60, creative 80, diagnostics 25. Four settings sliders save through the existing configuration API. Values are bounded to 0–100; missing/invalid values use defaults. The existing planner intent and simple task words select a mode; there is no extra classifier. The existing personality remains authoritative; a compact directive scales its expression at the final generation boundary only. Qwen 3.5 9B, residency, inference parameters and context/output budgets are unchanged.

Chat reveals a Focus button after selection wholly within a completed assistant response. The normal composer supplies the follow-up instruction. Both chat endpoints accept `focus: {text, source_turn_id}` alongside the existing `message`. The server validates selection against a completed response in the same durable chat (normalizing Markdown punctuation/whitespace), with an 8,000-character limit. Focus is included in planning and evidence queries; web queries reuse `compact_source_query`. Generation orders Focus, new instruction, supplied evidence, then recent conversation. A focused request retains at most two recent exchanges and discards older history from that request, without modifying the saved conversation or making a summarization call. Clear Focus removes the anchor; successful submission consumes it; errors retain it for retry.

`timing.orchestration` records route; web/Vault use and skip reason; Focus state/source; personality mode/percentage; character counts and approximate tokens (characters/4) for system, Focus, instruction, attachments, Vault, web, recent conversation, older context, adaptive profile, world state and total prompt. It also records `before_generation_ms` and `generation_controls_ms`. These are returned to Chat, persisted with the completed turn, and journaled as an orchestration event. Section estimates exclude some labels/separators and are not an additive tokenizer audit. The total measures the assembled messages. Verification-blocked requests record generation skipped and elapsed orchestration time; no generation prompt is built for them.

## Files

- `control-plane/evidence_router.py`: bounded web-first evidence policy.
- `control-plane/server.py`: ordering, Focus request integration, existing generation boundary and telemetry.
- `control-plane/conversation_orchestration.py`: deterministic mode/intensity, Focus validation and context preparation helpers.
- `control-plane/ariadne_config.py`, `configuration.html`, `configuration.js`: persisted intensity settings and sliders.
- `control-plane/home.js`, `chat.css`: selection action, anchor preview and clear/submit lifecycle.
- `control-plane/test_evidence_first.py`, `test_home_source_routing.py`, `test_conversation_orchestration.py`, `test_selection_focus.cjs`: updated web-first expectation and new regression coverage.

## Validation

100 Python tests passed across conversation orchestration, source routing, evidence policy, Home generation/documents, configuration page/defaults, durable chat store/HTTP persistence, provider relevance, semantic planner, Signal integration and model-residency tracing. After final telemetry and UI adjustments, 33 affected tests passed; the final web/Vault prompt adjustment passed all 11 source-routing tests. Nine Node tests passed, including selection activation/clear/cross-response bounds and existing settings state, discovery ordering and popover geometry. Both modified JavaScript files pass `node --check`; `git diff --check` passes.

An isolated localhost browser preview visibly verified selecting response text, revealing Focus, activating it, entering a follow-up, clearing Focus, the four default slider percentages and percentage updates. The preview used the actual Chat functions and slider markup, without a live server restart or model call. Server behavior was exercised through temporary test stores and HTTP tests; live Qwen output/personality calibration remains unverified.

The additional broader run was not wholly green: three cleanup configuration persistence tests fail because their fixtures omit `music`; these also fail in an archive of the base checkpoint. The old Home presentation assertion expecting `collapse.hidden = !meaningful` also fails at the checkpoint. A combined run additionally found an activity-state assertion expecting idle while shared state was complete; later comparison reproduced this combined-suite failure at both the checkpoint and current tree, while isolated runs passed. No new regression was attributable to this slice. These unrelated fixtures/state assumptions were left untouched.

## Measurements

Thirty samples per route, using the existing source-routing test harness with mocked planner/search/model and temporary real chat persistence:

| Request | Pre-generation median / maximum | Generation controls median / maximum |
| --- | --- | --- |
| Current web research | 14.634 / 19.797 ms | 0.0205 / 0.043 ms |
| Focused web follow-up | 15.611 / 19.069 ms | 0.023 / 0.063 ms |

Pre-generation includes controller setup, persistence and evidence assembly; generation controls measure final message preparation and context sizing. These are local mock-boundary observations, not real provider latency, model latency or a before/after speedup claim. Live turn telemetry is available for subsequent measurements after an authorized runtime refresh.

## Deferred

No second-pass search, multi-pass research, adaptive large-source research, LLM classification/summarization calls, personality prompt duplication, broad refactor or unrelated UI work. Token counts are approximate; percentage semantics need real-output calibration. Live integration acceptance and production overhead measurement await a runtime refresh. No commit, push or Ariadne restart was performed.
