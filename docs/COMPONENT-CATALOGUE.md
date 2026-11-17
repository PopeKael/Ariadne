# Ariadne Engineering Catalogue

Status: pilot (2026-11-17). This is a **navigation map**, not a substitute for verifying live code.

## Purpose

Answer a new developer's first questions without an unguided repository search:

1. What capability implements this user-facing behaviour?
2. Where are the active entry points, adapters, configuration, and tests?
3. What calls what, and where are the data, permission, and safety boundaries?
4. Why was it built this way, how can it be tested, and which changes or decisions explain its history?
5. Which older entry points are superseded or unreachable?

**Scope:** one document per meaningful component/capability, not one document per function. Keep code-level detail in source comments, tests, and version history. Never invent development history or imply a file is active just because it exists.

## Find a feature

1. Start here; follow the component link and the existing README/architecture links.
2. For an optional plugin, inspect its `control-plane/plugins/<id>/plugin.json`. That manifest is the runtime metadata source of truth, not this catalogue.
3. Follow the actual runtime entry point and caller through the implementation. Check for forwarding, dead code, compatibility shims, deployment differences, and external repositories.
4. Check the linked tests and configuration contracts before editing.
5. If any location or status is wrong, correct the documentation in the **same change** as the code. Record uncertainties as `not verified`.

## Component index

| Component | Description and investigation path | Documentation status |
| --- | --- | --- |
| [Cleanup / filing assistant](components/cleanup.md) | `control-plane/plugins/cleanup/plugin.json` → `cleanup.py` → `00_System/Organize-Downloads.ps1`; `control-plane/test_cleanup_plugin.py` | Pilot entry; source inspected 2026-11-17, runtime not executed |
| Knowledge Vault / MCP | [Four parcels](FOUR-PARCELS.md), [MCP](../00_System/KnowledgeVault-MCP.md), [Vault ingestion contract](KNOWLEDGE-VAULT-INGESTION-CONTRACT.md) | Existing architecture docs; component-level audit pending |
| Plugin framework | [Plugin framework](../control-plane/docs/PLUGIN-FRAMEWORK-V0.1.md) and `control-plane/plugin_registry.py` | Existing contract; individual plugins pending |
| Windows host / control plane | [Resident host](../control-plane/docs/RESIDENT-HOST.md), [control-plane README](../control-plane/README.md) | Existing docs; component-level audit pending |
| Discovery / Signal / News | [Discovery and Signal stack](../DISCOVERY-SIGNAL-STACK.md), `discovery-service/`, `signal-service/`, `control-plane/news_service.py` | Component-level audit pending |

Other bundled plugin directories currently present but **not yet audited into this catalogue**: `document-analysis`, `dungeon`, `knowledge-vault`, `qwen-code`, `rabbit-hole`, `signal-service`, `watchlist`. This is a coverage gap, not a claim they are undocumented everywhere.

## Required component entry

Use [the template](components/TEMPLATE.md), and make the shortest complete record that answers:

- **Identity and owner:** capability, architectural parcel, maintained owner/repository, status.
- **Behaviour:** purpose and user-visible entry points.
- **Code map:** exact repo-relative paths, live call chain, external dependencies, obsolete entry points.
- **Contracts:** configuration, API/CLI, data inputs/outputs, persistence, permissions, safety/rollback.
- **Verification:** focused tests, safe local check, expected outcome, environment assumptions.
- **History:** relevant ADR/issue/PR/commit links, important rationale, known migrations; unknowns explicitly marked.
- **Maintenance:** what must be revalidated when the component changes.

Prefer links to existing detailed documentation rather than copying it. The code and tests are the behaviour authority; Git history is the change-history authority; the catalogue is the discovery authority.

## Rollout and enforcement

**Stage 1 (this PR):** establish navigation, template, and the Cleanup example. No runtime or filing changes.

**Stage 2:** inventory all active plugins and core services. Audit actual call paths and link existing docs before adding new ones. Mark legacy, external, and unverified code instead of silently classifying it as current.

**Stage 3:** add a read-only validation tool and CI check that verify each registered component has a readable documentation link, all referenced repo paths exist, and new components cannot arrive undocumented. Start as a coverage report; enforce only once legacy entries are brought into scope. A check of path existence cannot prove a code path is active, so runtime/contract tests remain necessary.

**Stage 4:** update the development/review workflow so every component change updates its code map, acceptance checks, and historical decision links in the same PR. Never auto-generate explanatory claims from filenames.

## Boundaries

- Ariadne source/design documentation lives in the repository; user-owned Knowledge Vault notes are not the authority for the software implementation.
- External Knowledge Vault v2 workflows must be located and documented in their owning repository before asserting which implementation executes.
- Do not expose secrets, personal vault paths, credentials, or private network settings.
- Avoid a giant generated per-file manual that is outdated the moment a file moves. The catalogue should be small enough to trust.
