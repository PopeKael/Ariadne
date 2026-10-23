# Knowledge Vault workspace acceptance

The code changes in this checkpoint have only offline verification. Do not treat
the Knowledge Vault as operationally accepted until the live sequence below
completes successfully on Warren's machine.

## Manual acceptance sequence

1. Close the game and any GPU-heavy application. Start Ariadne normally and
   confirm Home chat is ready with its configured model.
2. Ask an ordinary Knowledge Vault question in Ariadne Chat. Confirm the answer
   uses the normal Chat model and cites Vault material through the existing
   retrieval/MCP path; this must not load the ingestion model just for the query.
3. Open **Tools & Plugins** and select **Open Knowledge Vault Management**. The
   workspace must show **Preparing Environment** while Rust Host handles the
   handoff. In its focused settings view verify local `gpt-oss:20b` and the
   fixed 8,192-token total context budget; Home provider settings stay separate.
   Model-backed controls must remain unavailable until Host reports readiness.
4. Start a small real Inbox intake from the workspace page. Confirm the worker
   reports local `gpt-oss:20b` at 8,192 tokens, matching Rust Host admission.
   Confirm bounded segments, reasoning, validation, accepted checkpoints, and
   embedding handoff complete without changing source Markdown.
5. While an exclusive Vault operation is active, try opening another
   GPU-consuming Ariadne workspace. It must report **WORKSPACE BUSY** and keep
   the current workspace available. Verify status-only and other explicitly
   GPU-independent checks do not request a model lease.
6. During a separate controlled ingestion, use **Pause**, wait for the saved
   checkpoint confirmation, then **Resume** and **Stop**. Confirm accepted
   segments remain accepted and the stopped run is resumable.
7. After ingestion has reached a safe checkpoint, launch Dungeon Crawl. Verify
   Ariadne reports it as the exclusive owner and blocks conflicting work.
   Exit Dungeon normally and confirm Rust Host restores Ariadne's configured
   Home environment.
8. Return to Knowledge Vault Tools, finish any remaining work, and select the
   explicit workspace exit action. Confirm Ariadne restores its normal Chat
   model before normal chat operations are enabled.
9. Separately test recovery: close a Vault tab while a controlled ingest is
   inside a model request. Confirm the worker is asked to stop at its next
   checkpoint, its Rust Host lease remains held until that boundary, and an
   expired heartbeat eventually cleans up the abandoned workspace without
   corrupting or duplicating accepted work.

## Pass criteria

- The small Inbox intake reaches its terminal complete state with accepted
  outcomes, no new failures, and valid checkpoints.
- Worker model, context, output settings and embedding settings match the saved
  Configuration values.
- The prior ten-source, zero-failure 16K checkpoint remains preserved as
  recovery evidence; it is not the target runtime configuration.
- Host ownership, BUSY responses, checkpoint handling, Dungeon handoff and
  Home restoration match the sequence above.
- Ordinary Knowledge Vault questions continue to work in Ariadne Chat and via
  external MCP clients.

Record the actual run identifier, completion report, final source counts, and
any Host or worker errors. A page saying “running” is not completion evidence.
