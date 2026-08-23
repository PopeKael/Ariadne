# Ariadne unified repository layout

Date: 2026-08-15
Status: active migration decision

## Decision

`D:\Downloads\Ariadne` is now the canonical Ariadne repository and is
connected locally to `https://github.com/PopeKael/Ariadne.git`.

The existing Knowledge Vault repository history was merged into this
repository. The local control panel was imported as a separate component:

```text
Ariadne/
├── 00_System/       Knowledge Vault services, retrieval, identity, and MCP
├── Projects/        Ariadne project documentation and history
├── docs/            Repository-wide architecture and standards
├── control-plane/   Browser interface, tray companion, and Vault worker
└── private folders  User-owned Markdown data, excluded from Git
```

The Vault remains the durable knowledge and identity substrate. The control
plane is the interface and local operating surface. Neither is a replacement
for the other.

## Path transition

The control plane now defaults to the repository root as its Vault root. A
temporary compatibility override is available through `ARIADNE_VAULT_ROOT`.
This allows old services or recovery runs to target another local Vault
without embedding that path in source code.

The private Vault folders were copied into the canonical root without deleting
the original `D:\Downloads\KnowledgeVault`. The original Knowledge Vault and
`D:\Documents\Codex\Ariadne` control-panel trees remain rollback sources until
the new installation has been exercised in normal use.

## Deliberately deferred

- No GitHub push was performed.
- The modified generated `00_System/library.json` was not copied blindly; it
  contains machine-specific paths and requires a separate catalogue/path audit.
- Obsidian machine-local changes were not promoted as an architectural change.
- The old source directories must not be deleted until the new control plane,
  MCP paths, startup task, and retrieval workflows have been verified.

## Next architectural step

Define the conversation-layer context contract around the existing pieces:
the active identity kernel, retrieved Vault passages, current turn state,
project state, people/entities, and available tools. Keep model-provider
selection behind an adapter so changing the reasoning engine does not change
Ariadne's identity or memory boundaries.
