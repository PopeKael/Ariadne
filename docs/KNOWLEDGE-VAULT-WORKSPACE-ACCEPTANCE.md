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
3. Open **Tools & Plugins** and select **Open Knowledge Vault Tools**. The
   workspace must show **Preparing Environment** while Rust Host handles the
   handoff. Verify the configured ingestion model and context in Configuration.
   Model-backed controls must remain unavailable until Host reports readiness.
4. Start **Full Vault Rebuild** from the workspace page. Confirm the worker
   reports the configured model and the same context length Rust Host admitted.
   Confirm it advances through sources and segments, writes checkpoints, and
   reports completion without an 8K-versus-16K profile rejection. For the
   current default that profile is GPT-OSS 20B at 16K; configured overrides
   must be reflected identically by Host and worker.
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

- Full Vault Rebuild reaches its terminal complete state with a completion
  report and no unprocessed source rows.
- Worker model, context, output settings and embedding settings match the saved
  Configuration values.
- Host ownership, BUSY responses, checkpoint handling, Dungeon handoff and
  Home restoration match the sequence above.
- Ordinary Knowledge Vault questions continue to work in Ariadne Chat and via
  external MCP clients.

Record the actual run identifier, completion report, final source counts, and
any Host or worker errors. A page saying “running” is not completion evidence.
