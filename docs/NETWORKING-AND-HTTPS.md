# Networking and HTTPS

This guide describes Ariadne's portable deployment contract and deployment
choices. You do not need Wazza's Synology, home network, domain names, IP
addresses, certificates or Docker layout. It does not install or reconfigure
networking, and it does not claim that every choice is already packaged.

## Start local; add a network edge when needed

The simple first-run contract remains **`http://localhost:8765`**, bound to
loopback only. A purely local installation requires no domain, reverse proxy,
certificate, Docker deployment or HTTPS infrastructure.

**Current checkout limitation (11 October 2026):** the listener defaults to
`127.0.0.1:8765`, but browser entry routes in `control-plane/server.py` redirect
to a hardcoded `PUBLIC_ORIGIN` for Wazza's installation unless a proxy marks the
request as HTTPS. The Rust host and browser launchers also contain that origin.
Consequently the documented local HTTP first run is not currently fulfilled for
browser pages in this checkout. There is no generic public-origin setting that
this guide can tell you to set. This is a portability defect, not a requirement
to use Wazza's domain or build his network. A separate runtime change is needed
to restore the local contract and configurable browser origin. Do not spoof
forwarded headers or change machine-global routing to conceal this mismatch.

## Why HTTPS-first beyond localhost?

HTTPS gives browser-facing deployments a secure origin and protects traffic
between private devices. It provides a stable foundation for future browser
features, service integration and eventual packaged deployment. Localhost HTTP
is a special case: browsers treat loopback origins as potentially trustworthy
for many secure-context features. This does not make ordinary LAN HTTP secure.
See [MDN's secure-context explanation](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Secure_Contexts).

Keep application work separate from TLS and network access. The application
owns chat, retrieval and jobs; the edge owns certificates, accepted clients and
routing. An HTTPS browser URL does not require every isolated internal hop to
use HTTPS, nor does encryption provide authentication by itself.

## Service boundaries

| Layer | Responsibility | Network boundary |
| --- | --- | --- |
| Ariadne application services | Browser UI, control plane, jobs, retrieval orchestration and application APIs | Private control endpoints; loopback by default |
| Local model services | Ollama or another deliberately configured inference/embedding provider | Separate service; keep raw model APIs private |
| MCP, services and tools | Vault access, integrations and specific capabilities | Each has its own transport, permissions and authentication; UI access does not authorize all tools |
| Optional Docker, WSL or external services | Selected search, news, media or other supporting workloads | Only needed for the chosen features; not first-run prerequisites |
| HTTPS/TLS edge | Browser-facing encryption, certificate lifecycle, access policy and forwarding | The deliberate entry point for other devices |
| User-provided infrastructure | DNS, VPN/tunnel, proxy, NAS, VM/container host or certificate authority | Owned and configured by the installer, independent of Ariadne |

The native Windows resident host supervises the core. That existing lifecycle
does not mean it already manages every external service or TLS deployment.

## Deployment modes

### 1. Local-only

```text
Browser on this computer -> http://localhost:8765 -> Ariadne control plane
                                                   -> private local services
```

Keep the listener on loopback. No LAN listener, port forwarding or certificate
is needed. On another device, `localhost` means that other device, not the
Ariadne computer. The current browser-redirect limitation above must be resolved
before this checkout can deliver the promised first run; adding TLS is not a
substitute for that correction.

### 2. Private/home network HTTPS

```text
Authorized private browser -> HTTPS edge/reverse proxy -> Ariadne control plane
                                                          -> private services
```

Choose one canonical browser URL that your devices can resolve, and a valid
certificate trusted by those devices. The edge may be on the Ariadne machine
or elsewhere. A same-machine edge can reach the loopback backend. An edge on
another host cannot reach that loopback listener directly: it needs an explicitly
restricted private listener, authenticated gateway or tunnel designed for that
hop. Do not solve reachability by exposing the core to all interfaces.

Keep access restricted to authorized devices/users. Where application endpoints
do not supply authentication, enforce it at the edge or authenticated private
access layer and prevent direct backend access. A home LAN alone is not an
authentication mechanism.

### 3. Existing infrastructure

Use infrastructure you already operate: Synology's reverse proxy, Caddy, nginx,
Traefik, another NAS, or a secure tunnel with appropriate access controls.
These are alternatives, not dependencies. A tunnel's transport encryption does
not automatically make its published URL private or authenticated.

The installer owns DNS, certificate issuance/renewal, client trust, routing and
access policy. Use that product's current documentation. Wazza's deployment
records illustrate one installation; their hosts, certificates, ports and Compose
project names are not a reusable installation recipe.

### 4. Self-contained single-machine deployment

Supporting services required by selected features may live on the same computer
using Docker/containers, a lightweight VM, or application-managed processes where
supported. None of these is mandatory for the basic local interface. Keep their
listeners private and give each service its own data/configuration boundary.

The tradeoff is additional RAM, storage and startup time in exchange for a more
complete deployment on one machine. Model memory and disk usage are additional
costs. Container/VM loopback is not the host's loopback: map each service hop
explicitly. Installing Docker or WSL alone does not configure Ariadne's services.
This guide supplies no new images, Compose bundle, VM image or service supervisor.

### 5. Future packaged Ariadne

Where practical, a future package may start and supervise supporting services,
perform readiness checks and manage shutdown so users do not have to recreate
Wazza's home lab. This is a direction, not a completed installer capability.
Optional integrations and user-provided infrastructure can remain external;
network exposure, trust and credentials must still be explicit choices.

## Generic network contract

- **Localhost-safe defaults:** use loopback HTTP for first run. Do not require
  certificates or silently enable remote access.
- **Configurable service endpoints:** model, MCP and supporting-service addresses
  belong to installation configuration, not assumptions about a particular NAS.
  Distinguish browser-reachable URLs from addresses used only by backend clients.
- **Canonical browser URL:** one installation-owned origin for navigation,
  assets, API calls and streams; relative browser routes should retain it.
  Local mode uses `http://localhost:8765`; a networked mode uses the installer's
  HTTPS origin. Do not send another device's browser to the server's localhost.
- **Configurable TLS edge:** terminate TLS at the selected proxy/gateway.
  Validate hostname and certificate trust, and arrange renewal. Use TLS on
  upstream hops that cross untrusted networks. Isolated loopback or appropriately
  restricted private HTTP is acceptable under this generic contract.
- **Streaming and proxy metadata:** preserve incremental response delivery and
  suitable timeouts; avoid proxy buffering that hides progress. Forward the
  original protocol and host where required by the application. The trusted edge
  must replace client-supplied forwarding headers; the backend should trust only
  its known proxy peers. Headers are metadata, not authentication. See the
  [Forwarded header reference](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Forwarded)
  and [nginx proxy buffering documentation](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_buffering).
- **Private controls stay private:** do not publicly expose unauthenticated
  control APIs, raw inference endpoints, Vault retrieval, MCP or internal tool
  routes. HTTPS alone does not make those endpoints safe to publish. Preserve
  each service's own authentication and authorization boundary.
- **Private state stays outside Git:** credentials, private keys, live Vault
  content, generated catalogues, logs and deployment secrets are installation
  data. Public examples use placeholders; review previously tracked files too.

This is the architectural contract, not a claim that all settings and proxy
trust protections are implemented in every current code path.

## Current configuration and portability gaps

Verified from repository source, without changing the runtime:

| Area | Current implementation | Meaning for a new installation |
| --- | --- | --- |
| Core listener | `ARIADNE_BIND_ADDRESS` defaults to `127.0.0.1`; `ARIADNE_PORT` defaults to `8765` | Keep defaults for local use; changing the bind is an access decision |
| Browser origin | Hardcoded `PUBLIC_ORIGIN` and launcher URLs; browser redirects inspect `X-Forwarded-Proto` | Generic origin/local-mode configuration remains unfinished; forwarding headers must not be an exposure workaround |
| Supporting services | Signal, Discovery and Article Cache have endpoint overrides, but their RUN defaults contain Wazza's NAS address | Supply your own endpoints for enabled features; do not rely on those defaults |
| Models | Core's main `OLLAMA_URL` is fixed to loopback; other provider paths have their own configuration | Do not assume one environment variable relocates all model clients |
| Vault | Configuration supports `ARIADNE_VAULT_ROOT`; the current storage default is Wazza's local Vault path | Explicitly choose your own Vault; the repository is not automatically the live Vault |
| TLS support | `tls_transport.py` supports optional certificate/key settings; `https_gateway.py` loads a local gateway configuration with a peer allowlist | Existing building blocks, not a portable turnkey edge or proof of complete access control |

Existing endpoint overrides include `ARIADNE_SIGNAL_SERVICE_URL`,
`ARIADNE_DISCOVERY_SERVICE_URL` and `ARIADNE_ARTICLE_CACHE_URL`; DEV profiles have
their own Signal/Discovery overrides. There is no new setting introduced here.
The historical [HTTPS cutover record](https-platform-design-2026-10-01.md)
contains a stronger, installation-specific end-to-end TLS objective. It does not
override this generic allowance for isolated internal HTTP.

Before deploying beyond localhost, map the complete browser → edge → core →
model/MCP/service topology, including listeners, names, trust, authentication,
streams and return URLs. Verify redirects, incremental responses, blocked direct
backend access and certificate renewal in the chosen deployment. Never create
machine-global forwarding, firewall or certificate changes merely to make a test
pass. Unresolved portability gaps require separately scoped implementation work.

Start with [Clone and deploy](CLONE-AND-DEPLOY.md); consult the
[security boundary](../control-plane/docs/SECURITY.md) for private-data rules.
