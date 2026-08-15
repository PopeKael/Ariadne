# Ariadne

Project Ariadne is the combined local-first system for a trusted AI
Librarian: persistent knowledge, identity, retrieval, MCP access, and the
local control-plane interface that presents and operates those capabilities.

## Repository guide

- [`00_System/`](00_System/README.md) — Ariadne's operational framework, scripts, configuration, and system documentation.
- [`control-plane/`](control-plane/README.md) — the local browser interface and Windows tray companion.
- [`docs/`](docs/README.md) — project design, standards, and roadmaps.
- Knowledge folders — the user-owned Markdown knowledge store, with private content excluded by [`.gitignore`](.gitignore).

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

Open `http://127.0.0.1:8765`. The control plane uses the repository root as
the default Vault root. Set `ARIADNE_VAULT_ROOT` only for a deliberate
transition or compatibility run against another local Vault location.
