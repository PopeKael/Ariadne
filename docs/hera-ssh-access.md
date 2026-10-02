# Hera SSH access from Windows

Read this before giving Warren an SSH command or requesting access again.
Do not assume Codex automation and Warren's interactive PowerShell have the
same access to a private key. Do not ask for passwords in chat.

## Verified on 2026-10-02

- Hera/KStore: `192.168.1.200`; SSH account: `Wazza`.
- Effective workstation SSH configuration sets `BatchMode yes`, including
  connections made directly to the IP address. This disables password prompts.
- Warren's PowerShell could not read the alias key
  `C:/Users/Warren/.ssh/hera_wazza_ed25519` (Permission denied).
- Overriding only public-key authentication does not override BatchMode.
- The corrected command below was checked with `ssh -G`: BatchMode is no,
  public-key authentication is false, password authentication is yes, and
  preferred authentication is password. Warren's terminal screenshot confirms
  successful password login, authenticated sudo, image build
  `20261002-private-https-4`, and the existing Signal container starting.
  The live health endpoint independently confirms that build and healthy
  semantic matching (39 matches, 9,754 stored signal embeddings).
- Prior access audit recorded authenticated sudo Docker access, but no direct
  Docker socket access. Recheck privileges rather than assuming access persists.

## Interactive connection check

Run in Windows PowerShell, not inside an already connected NAS shell:

```powershell
ssh -t -o BatchMode=no -o PubkeyAuthentication=no -o PreferredAuthentications=password Wazza@192.168.1.200
```

Enter the NAS password in the terminal. On success, the prompt must be the
NAS shell. Windows sudo is unrelated and must not be enabled for this task.

## Prepared Signal update

This command changes the existing Signal deployment, so use only when its
update is authorized and its source and Compose file are already prepared:

```powershell
ssh -t -o BatchMode=no -o PubkeyAuthentication=no -o PreferredAuthentications=password Wazza@192.168.1.200 "sudo /usr/local/bin/docker compose -p ariadne-signal-service -f /volume1/docker/ariadne-signal-service/docker-compose.yml up -d --build"
```

The SSH login and sudo may each prompt for the NAS password. Do not append
instructions such as `and reply done` to a shell command.

## Before every future attempt

1. Read this runbook and inspect `ssh -G` for the exact proposed command.
2. Separate network reachability, authentication, sudo privileges, and Docker
   deployment verification. A reachable SSH port does not establish login.
3. If a command fails, inspect the failure and effective configuration before
   sending another command. Do not cycle through guessed variants.
4. After deployment, verify the live build identifier and service health.
   A successful shell command alone does not prove the application is fixed.
