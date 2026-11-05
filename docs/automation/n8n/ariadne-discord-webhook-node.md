# Ariadne Discord webhook node

## Purpose

`packages/n8n-nodes-ariadne-discord` reuses n8n's encrypted
`discordWebhookApi` credentials for News Service card delivery. It uses the
existing Components V2 payload and delivery ledger; it does not introduce a
bot token, webhook URL field, destination registry, or second delivery history.

The node checks that the candidate's `state_json.destination` matches its
configured destination, posts the prepared payload unchanged with
`wait=true&with_components=true`, and returns the message ID and timestamp in
the fields consumed by `Build Delivery Receipt`. The transport strips raw
responses and replaces request exceptions with a generic uncertain-outcome
error. The workflow's existing inflight/receipt/deduplication rules remain the
retry authority.

## Build and test

From the package directory:

```powershell
npm install
npm run build
npm test
npm pack --pack-destination "D:\Downloads\N8N Automations\artifacts"
```

The package requires the n8n instance's existing `n8n-workflow` runtime as a
peer. `discordWebhookApi` is supplied by n8n and is not bundled. The `n8n.nodes`
manifest path must match the compiler output at
`dist/AriadneDiscordWebhook/AriadneDiscordWebhook.node.js`.

## Synology installation

The existing n8n project bind-mounts `/volume1/docker/n8n/data` to
`/home/node/.n8n`. Install package contents under
`/volume1/docker/n8n/data/nodes/node_modules/n8n-nodes-ariadne-discord`, then
restart the existing `n8n` project from Synology Container Manager. The Windows
SMB mapping `V:` exposes `/volume1/docker`; copying to the bind mount does not
require SSH.

Verify the node JavaScript file exists and the manifest points to that emitted
path before restarting. Then verify `/healthz` returns `{"status":"ok"}` and
search for **Ariadne Discord Webhook** in the n8n node picker.

The Container Manager terminal can run the normal package installation when it
can attach to the container:

```sh
npm install /home/node/.n8n/nodes/n8n-nodes-ariadne-discord-1.0.0.tgz --prefix /home/node/.n8n/nodes
```

If its terminal cannot attach, extract the same archive into the package
directory through the verified SMB mapping, preserving its contents and
manifest. Restart only the existing `n8n` project from Container Manager.

Keep the existing delivery branch gated while installing. Use each saved
`Discord Webhook` credential's **Inspect Webhook Destination** operation to map
its returned `channel_id` to the source registry's three configured
destinations. Then replace the failed bot sender with three destination-
specific node instances, each bound to an existing `discordWebhookApi`
credential. Keep the existing production guard, `Mark Delivery Inflight`,
`Build Delivery Receipt`, `Persist Delivery Receipt`, and activation checkpoint
unchanged. Do not export credential values or place webhook URLs in node
parameters.

Before opening the production delivery guard, verify one eligible new card
reaches its assigned channel, record the returned Discord message ID in the
existing delivery ledger, and rerun synchronization to confirm the sent key is
skipped. Then verify a non-dry-run Ariadne synchronization shows the newest two
cards per source, newest first.

## Local test coverage

The six tests verify URL validation, Components V2 request options and payload,
safe webhook inspection, fail-closed destination matching, and secret-free
transport failures. They use a stub request helper and do not constitute a
live Discord delivery test. The emitted node path is checked separately during
installation.
