# Changelog

All notable repository-level changes are recorded here. Entries describe changes to the version-controlled Ariadne framework; they do not record private knowledge-store content.

## [Unreleased]

### Changed

- Documented the policy for generated local runtime data and the embedding-index rebuild workflow.
- Added a local HTML control menu for routine and maintenance KnowledgeVault workflows.
- Added stable line-anchored, structured citations and display-ready citation text to knowledge retrieval results.

## 2026-07-14

### Changed

- Excluded `00_System/Data/embedding-index.json` from Git. The local semantic-search index is generated from the vault and can be rebuilt with `00_System/Build-Embeddings.ps1 -Rebuild`.

### Removed

- Purged historical copies of the generated embedding index from repository history.
