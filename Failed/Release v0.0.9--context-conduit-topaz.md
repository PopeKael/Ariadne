---
title: "Release v0.0.9--context-conduit-topaz"
source: "https://github.com/openai/tunnel-client/releases/tag/v0.0.9--context-conduit-topaz"
author:
published:
created: 2026-06-26
description: "Contribute to openai/tunnel-client development by creating an account on GitHub."
tags:
  - "clippings"
---
## tunnel-client v0.0.9 - Context Conduit Topaz

Compared with `v0.0.8`, this release focuses on enterprise gateway compatibility, control-plane connectivity, and day-2 supportability. It keeps the customer distribution shape the same while making prefixed gateway deployments, mTLS control-plane access, and operator diagnostics more practical.

## Release Assets

- Combined distribution bundle: `all.zip`
- Combined tarball: `all.tar.gz`
- Platform zips: `darwin-amd64`, `darwin-arm64`, `linux-amd64`, `linux-arm64`, `windows-amd64`, `windows-arm64`
- Public URL manifest: `PUBLIC_URLS.txt`
- Checksums: `SHA256SUMS.txt`

Public base URL:  
`https://persistent.oaistatic.com/tunnel-client/v0.0.9--context-conduit-topaz/`

Public downloads:

- [`all.zip`](https://persistent.oaistatic.com/tunnel-client/v0.0.9--context-conduit-topaz/tunnel-client-v0.0.9--context-conduit-topaz-all.zip)
- [`all.tar.gz`](https://persistent.oaistatic.com/tunnel-client/v0.0.9--context-conduit-topaz/tunnel-client-v0.0.9--context-conduit-topaz-all.tar.gz)
- [`darwin-amd64.zip`](https://persistent.oaistatic.com/tunnel-client/v0.0.9--context-conduit-topaz/tunnel-client-v0.0.9--context-conduit-topaz-darwin-amd64.zip)
- [`darwin-arm64.zip`](https://persistent.oaistatic.com/tunnel-client/v0.0.9--context-conduit-topaz/tunnel-client-v0.0.9--context-conduit-topaz-darwin-arm64.zip)
- [`linux-amd64.zip`](https://persistent.oaistatic.com/tunnel-client/v0.0.9--context-conduit-topaz/tunnel-client-v0.0.9--context-conduit-topaz-linux-amd64.zip)
- [`linux-arm64.zip`](https://persistent.oaistatic.com/tunnel-client/v0.0.9--context-conduit-topaz/tunnel-client-v0.0.9--context-conduit-topaz-linux-arm64.zip)
- [`windows-amd64.zip`](https://persistent.oaistatic.com/tunnel-client/v0.0.9--context-conduit-topaz/tunnel-client-v0.0.9--context-conduit-topaz-windows-amd64.zip)
- [`windows-arm64.zip`](https://persistent.oaistatic.com/tunnel-client/v0.0.9--context-conduit-topaz/tunnel-client-v0.0.9--context-conduit-topaz-windows-arm64.zip)
- [`SHA256SUMS.txt`](https://persistent.oaistatic.com/tunnel-client/v0.0.9--context-conduit-topaz/SHA256SUMS.txt)

## Highlights

- **Prefixed enterprise gateway routes**  
	`tunnel-client` now accepts an optional control-plane URL path through `--control-plane.url-path`, `CONTROL_PLANE_URL_PATH`, and `control_plane.url_path`.  
	This is for gateways that need a workspace, environment, or tenant prefix before the normal `/v1/...` tunnel routes, for example `https://gateway.example.com/workspace/dev/us/v1/tunnel/<tunnel_id>/poll`.
- **Control-plane mTLS**  
	Runtime control-plane connections can now present a client certificate and key through `--control-plane.client-cert`, `--control-plane.client-key`, `CONTROL_PLANE_CLIENT_CERT`, `CONTROL_PLANE_CLIENT_KEY`, and matching YAML fields.  
	When mTLS is configured against the default API host, `tunnel-client` automatically uses `https://mtls.api.openai.com`.
- **Explicit MCP session termination**  
	Control-plane tunnel closes can now be forwarded upstream for transports that support session termination.  
	This lets the client relay an explicit session close instead of leaving the upstream MCP session lifecycle ambiguous.
- **Structured runtime health checks**  
	`tunnel-client health` now probes `/healthz` and `/readyz` from a base URL, URL file, or loopback port.  
	The command is intended for operator checks, local scripts, and troubleshooting flows where a simple curl is too thin.

## Diagnostics and Support

- **Safer default health binding**  
	The default health listener now binds to `127.0.0.1:8080`.  
	Deployments that need kubelet, sidecar, or other trusted remote probes can still opt into `HEALTH_LISTEN_ADDR=:8080`.
- **Richer Tunnel MCP diagnostics**  
	Tunnel MCP status now surfaces more useful inspector, runtime, plugin-install, and binary-hint state.  
	The Codex-facing app tools and self-checks make native runtime workflows easier to inspect before asking users to debug by hand.
- **Better control-plane error detail**  
	Control-plane API failures now preserve structured error detail instead of collapsing everything to status-only messages.  
	This should make auth, mTLS, and route failures easier to distinguish from generic connectivity problems.

## Compatibility and Reliability

- **Protected control-plane headers**  
	Reserved control-plane headers are now protected from override by extra-header configuration.  
	This keeps authentication and tunnel-client identity headers under client control.
- **Broader regression coverage**  
	Added coverage for connector authorization forwarding, control-plane extra-header authorization, HTTP guard spoof cases, and prefixed control-plane routes.  
	Tunnel integration ownership, no-service coverage, and failure visibility were also tightened.
- **Updated handoff docs**  
	Onboarding, deployment, handoff, and secure MCP tunnel guide cross-references were refreshed for the supported release path.