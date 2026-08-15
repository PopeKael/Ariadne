# Ariadne

Project Ariadne is the combined local-first system for a trusted AI
Librarian: persistent knowledge, identity, retrieval, MCP access, and the
local control-plane interface that presents and operates those capabilities.

## Repository guide

- [`00_System/`](00_System/README.md) — Ariadne's operational framework, scripts, configuration, and system documentation.
- [`control-plane/`](control-plane/README.md) — the local browser interface and Windows tray companion.
- [`docs/`](docs/README.md) — project design, standards, and roadmaps.
- Knowledge folders — the user-owned Markdown knowledge store, with private content excluded by [`.gitignore`](.gitignore).

## Four architectural parcels

- **Knowledge Vault** — memory and durable evidence.
- **MCP** — the nervous system connecting reasoning engines to information and capabilities.
- **Toolshed** — controlled hands for local actions and services.
- **Workspace/interface** — the environment Ariadne and Wazza operate through.

See [`docs/FOUR-PARCELS.md`](docs/FOUR-PARCELS.md) for ownership boundaries
and [`docs/CHECKPOINT-2026-08-15.md`](docs/CHECKPOINT-2026-08-15.md) for the
current system state. The concise [handover](docs/HANDOVER-2026-08-15.md)
records the consolidation decisions and earlier rabbit holes.

## Local generated data

The local semantic-search index at `00_System/Data/embedding-index.json` is generated from the vault. It is deliberately excluded from Git because it is machine-specific, can grow large, and can be rebuilt.

To check or rebuild it from the repository root:

```powershell
.\00_System\Build-Embeddings.ps1 -Status
.\00_System\Build-Embeddings.ps1 -Rebuild
```

See [CHANGELOG.md](CHANGELOG.md) for notable repository changes.

## Local control plane

Run the interface from the repository root:

```powershell
py -3 .\control-plane\server.py
```

Open `http://localhost:8765`. The control plane uses the repository root as
the default Vault root. Set `ARIADNE_VAULT_ROOT` only for a deliberate
transition or compatibility run against another local Vault location.
