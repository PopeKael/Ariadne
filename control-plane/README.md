# Ariadne local control plane

This is Ariadne's local browser interface and Windows tray companion. It is
part of the same repository as the Knowledge Vault; the Vault system files are
in `../00_System/` and the private Markdown knowledge folders remain excluded
from Git.

- Address: `http://127.0.0.1:8765`
- Host: Windows
- Runtime: Python standard library, plus Pillow and pystray for the tray companion
- Network exposure: loopback only; it is not published to the LAN
- Current capabilities: drives, WSL registrations, Docker container metadata
- Tray companion: open, restart, or exit Ariadne without a console window

Reference architecture and the public/private boundary are documented in
`docs/`. The Vault root can be overridden temporarily with
`ARIADNE_VAULT_ROOT`; by default it is the repository root.

Run it from PowerShell:

```powershell
py -3 .\control-plane\server.py
```

Then open `http://127.0.0.1:8765` in a browser.
