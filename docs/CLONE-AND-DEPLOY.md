# Clone and deploy Ariadne

This guide is for somebody who wants to build their own local Ariadne-style
Knowledge Vault and control panel from the public repository.

It is a Windows-first, local deployment. GitHub stores the source and history;
the application and personal Vault run on the user's own computer.

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

The default Vault root is the Ariadne repository itself. This is convenient
for a new local test installation, while private Vault folders remain ignored
by Git.

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
.\.venv\Scripts\python.exe .\control-plane\server.py
```

Open this address in a browser:

`http://localhost:8765/`

The server binds to loopback only. It is not exposed to the local network by
default.

For the tray companion and browser launch flow, use another PowerShell window:

```powershell
$env:ARIADNE_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
.\control-plane\start-ariadne.ps1 -OpenBrowser
```

The tray menu can open, restart, or exit Ariadne. Only run one control-plane
instance on port `8765`.

## 6. Optional Windows startup

After the manual launch works, install the per-user logon task:

```powershell
.\control-plane\install-startup.ps1
```

The task starts the local tray companion at logon. Remove the task if it is no
longer wanted:

```powershell
Unregister-ScheduledTask -TaskName 'Ariadne Local Control Plane' -Confirm:$false
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

Each user still supplies their own knowledge, identity material, Ollama or
other local models, WSL/Docker services, hardware, and operating choices. A
clone does not reproduce Wazza's private memory or machine environment.

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
- [Current handover](HANDOVER-2026-08-15.md)
- [Control-plane security](../control-plane/docs/SECURITY.md)
- [Public/private boundary](../.gitignore)
