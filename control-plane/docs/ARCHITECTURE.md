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
                         +-- native Ollama and Windows applications
                         +-- WSL 2 / Ubuntu 24.04 on F: (only when explicitly needed)
                         +-- GPU and resource telemetry
```

## Responsibilities

### Windows host agent

The Windows agent is authoritative for the desktop. It observes and, after explicit approval, controls native Ariadne workloads, WSL, selected Windows applications, GPU workloads, and operating profiles. Docker Desktop is not an Ariadne runtime dependency and is never started, stopped, probed, recovered, or repaired by the host.

### Browser interface

The browser is the user interface. It should remain usable when all managed workloads are stopped.

Model Lab is the controlled local-model benchmark surface. It is recipe-driven:
the recipe owns benchmark instructions and canonical parameters, while the
operator uses the declared source material bundle (Test 1 restores its two
canonical Markdown files automatically). Standard runs use the
canonical recipe; deliberate parameter changes are recorded as Adapted, and a
model that cannot satisfy the recipe is reported as Standard Incompatible.
Capability negotiation covers context, text, vision, tools, structured output,
and provider-native reasoning. The current Ollama mapping is OFF -> `think:false`
and LOW -> `think:true` when the model reports boolean thinking; unsupported
discrete levels remain disabled.

The harness sends an explicit model, recipe, source order, and Ollama options to
native Ollama without changing Home routing or enabling web, tools, Vault
retrieval, memory, or conversation history. Streaming keeps thinking and final
response in separate panels, and each run records the exact instructions,
source metadata and hashes, capability snapshot, context estimate, effective
configuration, output, telemetry, provider timing, and Rust/avatar transition
acknowledgements in the append-only
`control-plane/runtime/model-lab-runs.jsonl` history. See
[`MODEL-LAB-BENCHMARK-HARNESS.md`](MODEL-LAB-BENCHMARK-HARNESS.md) for the
operator and acceptance contract.

### Synology deployment

The Synology hosts the always-on web presentation and, later, carefully limited coordination services. It must not become an unrestricted remote administrator of the Windows machine.

### Storage

- C: Windows, applications, and system-managed files.
- D: durable repositories, Knowledge Vault, and backed-up data.
- F: AI models under `F:\AI`, Ubuntu WSL, and active Linux tooling.
- E: video editing only.
- F: Ubuntu WSL, active Linux tools, builds, databases, and scratch space.

## Design principles

1. Observe before acting.
2. Every action has a named target, a reason, a result, and an audit record.
3. News deployment profiles are explicit: RUN uses the Hera production Signal/Discovery endpoints. Local DEV Signal/Discovery is manual/unavailable from Ariadne until a separate workflow exists; switching profiles never controls Docker. Gaming and renderer profiles remain separate workload concerns.
4. No service silently downloads large data to a default user folder.
5. Public code contains structure and examples; private configuration contains reality.
6. The system remains useful if Synology, Docker, WSL, or the browser is unavailable. Docker belongs to a future explicit Build/Deploy-to-Hera workflow, not host runtime management.
