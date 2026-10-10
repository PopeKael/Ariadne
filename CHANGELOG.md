# Changelog

All notable repository-level changes are recorded here. Entries describe changes to the version-controlled Ariadne framework; they do not record private knowledge-store content.

## [Unreleased]

### Changed

- Separated The Lab's compact model context from its full saved trajectory. Reads now support source ranges, literal search and long-line continuation; failed patches report nearby committed source. Successful edits retire old source/browser observations while retaining the original task, current revision, concise history and current diagnostics. No agent-loop or execution-limit changes.
- Removed normal local Ollama run/call wall-clock cutoffs. Valid output refreshes a generous idle-only emergency watchdog; removed the separate legacy prompt-byte cap while retaining context-token, output, step, repair and no-progress protections. Timing continues to be recorded.
- Prevented unavailable Lab patch actions before a current file read; increased the bounded total run allowance to 20 minutes for measured local-model latency and corrected total-deadline reporting. Per-call, repair and step limits remain enforced.
- Fixed premature Lab context stops by reusing measured provider input counts for unchanged conversation prefixes; exact-patch failures now report bounded actual source lines to support correction. Added regressions for both failures.
- Refactored The Lab into a bounded model/action/observation coding-agent loop, using mini-SWE-agent and Aider as design references. Removed mandatory semantic planner/reviewer stages; added read, create, exact patch, run, sequential browser test and finish actions with full trajectory and provider timing/token receipts.
- Added a separate Windows-host-owned full-screen Lab Runner with confirmed foreground focus and explicit launch/close/error reporting; Workbench remains the development preview.
- Persisted Lab results in normal Chat with Save to Inbox and saved-build reopening. Added visible elapsed/output/heartbeat activity, candidate diagnostics and interrupted-run recovery.
- Verified a live four-call counter build and real-browser interactions; recorded bounded-failure and two-patch repair fixtures separately from live-model evidence. Updated About and the checkpoint/validation logs on 2026-10-09.
- Added The Lab inside Chat with a dedicated local coding model, minimum 32K context, immediate GPU model loading, and Workbench Preview / Code / Metrics for saved single-file browser builds.
- Added build-stream heartbeats and saved-result recovery after interrupted connections; documented runtime verification and remaining preview isolation and model-output quality limits.

- Made daily Inbox ingestion resilient to repeated conversation exports: the newest readable snapshot wins, older conflicting snapshots are archived in `Archive/Duplicates/`, and each resolution is recorded in the run manifest and `deduplication-report.json`.
- Documented the policy for generated local runtime data and the embedding-index rebuild workflow.
- Added a local HTML control menu for routine and maintenance KnowledgeVault workflows.
- Added stable line-anchored, structured citations and display-ready citation text to knowledge retrieval results.
- Added Ariadne Tools v1 foundation with registry-driven temporary Document Analysis for Markdown and text attachments, bounded chunk retrieval, front-matter metadata, and chat-scoped cleanup.
- Fixed temporary Markdown attachments with block-style YAML lists such as author and tags; malformed list handling no longer terminates the upload request.
- Added Ariadne Planner v1: configurable local semantic routing with strict Ollama JSON Schema output, authoritative runtime context, planner telemetry/residency checks, validated execution constraints, and deterministic fallback.
- Hardened planner applicability so attachment tools are not offered without attachments, and residency telemetry now distinguishes cold model loads from warm resident requests using before/after `/api/ps` checks.
- Promoted `qwen3.5:9b-q4_K_M` as the default resident Semantic Interpreter after the v2 frozen 60-case bakeoff; smaller tested models did not reach 54/60 final routes.

## 2026-07-14

### Changed

- Excluded `00_System/Data/embedding-index.json` from Git. The local semantic-search index is generated from the vault and can be rebuilt with `00_System/Build-Embeddings.ps1 -Rebuild`.

### Removed

- Purged historical copies of the generated embedding index from repository history.
