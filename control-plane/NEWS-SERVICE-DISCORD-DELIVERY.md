# News Service manual Discord delivery: inactive preparation

`news_discord_delivery.py` is an unconnected state machine, not an HTTP endpoint or working transport. It requires explicit adapters for authenticated principal and CSRF verification, the shared `NewsDiscordFormat.build` formatter in `news-service.js`, destination-bound server-side credential lookup, and Discord transport. It has no default authentication, credentials or outbound transport, and must not be wired to the public app until all of those are implemented and independently verified.

Before enabling delivery:

1. Establish genuine user authentication at a verified edge or in Ariadne, protect the backend against direct bypass, and bind a CSRF token to the authenticated session. Same-origin checks, proxy IP allowlists, and a client-created Ariadne session ID do not establish user identity.
2. Rotate any webhook URLs previously disclosed outside their intended secret store, then provision separate credentials for `thailand_news` and `ai_noise` into a server-side secret store such as Windows Credential Manager. Do not expose URLs through browser requests, APIs, command lines, logs or Git. Confirm the host's Windows account can read them. Do not infer that a generic existing n8n credential belongs to either channel.
3. Build the payload on the trusted side from the stored publication using the same `NewsDiscordFormat.build` implementation used by the preview; do not trust client-provided payloads, destination URLs or status. Confirm output is identical to the reviewed preview and disallow unsolicited mentions.
4. Implement a fixed Discord Execute Webhook transport with `wait=true&with_components=true`, no redirect to arbitrary hosts, and secret-safe errors. Honor Discord's per-route and global rate limits. Keep the user-facing Send control disabled until all checks pass.
5. Handle `inflight` and `uncertain` attempts after a crash via explicit reconciliation. A timeout or an ambiguous error must never be silently retried. Review persistence and recovery under concurrency before production use.
6. Verify a user-bound confirmation flow, explicit separate resend confirmation, credential-rotation state, rate limits, authorization bypass resistance, delivery status and retry behaviour end to end with a mock server. No live message is sent by this preparation.

The isolated tests use disposable databases and fake credentials. The state-machine table is created only when its `send` method is called against a supplied database; it has not been created in the live News Service database.
