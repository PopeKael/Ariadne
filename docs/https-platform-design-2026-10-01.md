# Ariadne HTTPS cutover design

Status: Synology certificate issued and assigned; desktop TLS prepared but not
activated. Semantic matching remains broken. DNS, firewall rules, listener
ports, proxy upstreams and containers remain unchanged.

## Applied certificate correction

Synology issued a separate Let's Encrypt certificate for ariadne.dia.net.au,
expiring 2026-12-30, and assigned it only to the existing Ariadne proxy rule.
Independent verification to NAS port 8765 passed trust, hostname and TLS 1.3
checks. The corresponding HTTPS listener was reloaded by DSM. Other certificate
assignments were preserved.

Desktop code now supports TLS through ARIADNE_TLS_CERT_FILE and
ARIADNE_TLS_KEY_FILE, with ARIADNE_TLS_REQUIRED preventing HTTP fallback.
Client probes validate certificates; ARIADNE_TLS_CA_FILE adds an explicitly
trusted CA without replacing default public roots. Three real TLS/configuration
tests and six Home health tests passed. Running Ariadne has not been restarted.

Certificate export through the in-app browser did not return a download.
Read-only SSH cannot access the NAS certificate directory without sudo.
Desktop activation therefore still needs a local certificate/key export (kept
outside Git), controlled listener access, and the private embedding route.
Canonical port 443 and the all-platform HTTPS cutover are not yet complete.

## Prepared activation scope

- `00_System/Enable-AriadneHttps.ps1` defaults to Plan; Apply requires admin
  rights and explicit approval of this network-access change.
- Windows private-profile TCP 18765 accepts Hera 192.168.1.200 only. Ariadne's
  gateway independently validates its proxy-peer allowlist.
- The gateway runs inside the existing core and exposes only Nomic model
  availability/embedding operations on its private Ollama route. Raw Ollama
  remains loopback-only. No new NAS container or independent gateway process.
- Windows resolves the canonical name to NAS 192.168.1.200 through one backed-up
  hosts entry, leaving Cloudflare unchanged. Conflicting overrides stop Apply.
- Synology's unsaved `Ariadne LAN` access profile allows 192.168.1.0/24 then
  denies all other sources. The proposed proxy source is HTTPS 443 and its
  upstream is HTTPS 192.168.1.100:18765. Private name resolution for the Signal
  client must be checked and, if needed, configured in its existing project.
- Changing this access scope requires confirmation before activation. Restart
  the core only after checking there is no active ingest/model job. Related
  Signal configuration/restart may be needed; container cleanup stays deferred.
- Four HTTPS gateway tests passed, including backend isolation and rejection of
  model operations. The exported real certificate successfully verified in a
  temporary local gateway request returning only the embedding model catalogue.

Desktop certificate files are currently a private export snapshot; NAS renewal
does not yet refresh that snapshot. Renewal synchronization/expiry monitoring
must be completed before claiming HTTPS lifecycle reliability.

The table below records the initial state before the certificate correction.

## Requirements

- Canonical browser origin: `https://ariadne.dia.net.au`, standard port 443.
- HTTPS across the platform, including service-to-service network connections.
  Do not silently treat a plain HTTP upstream as satisfying this requirement.
- Speechify is Warren's existing speech-to-text and TTS service. Support both
  the browser extension and the future integration with more application control.
- Preserve the seven intended NAS projects; add no ad hoc containers.
- Container reconciliation is deferred while Ariadne functionality is repaired.
- Do not activate the superseded HTTP Ollama portproxy repair.

## Confirmed current state

| Connection | Observed state | Required change |
|---|---|---|
| DNS `ariadne.dia.net.au` | CNAME to `klf23.synology.me`; public IPv4 and IPv6 answers | Preserve the established name; verify LAN resolution and both address families during cutover |
| Synology Ariadne frontend | Generated nginx listens on 8765 with SSL, not 443 | Configure canonical HTTPS 443 routing |
| Ariadne rule certificate | Let's Encrypt certificate for workshop.dia.net.au with SANs alicebeauty, dev, warren and workshop; Ariadne absent | Issue/assign a trusted certificate containing ariadne.dia.net.au |
| Synology upstream | `http://192.168.1.100:8765` | Use a reachable, verified HTTPS desktop endpoint |
| Desktop Ariadne | Default bind 127.0.0.1; LAN port 8765 actively refuses connection | Provide a managed TLS listener restricted to intended proxy access |
| Hera embeddings | `http://192.168.1.100:11434`; desktop Ollama listens only on loopback | Provide an authenticated private HTTPS embedding route to local Ollama |
| Hera Signal and Discovery | HTTP on 8788 and 8789 | Publish private TLS service endpoints and change configured clients |
| Core HTTPS probes | `probe_http` and `json_http` use unverified SSL contexts | Enforce certificate and hostname validation; explicitly provision private CA trust where applicable |
| Browser requests | Most Home API requests use relative URLs | Keep same-origin routing; audit every asset, iframe, stream and launch URL |
| Renderer launch | app.js contains hardcoded HTTP localhost:8766 URLs | Route rendered content through the HTTPS origin and verify streaming/navigation |

On NAS port 443, the current response for the Ariadne SNI name presents the
default Synology certificate. On the actual Ariadne port 8765, certificate
validation fails specifically because Ariadne is absent from its SAN list.
Certificate inspection with validation disabled was used only to inspect
certificate metadata; no application requests used that bypass.

## Target routing and trust boundaries

1. Browser reaches the canonical HTTPS origin. Synology handles its existing
   reverse-proxy role, valid certificate, and appropriate access controls.
2. Synology reaches the Windows Ariadne service over verified HTTPS. A managed
   desktop TLS endpoint must be available before changing the proxy destination.
   Preserve loopback-only raw backend listeners where a TLS adapter is needed.
3. Browser API, images, voice controls and renderer content use same-origin
   HTTPS paths. Preserve streamed chat delivery and long ingest-session heartbeats.
4. Ariadne's backend accesses private Signal/Discovery HTTPS routes. Hera uses
   a private, authenticated HTTPS embedding route on the desktop. These machine
   routes must not become unrestricted public APIs under the browser domain.
5. Ollama natively serves HTTP. If wrapped with TLS, distinguish the raw
   implementation listener from configured HTTPS client endpoints. A remaining
   adapter-to-Ollama loopback HTTP hop is an explicit exception requiring a
   decision; it must not be presented as end-to-end HTTPS compliance. If no such
   exception is acceptable, use a backend with native TLS support.
6. Public-domain DNS does not by itself authorize public access to control-plane
   actions or inference. Establish browser authentication and separate machine
   credentials before making those routes Internet-accessible. Use private
   resolution/routes where external access is not required.

Certificate provisioning, renewal, trusted issuers, private service names and
listener ports must be recorded before activation. Do not disable TLS
validation or substitute `https://` text for implementing a TLS server.

## Speechify acceptance

- Test the existing extension against the authenticated canonical page: reading
  dynamic assistant replies and dictation into the actual input field.
- Keep API credentials on the backend. Use the provider's HTTPS API and return
  voice results through authenticated HTTPS application routes.
- Verify actual STT/TTS behavior, microphone permissions where used, browser
  site permissions, streaming and playback. This audit does not establish a
  universal HTTP limitation for every Speechify product; HTTPS is independently
  the user's platform requirement.

## Cutover sequence and completion criteria

1. Preserve current configuration snapshots and data; select certificates,
   private routes and authentication. No new stray containers.
2. Establish desktop TLS reachability and certificate validation from Hera.
3. Correct the Synology frontend port, certificate assignment and HTTPS upstream.
4. Update service clients and browser launch URLs; remove unverified TLS probes.
5. From Hera, produce a finite embedding through the configured private HTTPS
   route; then confirm real Signal matches and healthy semantic status.
6. Verify Home Vault-index and Signal-matching indicators independently, all
   browser assets/streams without mixed content, ingest pause/resume/stop and
   Speechify extension/API behavior where integration exists.
7. Restart Ariadne and repeat browser, remote embedding and matching checks.
   Verify LAN/public IPv4/IPv6 paths according to the intended access scope.
8. Checkpoint only reviewed files after runtime acceptance. HTTPS and semantic
   matching are not complete until their real-client checks pass.

References: [Ollama HTTP server and proxy behavior](https://docs.ollama.com/faq),
[Synology source/destination protocols](https://kb.synology.com/en-au/DSM/help/DSM/AdminCenter/application_appportalias?version=6),
[Speechify official API quickstart](https://docs.speechify.ai/build/guides/get-started/quickstart).

## Activation evidence and remaining work

The approved private HTTPS cutover is active. Windows maps ariadne.dia.net.au
to 192.168.1.200. The existing Synology rule now listens on HTTPS 443 and
forwards to HTTPS 192.168.1.100:18765, using the issued Ariadne certificate.
The access profile allows 192.168.1.0/24 and the separately approved Signal
container address 192.168.112.2, then denies all other sources. Windows permits
the TLS port only from Hera on the Private network profile.

The gateway runs inside the resident Python core. Activating it required a
resident-host restart: the Rust supervisor observes a terminated core but does
not automatically respawn it. Canonical HTTPS /api/status returned 200, the
Home page rendered in the browser, and Hera verified a trusted TLS connection
and one finite 768-dimensional embedding. All 14 focused TLS, gateway and
independent health-indicator tests passed.

Signal was recreated through its existing DSM project, preserving its data
volume and project association. Its Compose file includes the private DNS
mapping; its selected embedding provider endpoint is now
https://ariadne.dia.net.au/internal/ollama. The user ran a container-side
diagnostic: private resolution returned 192.168.1.200 and trusted HTTPS model
catalogue retrieval returned 200. The old 1.5-second availability deadline was
insufficient for a cold proxy/TLS request (a successful desktop request was
observed taking 4.875 seconds). HTTPS probes now allow 10 seconds; local HTTP
probes retain their existing deadline. DSM built and activated image
ariadne-signal-service:20261001-private-https-1, followed by
20261001-private-https-2 for nonblocking health reporting.

A replay using the exact registered source was accepted as one duplicate, with
zero rejections or errors. Real Signal enrichment then reported healthy,
50 matches, 9,576 stored signal embeddings, nine stored interest embeddings,
and three newly embedded signals. Certificate validation remained enabled.

Restart acceptance exposed an idle TCP/TLS preconnection blocking the gateway's
accept loop. A real socket regression reproduced the failure before the fix.
TLS handshakes now execute in individual worker threads with a bounded handshake
timeout; idle peers cannot stall other clients. The 15 focused control-plane
tests pass. A fresh finite vector and healthy semantic replay were verified
after the resident-host restart, and the browser's Signal matching indicator
subsequently reported healthy through the canonical HTTPS origin.

The Signal health endpoint previously called the live inference snapshot,
probing several providers on every request. That could exceed Ariadne's health
deadline and falsely report Signal offline while matching was healthy. Health
now returns previously observed provider states without network I/O; unseen or
changed Ollama configurations are reported Unknown. Embedding operations and
the inference configuration endpoint still perform live checks. The deployed
health response completed in 0.357 seconds; a real duplicate intake again
reported healthy semantic matching with 50 matches. Four focused inference
tests pass, including no network I/O from health snapshots and invalidation
when the provider endpoint changes.

The full Signal suite ran 44 tests: 42 passed, one semantic category-threshold
assertion failed, and one intake HTTP contract timed out while fetching fixture
images. Both affected tests also fail against the unchanged HEAD baseline
(the older health probe times out earlier in the HTTP contract). These are
recorded existing failures; this work does not claim the full suite is green.

The NAS was missing the repository's .dockerignore. It is now deployed, with
runtime data and rollback source copies excluded from image-build context.
The project uses pull_policy: build for its locally versioned image.

Outstanding: automatic synchronization of renewed desktop certificate copies;
Synology upstream certificate validation (its generated rule currently has no
proxy_ssl_verify directive); migration of remaining network service addresses,
renderer URLs and launchers; Speechify runtime acceptance; and private DNS
configuration for additional LAN devices.
The localhost HTTP implementation hops remain internal. This activation is not
proof of complete platform HTTPS compliance or restored semantic matching.

The first diagnostic candidate replay used a source URL ending in a slash while
the registered Discovery source did not. That exposed a separate source-upsert
uniqueness failure; replay with the exact registered source succeeded. No source
schema or container-cleanup changes were made as part of this activation.


## 2026-10-02 launch and recovery correction

The previous activation left browser launchers unfinished. Rust avatar/tray dashboard activation, the PowerShell launcher, and the legacy Python tray now use `https://ariadne.dia.net.au/`. The installed release host was rebuilt and replaced. Local browser page requests redirect to this origin, retaining path and query; proxy requests avoid redirect loops. Host readiness uses a separate cheap `/api/core/ready` response rather than page rendering or optional service diagnostics.

Renderer browser links and God’s Eye View launch redirects use `/workspaces/video/` and `/workspaces/gods-eye-view/` on the canonical origin. The fixed-loopback workspace proxy forwards assets, requests, and binary output; renderer GPU workloads were not started for this change. These workspace runtime checks remain distinct from Home acceptance. Application-owned HTML/JS browser links have no local HTTP launch addresses. Private core/Ollama loopback transports and existing NAS service transports are still HTTP internally; this change does not claim full internal transport migration, renewal synchronization, or upstream certificate verification.

Health was repeatedly reading and parsing the 313,568,414-byte embedding index twice per request. Vault counts now share a file-signature cache and refresh after source changes; semantic index status reuses that summary. Live trusted HTTPS checks after activation: Home 0.069 s, activity 0.262 s, adaptive 0.237 s, Create 0.055 s, System 0.047 s. A subsequent host restart measured readiness 0.056 s, activity 0.247 s, adaptive 0.244 s. These are response timings, not full first-paint measurements.

Signal matching had retained a failed result after the desktop embedding route returned. The recovery update retries unavailable/pending matching every 30 seconds in the background, reuses stored embeddings, serializes semantic enrichment, and preserves collection state when Hera has no local feeds. The new regression proves unavailable -> healthy recovery without new intake or duplicate embeddings. The existing category-ranking test still fails as recorded on 2026-10-01; the other seven adaptive tests pass. Eighteen focused control-plane tests and seventeen Rust host tests pass.

Warren activated Signal image `20261002-private-https-4` with the corrected interactive SSH command. The live health endpoint independently confirms that exact build, service state healthy, semantic state healthy, 39 matches, 9,754 stored signal embeddings, 9 stored interest embeddings, and no semantic error. This includes the final intake-only timer correction. DSM had shown a stale prefixed Signal container name that Docker reported nonexistent; project Stop had no effect, so activation used the existing Compose project through authenticated sudo. Data mounts and project ownership are preserved. The old installed host is backed up under ignored `Data/semantic-repair/ariadne-host.before-https-20261002.exe`.
