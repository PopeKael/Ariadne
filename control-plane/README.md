# Ariadne local control plane

This is Ariadne's local browser interface and Windows tray companion. It is
part of the same repository as the Knowledge Vault; the Vault system files are
in `../00_System/` and the private Markdown knowledge folders remain excluded
from Git.

- Address: `http://localhost:8765`
- Host: Windows
- Runtime: Python standard library, plus Pillow and pystray for the tray companion
- Network exposure: loopback only; it is not published to the LAN
- Current capabilities: drives, WSL registrations, Docker container metadata
- Tray companion: open, restart, or exit Ariadne without a console window
- Open WebUI launch: starts Docker Desktop when needed, opens the local UI, and
  preloads `gpt-oss:20b` into Ollama memory with a five-minute keep-alive.

The active Ollama model store is `F:\AI\Ollama`. The previous C: store is kept
as `C:\Users\Warren\.ollama\models.rollback-20260817` until the new location
has had normal use. The dashboard launch behaviour can be adjusted with
`ARIADNE_OPEN_WEBUI_URL`, `ARIADNE_OPEN_WEBUI_CONTAINER`,
`ARIADNE_CHAT_MODEL`, and `ARIADNE_OLLAMA_PRELOAD_KEEP_ALIVE`.

Ariadne treats the browser page as the workload boundary. Closing the last
active page session, or losing its heartbeat, cancels its local jobs, unloads
all Ollama models, and stops managed WSL/rendering workloads. Keep the Ariadne
page open when a deliberate background task must continue.

Reference architecture and the public/private boundary are documented in
`docs/`. The Vault root can be overridden temporarily with
`ARIADNE_VAULT_ROOT`; by default it is the repository root.

For somebody deploying their own copy, start with
[`../docs/CLONE-AND-DEPLOY.md`](../docs/CLONE-AND-DEPLOY.md). The tray
dependencies are listed in [`requirements.txt`](requirements.txt).

Run it from PowerShell:

```powershell
py -3 .\control-plane\server.py
```

Then open `http://localhost:8765` in a browser.

