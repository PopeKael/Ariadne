# Ariadne

Project Ariadne is the combined local-first system for a trusted AI
Librarian: persistent knowledge, identity, retrieval, MCP access, and the
local control-plane interface that presents and operates those capabilities.

## Repository guide

- [`00_System/`](00_System/README.md) — Ariadne's operational framework, scripts, configuration, and system documentation.
- [`control-plane/`](control-plane/README.md) — the local browser interface and Windows tray companion.
- [`docs/`](docs/README.md) — project design, standards, and roadmaps.
- [`docs/CLONE-AND-DEPLOY.md`](docs/CLONE-AND-DEPLOY.md) — the plain-language
  starting guide for somebody building their own local Ariadne.
- [`docs/NETWORKING-AND-HTTPS.md`](docs/NETWORKING-AND-HTTPS.md) — local HTTP,
  private HTTPS and independent deployment choices, with current portability gaps.
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
$env:ARIADNE_ALLOW_UNSUPERVISED_CORE = '1'
py -3 .\control-plane\server.py
```

On Windows, the normal user-facing startup is the Rust resident host from
the Start menu. The Python core refuses an ordinary direct launch unless the
explicit development override above is set; this prevents a headless backend
from being mistaken for a running Ariadne session.

The simple local deployment contract is `http://localhost:8765`, loopback-only,
with no HTTPS infrastructure required. The current checkout still redirects
browser pages to an installation-specific HTTPS origin; see the
[networking guide's portability limitation](docs/NETWORKING-AND-HTTPS.md#start-local-add-a-network-edge-when-needed)
before treating this as a working generic first run. No networking changes are
made by that guide.

Choose your own Vault using Configuration or `ARIADNE_VAULT_ROOT`. The current
default is an installation-specific path, not the repository root. Do not copy
Wazza's private Vault or network configuration to make a clone work.

## License and acknowledgements

The repository's source is distributed under the [Apache License 2.0](LICENSE). Keep any applicable third-party notices and licenses with their components; private vault contents are not part of the published source.

## Help and community

Please [open an Issue](https://github.com/PopeKael/Ariadne/issues) for a reproducible bug or a practical improvement idea. Include your Windows version, relevant component, setup steps and the behaviour observed. Do not publish personal vault content, credentials or private network details.

Part of [Garage Alchemy](https://garage.dia.net.au/): useful tools, practical experiments, and the lessons learned while building them.

**Support is optional:** [Buy Me a Coffee](https://buymeacoffee.com/warrengerdes) · [Join the Tribe on Patreon](https://www.patreon.com/c/chanyawazza). No payment is required to use or contribute to this project under its licence.
