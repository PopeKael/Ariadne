# 00_System

## Purpose

The **00_System** folder contains the operational framework that powers Project Ariadne.

Unlike the knowledge stored elsewhere in the vault, this folder contains the rules, configuration, documentation, prompts, templates, scripts, and architecture that define **how Ariadne works**.

Think of this folder as the operating system of the KnowledgeVault.

## Contents

Typical contents include:

- System architecture documentation
- AI prompts and system instructions
- Processing workflows
- Automation scripts
- Templates
- Configuration files
- Development notes
- Build documentation
- Project roadmap

## Generated runtime data

`Data/embedding-index.json` is Ariadne's local semantic-search index. It is generated from the vault and intentionally excluded from Git: it is reproducible, machine-local, and may be large.

Check or recreate it with:

```powershell
.\00_System\Build-Embeddings.ps1 -Status
.\00_System\Build-Embeddings.ps1 -Rebuild
```

Do not add the generated index to commits. The scripts, configuration, and source content needed to rebuild it remain version controlled.

Search results carry a structured citation with the document and chunk identity, vault path, heading, Markdown line range, and available original-source metadata. They also include a ready-to-display citation string. A citation can be `complete` (external source present), `vault-only` (valid local note), or `incomplete` with the missing fields listed.

## Control menu

`Start-AriadneControl.ps1` is the loopback-only rebuild-v1 command menu. Its allow-list is limited to daily ingestion, index status/rebuild, retrieval evaluation, rebuild regression tests, and read-only failure audit. The definitive command surface is documented in [Supported Commands](../docs/Supported-Commands.md).

## Ariadne identity and query context

The active behavioural identity is [Ariadne Identity Kernel v1.0.0](../Ariadne%20Identity%20Kernel%20v1.0.0.md). `ariadne_mcp.py` loads only that file's compact runtime section for planner, summariser, and answer calls. Memory and retrieved notes remain separate prompt data and are never used to update identity automatically.

The local Ollama chat request defaults to an 8,192-token context and a 1,024-token output limit. Override these per machine with `ARIADNE_NUM_CTX` and `ARIADNE_NUM_PREDICT`; `ARIADNE_CHAT_MODEL` continues to select the installed chat model. Query results expose the kernel version used so behaviour remains auditable.

## What belongs here?

Anything that defines the behaviour of Ariadne belongs in this folder.

Examples include:

- How documents are ingested
- Folder conventions
- Metadata standards
- Processing pipelines
- Retrieval strategies
- Prompt engineering
- AI orchestration
- Installation and deployment documentation

## What does *not* belong here?

This folder should **not** contain personal knowledge or research.

Examples that belong elsewhere include:

- Wiki articles
- Journal entries
- People
- Sources
- Personal notes
- Projects unrelated to Ariadne itself

## Design Principle

Ariadne separates **knowledge** from **behaviour**.

The KnowledgeVault stores information.

The **00_System** folder defines how that information is organised, processed, searched, and presented.

Keeping these responsibilities separate makes the project easier to understand, maintain, and extend.

---

**In short:**

If the vault is a library, **00_System** is the librarian's handbook.
