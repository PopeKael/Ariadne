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
{"v":1,"type":"clear_status"}
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
The setup page can temporarily test each canonical state or run the full
sequence; `clear_status` removes only the temporary status bubble and leaves
the current Avatar State unchanged.
Missing or invalid assets are logged once per state and fall back to `idle`;
if that is also unavailable, the overlay is hidden without terminating the
host. Manifest paths must remain inside the selected pack.

## Startup migration

The normal user-facing entry is `Ariadne.lnk` in the current user's Start Menu
Programs folder. The separate startup entry is `Ariadne Host.lnk` in the
current user's `shell:startup` folder. Both target the release
`ariadne-host.exe` and use `host/assets/branding/ariadne.ico`:

```powershell
.\control-plane\install-startup.ps1
```

The installer audits and removes duplicate Ariadne-named shortcuts from the
user/common Start Menu and Startup roots before creating exactly those two
entries. It also refuses to install while the legacy Scheduled Task exists
unless `-RemoveLegacyTask` is supplied.

After confirming the host manually, remove the legacy task during migration:

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
3. Confirm `http://localhost:8765/` and `/api/status` work.
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


## Lab Runner ownership

The host accepts `launch_lab_runner` with two lowercase 32-character hexadecimal
identifiers: run_id and launch_id. It constructs the canonical HTTPS Runner URL,
launches installed Edge with fullscreen kiosk flags in a dedicated local profile,
and acknowledges LAUNCHED / FAILED / CLOSED to the core loopback callback.
The core validates saved successful projects before asking the host to launch;
the Runner wrapper separately reports READY after the sandboxed build loads.
Only one presentation session may run at once. Alt+F4 closes the session and the
host restores the avatar if it was visible before launch. Preview stays in Chat's
unchanged Workbench pane. Each launch has a separate profile under
%LOCALAPPDATA%/Ariadne/LabRunner/Sessions/<launch_id>, retained as owned runtime
state for session diagnostics and never committed. A kill-on-close Windows job
owns Edge and its descendants so host shutdown cannot leave an orphan kiosk.
Separate profiles prevent Chromium from delegating a new launch to an older
session. Workbench displays launch progress and errors next to its launch button.
Session completion follows the Windows job's active process count, not the exit
of Edge's starter, which can hand off to a child process. Host diagnostics record
the starter exit status and owned process count for failed-launch investigation.
At launch the host searches visible top-level windows whose processes
belong to the Runner's lifecycle job. It raises that window without changing size
or making it permanently topmost, requests foreground activation, and confirms
GetForegroundWindow matches. If necessary it briefly attaches the foreground
thread's input queue and the Runner UI thread's queue, then immediately detaches
both after activation. Attempts stop
after success or ten seconds; switching away afterward is respected. A separate
FOCUS acknowledgement records foreground=true/false without replacing READY or
terminal states. Workbench reports a denied activation honestly.
