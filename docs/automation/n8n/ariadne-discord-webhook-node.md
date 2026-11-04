# Ariadne Discord webhook node

## Purpose

`packages/n8n-nodes-ariadne-discord` adds the smallest sender node needed to
reuse n8n's encrypted `discordWebhookApi` credentials for News Service card
delivery. It uses the existing Components V2 payload and delivery ledger; it
does not introduce a bot token, webhook URL field, destination registry, or
second delivery history.

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
peer. `discordWebhookApi` is supplied by n8n and is not bundled.

## Synology installation

**Current checkpoint:** the package builds and its six local tests pass. The
Synology SSH key is present but this execution context is denied read access
to it (`Permission denied`), and there is no interactive SSH client session
available to install or restart the NAS container. The deployed workflow has
therefore not been switched to this node, and automatic webhook delivery has
not been enabled or claimed as verified. In the signed-in n8n credential UI,
`Discord Webhook account 4` displayed **Connection tested successfully**; that
confirms the saved credential works for Discord's credential test, but does
not identify its destination channel or constitute a message post.

To install the generated `n8n-nodes-ariadne-discord-1.0.0.tgz`, copy it to
`/volume1/docker/n8n/data/` on Synology, then run this on the NAS shell. The
Compose project is the existing `n8n` project at the established path
`/volume1/docker/n8n-compose/compose.yaml`:

```sh
sudo -n /usr/local/bin/docker compose -p n8n -f /volume1/docker/n8n-compose/compose.yaml exec -u node n8n sh -lc 'mkdir -p /home/node/.n8n/nodes && npm install /home/node/.n8n/n8n-nodes-ariadne-discord-1.0.0.tgz --prefix /home/node/.n8n/nodes'
sudo -n /usr/local/bin/docker compose -p n8n -f /volume1/docker/n8n-compose/compose.yaml restart n8n
```

After restart, confirm the node appears in the n8n node picker before changing
the workflow. The existing delivery branch must stay gated while installing.
Use each saved `Discord Webhook` credential's **Inspect Webhook Destination**
operation to map its returned `channel_id` to the source registry's three
configured destinations. Then replace the failed bot sender with three
destination-specific node instances, each bound to the already saved
`discordWebhookApi` credential. Keep the existing production guard,
`Mark Delivery Inflight`, `Build Delivery Receipt`, `Persist Delivery Receipt`,
and the activation checkpoint unchanged. Do not export credential values or
place webhook URLs in node parameters.

Before opening the production delivery guard, verify one eligible new card
reaches its assigned channel, record the returned Discord message ID in the
existing delivery ledger, and rerun synchronization to confirm the sent key is
skipped. Then verify a non-dry-run Ariadne synchronization shows the newest
two cards per source, newest first.

## Local test coverage

The tests verify URL validation, Components V2 request options and payload,
safe webhook inspection, fail-closed destination matching, and secret-free
transport failures. They use a stub request helper; they do not constitute a
live Discord delivery test.
