# Ariadne reference architecture

## Purpose

Ariadne is a local control plane for Warren's mixed Windows, Linux, GPU, storage, and NAS environment.

The design separates the interface from the authority that controls the desktop:

```text
Browser UI
    |
    +-- localhost during development
    +-- Synology-hosted deployment for always-on access
             |
             +-- authenticated Windows host agent
                         |
                         +-- WSL 2 / Ubuntu 24.04 on F:
                         +-- Docker Desktop
                         +-- selected Windows applications
                         +-- GPU and resource telemetry
```

## Responsibilities

### Windows host agent

The Windows agent is authoritative for the desktop. It observes and, after explicit approval, controls WSL, Docker, selected AI services, GPU workloads, and operating profiles.

### Browser interface

The browser is the user interface. It should remain usable when all managed workloads are stopped.

### Synology deployment

The Synology hosts the always-on web presentation and, later, carefully limited coordination services. It must not become an unrestricted remote administrator of the Windows machine.

### Storage

- C: Windows, applications, and system-managed files.
- D: durable repositories, models, Knowledge Vault, and backed-up data.
- E: video editing only.
- F: Ubuntu WSL, active Linux tools, builds, databases, and scratch space.

## Design principles

1. Observe before acting.
2. Every action has a named target, a reason, a result, and an audit record.
3. Profiles are explicit: General, Gaming, Interactive AI, Rendering, and Development.
4. No service silently downloads large data to a default user folder.
5. Public code contains structure and examples; private configuration contains reality.
6. The system remains useful if Synology, Docker, WSL, or the browser is unavailable.
