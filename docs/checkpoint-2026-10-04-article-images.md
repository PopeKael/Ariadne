# Article image pipeline checkpoint — 2026-10-04

## Result

Warren confirmed that the refreshed Home briefing showed images throughout, with no star placeholders. Runtime verification found 100 card image references, 100 successfully decoded images and zero rendered placeholders.

Article Cache now owns publisher HTML fetching, article text extraction, representative-image discovery, byte validation, local image storage and bounded retries. Discovery, News Backend, Signal and retained Home cards consume that preparation. Failed RSS image hints fall through to page metadata and article heroes; prepared cards are protected from legacy Signal image overrides. Ranking, feedback, TLDR, Think and last-known-good snapshots retain their existing behavior.

The checkpoint includes source, dependency pins, regression tests, selective backfill and reversible Hera deployment helpers, plus the deployment handover. It excludes generated runtime artifacts, image bytes, databases, private deployment inspection records and password-window transcripts. Scope is the Ariadne repository only.

## Validation

- Discovery/preparation: 21 tests passed on the final source before checkpoint.
- News Backend: 7 tests passed.
- Local snapshot, retained-card and Home integration checks: 15 tests passed.
- Earlier focused Signal image/HTTP checks: 10 passed with semantic enrichment isolated. The broad Signal suite still has the documented pre-existing failure and external-probe timeout; it is not reported as fully green.
- Live Discovery refresh prepared 202 of 240 candidates; 38 retained failure diagnostics. All 240 candidates were handed to Signal successfully in 12 batches with no failed batches. Semantic health remained healthy.
- All 100 current Home images decoded successfully, including news, sports, video and retained older cards. The user confirmed the rendered result after restarting the core.

## Deployment and recovery

Discovery, News Backend and Signal use build `20261004-article-preparation-1`; Article Cache uses `20261004-article-preparation-2` for deterministic MIME serving. Original containers and SQLite snapshots remain available on Hera. See [the complete handover](article-image-preparation-2026-10-04.md) for paths, password SSH access and rollback.

Windows PowerShell was separately upgraded and verified at 7.6.6; this machine-level update is not part of the repository payload.
