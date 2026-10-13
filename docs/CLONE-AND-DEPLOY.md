# Clone and deploy Ariadne

This guide is for somebody who wants to build their own local Ariadne-style
Knowledge Vault and control panel from the public repository.

It is a Windows-first, local deployment. GitHub stores the source and history;
the application and personal Vault run on the user's own computer.

For local HTTP, private HTTPS and optional single-machine service deployment,
read [Networking and HTTPS](NETWORKING-AND-HTTPS.md). No Synology, Wazza-owned
domain or home-network layout is required. The current checkout has documented
browser-origin and service-default portability gaps; this guide does not imply
those runtime gaps have been fixed.

## The four GitHub words that matter

- **Fork** creates a separate GitHub copy under your account. Use this when
  building your own independent Ariadne.
- **Clone** downloads a GitHub repository onto your computer.
- **Branch** is a separate line of work inside one repository. Use it for
  changes you may later merge through a pull request.
- **Deploy** means installing and running the cloned project on your computer.

For an independent installation, the normal path is:

```text
Fork -> Clone -> Install -> Add your Vault -> Run locally
```

## 1. Fork the repository

On GitHub, open the Ariadne repository and select **Fork**. Choose your own
account as the owner. This gives you a separate remote repository where you
can change the project without changing the original.

If you are only evaluating Ariadne, you can clone the original repository
instead. Forking is the better starting point for a personal build.

## 2. Clone your fork

Install Git for Windows, then run PowerShell:

```powershell
git clone https://github.com/YOUR-GITHUB-NAME/Ariadne.git
Set-Location .\Ariadne
```

Replace `YOUR-GITHUB-NAME` with your GitHub account name.

## 3. Create the local Python environment

Python 3.10 or newer is required. A virtual environment keeps Ariadne's
packages separate from the rest of the computer. Activation is optional; the
commands below call the environment directly and work without changing
PowerShell's execution policy.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r .\control-plane\requirements.txt
```

The control-plane server uses Python's standard library. Pillow and pystray
are needed by the optional Windows tray companion.

The MCP HTTP endpoint has a separate dependency set:

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\requirements-mcp.txt
```

Install that only when you intend to run the authenticated MCP HTTP endpoint.

## 4. Choose your Vault location

Choose your own Vault location explicitly. The current configured storage default
is specific to Wazza's installation; the repository is not automatically the
active Vault. Private Vault folders and generated state must remain outside Git.

To use an existing Vault somewhere else, set the root for the current
PowerShell session before starting Ariadne:

```powershell
$env:ARIADNE_VAULT_ROOT = 'D:\Path\To\Your\KnowledgeVault'
```

Do not copy somebody else's private Vault into your fork. Create or restore
your own `Inbox`, `Wiki`, `People`, `Entities`, and related folders locally.
Do not commit personal notes, credentials, tokens, cookies, model files, or
generated indexes.

## 5. Start the browser interface

For the simplest first run:

```powershell
$env:ARIADNE_ALLOW_UNSUPERVISED_CORE = '1'
.\.venv\Scripts\python.exe .\control-plane\server.py
```

This is an explicit development run: on Windows the current core otherwise
requires its resident Rust host. Normal operation uses that host's supervised
lifecycle. Setting this override does not enable network access or HTTPS.

Open this address in a browser:

`http://localhost:8765/`

The server binds to loopback only. It is not exposed to the local network by
default.

This is the intended simple local browser contract, requiring no certificates
or proxy. **Current limitation:** browser pages redirect to an installation-
specific HTTPS domain and launchers use that domain. A separate portability fix
is needed before a fresh clone can fulfill the local browser contract. See
[the source-verified gaps](NETWORKING-AND-HTTPS.md#current-configuration-and-portability-gaps);
do not reproduce Wazza's DNS/certificates or add global forwarding as a workaround.

For the legacy Python tray and browser launch flow, use another PowerShell window:

```powershell
$env:ARIADNE_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
.\control-plane\start-ariadne.ps1 -OpenBrowser
```

After the Rust toolchain is installed, build the resident host:

```powershell
cargo build --release --manifest-path .\control-plane\host\Cargo.toml
```

The normal resident path is then the Rust host, which starts and supervises
the Python core. See [`control-plane/docs/RESIDENT-HOST.md`](../control-plane/docs/RESIDENT-HOST.md)
for startup installation, IPC, avatar assets, and rollback. Only run one
control-plane instance on port `8765`.

## 6. Optional Windows startup

After the host manual smoke test works, install the per-user Startup shortcut:

```powershell
.\control-plane\install-startup.ps1
```

The shortcut starts the Rust host at logon. Remove it if it is no longer wanted:

```powershell
.\control-plane\remove-startup.ps1
```

## 7. Verify the installation

The browser should display the dashboard. A direct status check is:

```powershell
Invoke-WebRequest -UseBasicParsing http://localhost:8765/
Invoke-RestMethod http://localhost:8765/api/status
```

Run the repository regression tests from the project root:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s 00_System -p 'test_*.py'
```

## What this deployment does and does not provide

The repository provides the reusable Ariadne structure:

- Knowledge Vault services and retrieval foundations;
- local MCP boundaries;
- controlled Toolshed and service adapters;
- the browser Workspace/interface and Windows launcher.

Each user supplies their own knowledge, identity material, models as needed,
hardware and operating choices. WSL, Docker, NAS and other external services are
optional supporting infrastructure for selected features, not mandatory first-run
requirements. They may run on the same machine or existing private infrastructure;
future packaging may supervise more of them. A clone does not reproduce Wazza's
private memory or machine environment.

## Working on your fork

Create a branch before changing the project:

```powershell
git switch -c feature/my-ariadne-change
```

Commit and push that branch to your fork:

```powershell
git add .
git commit -m "describe the change"
git push -u origin feature/my-ariadne-change
```

If you are collaborating on the original repository, open a pull request
instead of pushing directly to `main`.

## Before calling a fork public

Review the repository for personal material before publishing it. The
`.gitignore` protects the main private Vault folders, but it cannot remove
personal information that was deliberately committed earlier. Check project
notes, reports, identity files, diagrams, logs, and home-lab references.

The repository's architecture and security references are:

- [Four parcels](FOUR-PARCELS.md)
- [Networking and HTTPS](NETWORKING-AND-HTTPS.md)
- [Current handover](HANDOVER-2026-08-15.md)
- [Control-plane security](../control-plane/docs/SECURITY.md)
- [Public/private boundary](../.gitignore)
