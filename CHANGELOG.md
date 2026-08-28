# Changelog

All notable repository-level changes are recorded here. Entries describe changes to the version-controlled Ariadne framework; they do not record private knowledge-store content.

## [Unreleased]

### Changed

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
