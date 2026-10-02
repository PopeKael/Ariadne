# Hera semantic matching: confirmed connection failure

Status: diagnosed; repair prepared, not activated. No firewall, listener,
provider configuration, or service restart has been performed.

Update: the platform HTTPS requirement supersedes the plain HTTP relay below.
Its Apply mode is disabled. Follow [the HTTPS design](https-platform-design-2026-10-01.md)
instead; the original connection failure is still unresolved.

## Evidence

- Hera Signal health (`192.168.1.200:8788/v1/health`) reports semantic matching
  unavailable and selects `ollama-lan-embedding`, `nomic-embed-text`, at
  `http://192.168.1.100:11434`.
- Desktop `127.0.0.1:11434/api/tags` lists `nomic-embed-text:latest`.
- Desktop `192.168.1.100:11434/api/tags` refuses the TCP connection.
- The running Ollama process listens only on `127.0.0.1:11434`.
- User environment `OLLAMA_HOST` is `127.0.0.1:11434`. Ariadne's owned Ollama
  startup inherits that setting and checks local model availability only.
- Enabled inbound block rules exist for Ollama executables. There is no
  existing Windows IPv4 portproxy relay for this endpoint.

This is a listener/connectivity failure. The earlier model detection and batch
size fixes cannot correct it. The origin/date of the loopback environment
setting has not been established.

The Home green "Semantic" indicator referred to the local Vault index, whereas
the Adaptive Profile warning referred to Hera news matching. The label now
reads "Vault index", and backend health has a separate "Signal matching"
component. Missing or unavailable matching health cannot display green. The
backend change requires the next core restart; it is not yet runtime-verified.

## Prepared repair and reversal

`00_System/Repair-HeraEmbeddingAccess.ps1` defaults to a read-only plan.
`-Mode Apply -WhatIf` also makes no changes. Administrator execution with
`-Mode Apply` creates a persistent relay from desktop `192.168.1.100:11434`
to loopback `127.0.0.1:11434`, and a named TCP firewall allow rule with remote
address `192.168.1.200`. Ollama environment and existing rules remain unchanged.
No service restart is part of applying this repair. Because the relay is
separate from Ollama, its listener is not recreated by Ariadne-owned Ollama
startup. Activation requires approval for the new network access.

`-Mode Remove` removes only that verified forwarding entry and named firewall
rule. Existing conflicting entries or differently scoped named rules cause a
failure before changes. The script checks the assigned desktop address, IP
Helper, and installed embedding model before application.

Syntax and read-only plan/WhatIf checks passed. Six Home health tests passed,
including independent Vault and Signal matching health. Network application
and runtime UI verification remain pending.

## Required acceptance before calling this repaired

1. From Hera, reach the configured endpoint and confirm the selected model.
2. From Hera, obtain a finite, non-empty vector using its selected embedding
   provider. Local desktop success alone does not satisfy this check.
3. Recompute existing Signal semantic matching and check actual matches and
   `/v1/health.semantic.state`, not just provider catalogue availability.
4. Verify the rendered Home status and Adaptive Profile agree.
5. Restart Ariadne and repeat the same remote embedding and matching checks.

Do not declare this fixed from unit tests, a local embedding, or a green Vault
index. Keep the private KnowledgeVault run outputs outside this checkpoint.

Windows mechanism references:
[portproxy](https://learn.microsoft.com/windows-server/networking/technologies/netsh/netsh-interface-portproxy)
and [New-NetFirewallRule](https://learn.microsoft.com/en-us/powershell/module/netsecurity/new-netfirewallrule?view=windowsserver2025-ps).
