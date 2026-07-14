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

## Control menu

Run `Start-AriadneControl.ps1` to open the local HTML control menu. It separates normal vault operations from maintenance routines and launches each selected workflow in a separate PowerShell window.

The launcher accepts requests only from its own loopback page and exposes a fixed allow-list of scripts. It is deliberately not a general PowerShell command runner.

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
