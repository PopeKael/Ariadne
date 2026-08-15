# Ariadne

Project Ariadne is an open architecture for a trusted AI Librarian: a persistent companion that understands people, maps the AI ecosystem, and intelligently routes work to the right intelligence at the right time.

## Repository guide

- [`00_System/`](00_System/README.md) — Ariadne's operational framework, scripts, configuration, and system documentation.
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
