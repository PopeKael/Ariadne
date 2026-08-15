# Engineering Standards

**Project:** Ariadne
**Purpose:** Defines the engineering principles used throughout the Ariadne project. These standards ensure the project remains maintainable, predictable, and reproducible as it evolves.

---

# Core Principles

## 1. Complete Artifacts

Always provide complete files.

Do not provide patches, partial snippets, or instructions to manually edit sections of code unless specifically requested.

Every version should be replaceable by copying a complete file into place.

---

## 2. One Feature Per Version

Each version introduces one logical capability.

Examples:

- v0.6 – Process Inbox and archive processed files.
- v0.7 – Structured review output.
- v0.8 – Knowledge Map suggestions.

Small, incremental improvements are preferred over large changes.

---

## 3. Test Before Commit

Every new feature must be tested before committing.

A version is not considered complete until it has successfully executed its intended workflow.

---

## 4. Commit Every Working Version

Every successful version is committed to Git.

Commit messages should clearly describe the version and the capability introduced.

Example:

Ariadne v0.7 - Structured review output

---

## 5. Configuration Over Code

Configuration belongs in configuration files whenever practical.

Avoid hard-coded values once a feature has stabilised.

Examples include:

- Model selection
- API endpoints
- Folder locations
- Runtime options

---

## 6. Protect User Data

User knowledge is never modified automatically.

Ariadne proposes changes.

The user approves changes.

Only approved actions modify the Knowledge Vault.

---

## 7. Preserve Source Material

Original documents are never destroyed.

Every ingestion preserves the original document until the user explicitly removes it.

Generated files may be recreated.

Original knowledge cannot.

---

## 8. Human Approval

Ariadne is advisory.

Important structural changes require human approval.

Examples include:

- Creating new Knowledge Areas
- Renaming topics
- Merging concepts
- Deleting information

---

## 9. Deterministic Output

Where practical, Ariadne should produce consistent, structured output.

Structured output is preferred over conversational responses for automated workflows.

---

## 10. Clear Separation of Responsibilities

The project consists of distinct components.

- Ariadne is the engine.
- The Knowledge Vault is the data.
- Prompts define behaviour.
- Configuration defines environment.
- Documentation defines process.

Each component has a single responsibility.

---

# Development Workflow

Develop

↓

Test

↓

Review

↓

Commit

↓

Push

↓

Tag Release

---

# Repository Policy

Git stores:

- Ariadne source code
- Prompts
- Knowledge maps
- Documentation
- Configuration
- Engineering standards

Git does not store:

- Personal knowledge
- Generated review files
- Generated runtime indexes, including `00_System/Data/embedding-index.json`
- Temporary working files
- User-specific workspace layout

Generated artifacts must have a documented rebuild path. For the local semantic-search index, use `00_System/Build-Embeddings.ps1 -Rebuild`.

---

# Engineering Philosophy

Prefer simplicity over cleverness.

Prefer explicit behaviour over hidden behaviour.

Prefer reproducibility over convenience.

Prefer small iterations over large rewrites.

Build systems that remain understandable six months from now.

---

*"Good engineering is making tomorrow easier than today."*
