# Ariadne maintenance conventions

Preserve the existing architecture. Inspect runtime evidence before changing it.

## KStore / Hera access

Read [the SSH runbook](docs/hera-ssh-access.md) before NAS work. On 2026-10-07,
Warren installed and Codex independently verified persistent passwordless Docker
administration. From the **normal Codex sandbox**, use:

```powershell
ssh -F C:/Users/Warren/.ssh/config hera-kstore "sudo -n /usr/local/bin/docker ps"
```

Do not escalate the Windows shell for SSH: that account cannot read the
sandbox-owned private key. Do not change key ACLs or ask Warren to repeat a sudo
grant that already works. Recheck the command in a fresh session. Only an actual
authentication/privilege failure warrants another access diagnosis.

For Warren's own interactive PowerShell login, the separate correct command is:

```powershell
ssh -t -o BatchMode=no -o PubkeyAuthentication=no -o PreferredAuthentications=password Wazza@192.168.1.200
```

Passwords stay in the terminal, never in chat or scripts.

## Project ownership and cleanup

KStore's intended projects: garage, salon, linktree, n8n, searxng,
ariadne-signal-service, ariadne-discovery-service, ariadne-article-cache,
ariadne-news-backend. Their Compose files are the deployment authority; verify
DSM project names, Docker project labels, actual image IDs, ports and data mounts.

Use Compose updates. Do not clone old Compose labels with raw Docker create, or
leave indefinitely renamed rollback containers attached to projects. Keep private
rollback definitions, image/data archives and a written maintenance record instead.
Temporary tools must be removed after use or have a documented continuing purpose.
Never globally prune persistent volumes. Preserve n8n's encryption configuration
and database together, and never run two instances against its same SQLite data.

See [the maintenance record](docs/nas-maintenance-2026-10-07.md) for actual results
and archive locations. Do not put full Docker environment values into Git or chat.
