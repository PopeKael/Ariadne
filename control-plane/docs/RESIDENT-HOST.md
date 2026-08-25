# Ariadne Resident Host migration

## Responsibilities

`ariadne-host.exe` is the small Windows resident process. It owns the
system-tray icon and menu, the per-user single-instance mutex, Python-core
startup/shutdown/restart, the desktop avatar overlay, and the local named-pipe
receiver. It does not reason about queries, access the Vault, call Ollama, or
replace Ariadne's HTTP server.

The Python Core remains `control-plane/server.py`. It owns Ariadne Home, the
HTTP API, retrieval, Vault access, routing, models, tools, and workload
lifecycle. Home emits optional presentation hints through `avatar_events.py`;
transport failure is deliberately ignored by the core.

## Build and manual run

From the repository root, with Rust installed:

```powershell
cargo build --release --manifest-path .\control-plane\host\Cargo.toml
Start-Process .\control-plane\host\target\release\ariadne-host.exe -WorkingDirectory (Get-Location)
```

The host resolves Python in this order: `ARIADNE_PYTHON`, the repository
`.venv`, the conventional per-user Python 3.12 location, then `PATH`. It
launches `server.py` with `pythonw.exe` when available, so normal startup has
no console window. Set `ARIADNE_PYTHON` when a different interpreter is
required.

## IPC protocol v1

Pipe: `\\.\pipe\ariadne-control`

Messages are UTF-8, newline-delimited JSON. The host ignores malformed,
unknown-version, and unknown-state messages:

```json
{"v":1,"type":"state","state":"thinking"}
{"v":1,"type":"say","text":"Checking the Vault."}
{"v":1,"type":"show"}
{"v":1,"type":"hide"}
{"v":1,"type":"move","x":1600,"y":700}
{"v":1,"type":"reload_avatar"}
```

Canonical states are: `idle`, `listening`, `thinking`, `searching_vault`,
`reading`, `cross_referencing`, `loading_model`, `working`, `speaking`,
`waiting`, `success`, `warning`, `confused`, `recovering`, `error`, and
`offline`.

Python callers should use `avatar_events.emit_state()` or the other helpers;
they must not open the pipe directly in request code.

## Avatar assets

The renderer reads `assets/avatar/avatar_states.json`, then resolves the
filename for the semantic state. The current manifest names sixteen PNG
files, but the renderer boundary is filename-based rather than extension-
based. Missing or invalid assets are logged and do not terminate the host.
The transparent, borderless, topmost overlay has no taskbar button; close is
treated as hide. Its last position is stored under `%LOCALAPPDATA%\Ariadne`.

An Avatar Pack is a directory containing `avatar_states.json` and the files
named by its `states` object. The selected directory is stored in
`%LOCALAPPDATA%\Ariadne\configuration.json` under `avatar.enabled` and
`avatar.asset_directory`. The `/configuration/avatar` page validates all
sixteen canonical Avatar States, shows available thumbnails, sends Preview
events, and can open the selected folder. `reload_avatar` makes the running
host reread the file without restarting Python. Disabling the avatar hides
only the overlay; the host, tray, Python core, Home, and IPC remain active.
Missing or invalid assets are logged once per state and fall back to `idle`;
if that is also unavailable, the overlay is hidden without terminating the
host. Manifest paths must remain inside the selected pack.

## Startup migration

The normal startup entry is a shortcut to `ariadne-host.exe` in the current
user's `shell:startup` folder:

```powershell
.\control-plane\install-startup.ps1
```

The installer refuses to install while the legacy Scheduled Task
`Ariadne Local Control Plane` exists. After confirming the host manually:

```powershell
.\control-plane\install-startup.ps1 -RemoveLegacyTask
```

This requires no administrator rights. Remove the normal entry with:

```powershell
.\control-plane\remove-startup.ps1
```

Add `-RemoveLegacyTask` only when the old task should also be removed. The
scripts report an existing legacy task rather than silently creating duplicate
startup paths.

## Verification

1. Build the release executable.
2. Run one host and confirm the tray icon appears; a second host exits because
   of the `Local\AriadneHost` mutex.
3. Confirm `http://127.0.0.1:8765/` and `/api/status` work.
4. Use the tray menu to open Home, hide/show the avatar, restart the core, and
   exit. The host should remain alive when Python is unavailable or crashes.
5. Open `/configuration/avatar`, validate all sixteen states, save a test
   pack, Preview a state, switch to Disabled, then re-enable it and confirm
   the running host reloads the setting.
6. With a supplied test asset, send a `thinking` event from Python and confirm
   the visible pose changes. Delete that asset and confirm the idle fallback
   and bounded host log entry.
7. Inspect `%LOCALAPPDATA%\Ariadne\host.log`; it records lifecycle and
   transport diagnostics, not query contents.

## Rollback

The old Python tray is retained in `control-plane/tray.py`. During migration,
run:

```powershell
.\control-plane\start-ariadne.ps1 -LegacyPythonTray -OpenBrowser
```

Remove the Startup shortcut first if it is installed. Do not run both resident
paths at once; they are separate supervisors even though the old tray mutex
prevents two old trays from coexisting.

## Current limitations

The supplied artwork is intentionally absent. The Stage 1 renderer supports
static transparent PNGs only; animated WebP/APNG is reserved for a later
renderer implementation. Rust release build and Windows visual smoke tests
require a Rust toolchain and a desktop session.
