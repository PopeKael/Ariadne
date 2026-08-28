# Ariadne checkpoint — 2026-08-15

## Project position

Ariadne has moved from separate prototypes into one canonical local project.
The working analogy is a persistent operational counterpart: the model may
change, but the four parcels, identity, memory, tools, and continuity remain
outside any one model.

Canonical repository:

`D:\Downloads\Ariadne`

Git remote:

`https://github.com/PopeKael/Ariadne.git`

Latest local implementation before publication:

`572cbfd style: present Ariadne dashboard as localhost`

The working tree was clean when this checkpoint was created.

## Four parcels

1. **Knowledge Vault — memory**: durable Markdown knowledge, identity kernel,
   catalogue, ingestion, graph, embeddings, and retrieval in the repository
   root and `00_System/`.
2. **MCP — nervous system**: local stdio and authenticated HTTP protocol
   boundaries in `00_System/ariadne_mcp.py` and
   `00_System/ariadne_mcp_http.py`.
3. **Toolshed — hands**: Vault workflows and controlled local actions for
   WSL, Docker, Ollama, LM Studio, Wan2GP, and related services.
4. **Workspace/interface — environment**: the browser dashboard, loopback
   server, tray companion, launcher, sessions, and operational controls in
   `control-plane/`.

The full parcel definition is in [FOUR-PARCELS.md](FOUR-PARCELS.md).

## Live state verified

- Desktop shortcut `Ariadne Control.lnk` points to the canonical launcher.
- The old Knowledge Vault shortcut was preserved as
  `Ariadne Control (old KnowledgeVault 8787).lnk`.
- Scheduled task `Ariadne Local Control Plane` points to
  `D:\Downloads\Ariadne\control-plane\start-ariadne.ps1`.
- The active tray process runs from
  `D:\Downloads\Ariadne\control-plane\tray.py`.
- Dashboard: `http://localhost:8765/` returned HTTP 200.
- `/api/status` reported integrated Knowledge Vault controls available.
- Python syntax, JavaScript syntax, and 21 Knowledge Vault regression tests
  passed.

## Publication checkpoint

The integrated local history is ready for GitHub review on branch
`agent/publish-integrated-ariadne`. The repository remains the canonical
project boundary for the four parcels; ignored Vault content and local
runtime state are not part of the public commit.

## Rollback state

The original working trees remain intact:

- `D:\Downloads\KnowledgeVault`
- `D:\Documents\Codex\Ariadne`

The private Vault folders were copied into the canonical repository without
deleting the originals. No GitHub push or destructive cleanup has occurred.

## Deliberately outstanding

- Audit and refresh generated `00_System/library.json` rather than blindly
  importing its machine-specific working-tree changes.
- Reconcile older control-menu documentation with the current control-plane
  interface.
- Define the conversation-layer context contract across the four parcels.
- Keep the old source trees until normal operation in the canonical location
  has been exercised for a reasonable period.
