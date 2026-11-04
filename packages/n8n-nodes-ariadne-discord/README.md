# Ariadne Discord webhook node

This installable n8n node posts the `payload` already prepared in `state_json`
by the Ariadne News Service workflow. It uses n8n's existing
`discordWebhookApi` credential and requires the candidate's destination to
match the node's configured destination.

The sender appends `wait=true&with_components=true`, sends the Components V2
payload unchanged, and returns only the HTTP status, Discord message metadata,
and rate-limit headers needed by the existing delivery receipt step. It never
returns the credential URL or raw HTTP response. Transport errors are replaced
with a generic uncertain-outcome error so the workflow's existing ledger
remains the authority for retry decisions.

The **Inspect Webhook Destination** operation makes a GET request using the
same saved credential and returns only Discord's webhook ID, channel ID, name,
and type. Use it to map each saved credential to the configured destination;
it does not post a message.

Build and test:

```sh
npm install
npm run build
npm test
npm pack
```

See [`docs/automation/n8n/ariadne-discord-webhook-node.md`](../../docs/automation/n8n/ariadne-discord-webhook-node.md)
for the existing Synology deployment procedure and workflow wiring contract.
