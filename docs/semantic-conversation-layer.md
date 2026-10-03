# Semantic conversation layer

Implemented 3 October 2026, extending checkpoint `1cfcf9f61aa211b0a9ba91ce0306c787905a0d15`. No application version change.

## Existing path and ownership

Home and Chat share `home_chat_payload` in `control-plane/server.py`. The browser's submitted history is not authoritative: `ChatStore` supplies recoverable chronological history. The existing `librarian_harness` makes one semantic interpretation call, then resolves deterministic capability/routing policy. `evidence_router` enforces evidence requirements; the controller executes attachment, web and optional Vault retrieval. `_home_model_chat` uses the existing model adapter, identity and personality. World State remains a derived SELF/NOW routing projection, not answer evidence.

This change extends that path. Workspace owns session continuity and assembly; MCP carries retrieval and generation requests; Vault owns durable knowledge and identity; Toolshed owns execution. The answer model owns none of those stores. There is no new agent framework, provider, dependency, editor, search pass, or knowledge migration.

## Turn contract and session state

The existing single interpreter response now includes a bounded `turn` object: conversational acts, subject, current objective, response objective and up to three proposed changes. Changes carry exact latest-user quotes, a provenance category and references to known state/assistant paragraphs. The interpreter's output allowance increases from 256 to a minimum of 768 tokens to accommodate this contract; model, context, temperature, seed, generation budgets and residency settings are unchanged. Older interpretation objects remain accepted through an explicit compatibility path.

`conversation_orchestration.py` validates the contract. `ChatStore.begin_turn` assigns IDs, validates quotes against the actual user instruction, resolves supersession and writes the new session state atomically with the submitted turn, before answer generation. Only verbatim user spans enter state. Assistant claims are labelled inference candidates and never become established facts. Uncertainties, user claims, constraints and decisions remain separate. Malformed encoded spans cannot become state facts. Input wording is otherwise preserved.

State is bounded to 16 active items, 32 supersession records and eight correction subjects; generation selects at most eight relevant items. Planner input receives a smaller six-item projection plus six short assistant candidates. State stays in the existing private HomeSessions JSON, follows session retention, and survives reopening or changing models. It does not update identity, World State, Markdown, indexes, or authoritative Vault knowledge. The raw transcript is preserved.

Supersession uses stable assistant paragraph hashes and user-state IDs. For a grounded acknowledge/update act without exact supersession references, the controller conservatively quarantines recent assistant claims and prior state for the same subject. A correction subject also suppresses assistant history on later related follow-ups, preventing a bad interpretation from returning. This fallback is visible in the recipe. It deliberately trades some assistant nuance for safety; it is not a semantic proof that every quarantined paragraph was false.

## Decisions and compiled context

Generic factual, mathematical, coding and architecture turns do not automatically search the Vault. The former policy floor accidentally matched ordinary `what`/`where` questions; it now requires an actual history reference. Known personal/project context, explicit chronology, forced Vault and Local-only verification retain their relevant existing behaviour. A grounded current-conversation update can skip history even if the interpreter unnecessarily requests it; explicit recall and forced Vault remain respected.

Web-first current research, attachment retrieval, verified Hera TLDR, model routing, personality controls and Focus behaviour remain in place. Subsequent streaming fixes forward provisional chunks during generation; the existing final source guard still checks the persisted answer, and the UI replaces provisional text with that final answer. Evidence failures that skip generation still return the existing fixed failure response.

The interpreter's bounded turn contract now also contains `search_query`, a compact phrase for the actual research task. It resolves conversational references using the existing Focus, attachment metadata and recent history in the same interpretation call. The controller supplies it through the existing compact query builder, preserving the task terms and existing 200-character/30-word bounds. Older or failed interpretations retain the prior Focus/article/original-request fallback. The actual query and its origin are recorded in the orchestration recipe. The relevance gate now requires a meaningful title/subject overlap as well as the existing snippet coverage, preventing incidental words in unrelated biographies from establishing relevance. A compact generation directive requires citations, separates old event dates from the current date and distinguishes general evidence from undisclosed facts about the specific subject. No additional interpretation or research pass is introduced; broader comparative context weighting remains deferred.

The current instruction, grounded state, response objective and relevant evidence are supplied deliberately. Recent generation history is capped at eight messages, 2,000 characters per message and 8,000 characters total. Superseded material is removed from that generation view, not from chronological storage. Focus keeps its priority and smaller history window. Identity and existing source-specific instructions remain authoritative; a short objective directive is added only at final generation. Subject matching is conservative token overlap, not an additional model call. Evidence keeps its existing source budgets; token estimates remain characters/4 rather than an exact tokenizer measurement.

## Context recipe and failure diagnosis

Each generation records a recipe before calling the model, then records completion or failure. It includes turn/model IDs, raw semantic act/objective versus compiled act/objective, subject, state-change IDs, correction/supersession, history request/reason, actual Vault/web use and reasons, attachments, actual retrieval tools, requested capabilities/gaps, reasoning tier, confidence/ambiguity, policy/fallback paths, stable evidence IDs, major context sizes and pre-generation/receipt timings. Quotes and complete prompts are not duplicated into diagnostic events. The grounded spans remain inspectable in the existing session record.

Prepared receipts survive interrupted model calls. Missing evidence is recorded as blocked generation; malformed final responses and client transport failures are distinguished from completed reasoning. Interpreter failures are recorded separately, with the existing fallback retained. A completed recipe does not certify factual correctness or model compliance.

Inspect a turn at:

```text
GET /api/core/context-recipe?chat_id=<chat-id>&turn_id=<turn-id>
```

The same receipt is returned as `timing.context_recipe`, persisted on the assistant message as `context_recipe`, and appended to the existing `control-plane/runtime/librarian-events.jsonl` as `CONTEXT_RECIPE`. Invalid chat IDs return 400; unknown turns/older turns without a recipe return 404. No new UI was added.

## Files and verification

- `control-plane/librarian_harness.py`: additive single-call interpretation contract and current-conversation policy.
- `control-plane/conversation_orchestration.py`: state validation, supersession, bounded history, objective compilation and privacy-safe recipes.
- `control-plane/home_chat_store.py`: atomic session-state update and persistent per-turn receipts.
- `control-plane/evidence_router.py`: narrower automatic Vault authorisation.
- `control-plane/server.py`: integration into all existing Home/Chat generation branches and diagnostic endpoint.
- `control-plane/search_providers.py`: bounded interpreted-query support and title/subject relevance check.
- `control-plane/test_search_provider_relevance.py`: query bounds, task preservation and incidental-biography rejection.
- `control-plane/test_home_source_routing.py`: exact spoken insurance follow-up through policy, query, source acceptance and generation.
- `control-plane/test_home_streaming.cjs`: progressive draft rendering and checked final-answer replacement using the existing Chat handler.
- `control-plane/test_turn_assembly.py`: synthetic A–F, grounding, continuity, task objectives, malformed output, persistence and HTTP regressions.
- `control-plane/evaluate_turn_assembly.py`: opt-in real local-model evaluation with temporary synthetic chats and synthetic history retrieval.
- `docs/semantic-conversation-evaluation.md`: six-case live evaluation showing interpreted turn, accepted state, history/evidence decisions, objective, recipe and generated answer.

From `control-plane`, run:

```text
python -m unittest test_turn_assembly test_librarian_harness test_conversation_orchestration test_home_source_routing test_search_provider_relevance test_evidence_first test_home_generation test_home_documents test_home_chat_store test_home_server_persistence test_semantic_planner test_core_interactions test_home_model_preload test_model_residency_trace test_startup_telemetry
node --test test_home_streaming.cjs test_selection_focus.cjs test_startup_telemetry.cjs
python evaluate_turn_assembly.py --live --output ../docs/semantic-conversation-evaluation.md
```

The opt-in evaluation performs model inference; ordinary regression tests use deterministic synthetic adapters. It does not restart services, inspect personal history, or write synthetic facts into the user's Vault. Live semantic interpretation is sampled, not an exhaustive proof of robustness. Broad historical-suite failures documented in the preceding orchestration slice remain outside this task.

## Validation results, 3 October 2026

The relevant Python regression suite passed all 108 tests in 71.083 seconds. Selection Focus and startup telemetry passed all four Node tests. `git diff --check` passed. The six synthetic live cases used the existing Qwen interpreter and generation adapter: all generated complete answers; only explicit chronology used the synthetic Vault fixture. The evaluation report records the actual interpreted state, policy and generated answer for each case.

Across those six cases, pre-generation orchestration took 5.5–12.6 seconds, including the existing semantic model call. Receipt persistence took 8.9–11.0 milliseconds. These are measured durations, not a before/after overhead comparison. The correction case retained uncertainty correctly, although the final model added an unsupported "pending their confirmation" framing; the recipe makes this remaining model-compliance issue distinguishable from state assembly.

After the tests, the application was restarted with the tested code. An actual Home chat request asking for a one-sentence hash-table definition completed using `qwen3.5:9b-q4_K_M`, without web or Vault retrieval. The diagnostic endpoint returned its persisted completed recipe: chat `3c904426089e4747bab40e6ca128ae2c`, turn `341eb0c2ee27401da6b30431f39fe689`. Pre-generation timing was 5,132.764 milliseconds and receipt persistence was 60.109 milliseconds. The interpreter labelled this turn clarification, but the compiled answer objective and routing were correct. The user chose backend-only acceptance; browser rendering was not verified. No commit or push was performed.

## Remaining boundaries

Exact quote provenance does not prove a user's claim true; subject labels and semantic categories can still be misinterpreted. Long-range subject matching and conservative history omission need further evaluation. State is scoped to recoverable chats, not a permanent belief store. No semantic contradiction scan over arbitrary paraphrased retrieved documents, multi-pass research, agent delegation, external/browser escalation or specialist-model integration is implemented. Existing provider policy remains the execution authority.

## Conversational search follow-up validation

The subsequent insurance follow-up fix adds a bounded interpreted query to the same semantic call, records its origin, checks title/topic and named-entity relevance, and gives the latest web research question priority over attached article summary instructions. Final relevant validation passed 122 Python tests in 69.241 seconds and five Node tests. The [conversational search verification report](conversational-search-verification.md) records actual queries, sources, streaming timings, acceptance and remaining model limitations. No additional research pass, semantic call or model change was introduced.
