# Rabbit Hole persistent library — 10 October 2026

Advancing discovery previously retained repository names as seen, but discarded
the browsable cards. Rabbit Hole now separates GitHub discovery from local
review decisions and navigation.

## Behaviour

- Undecided cards persist in `control-plane/runtime/rabbit-library.sqlite3`.
  GitHub discovery appends or updates cards; it cannot reverse a dismissal.
- Nine cards per page, three columns on desktop, two on intermediate widths,
  one on mobile. Previous/Next loops through the local library. The page cursor
  survives refresh; the page count and total undecided count are visible.
- Watchlist projects are excluded, including paused/archived watches. Watch or
  Dismiss fills vacated slots from the local collection without source requests.
  Dismiss persists; Undo restores the last dismissal, including after reload.
- Find something new still uses the existing bounded discovery job and shared
  GitHub guard. Local browsing is separate and never submits a discovery job.
- Check this project accepts an HTTPS GitHub repository URL or owner/project,
  normalizes it, and checks repository metadata plus the existing README,
  releases and issues assessment. It never clones or runs repository code.
- Explicit suggestions remain visible even if the assessment recommends Ignore;
  source findings still determine the verdict. A quota pause retains the link
  with pending-source status and Retry source check, without claiming compatibility.
  Already watched projects are directed to Watchlist; dismissed ones require Undo.
- Direct checks share the hourly/daily request caps, pacing and response cache;
  they do not spend a fresh repository-search batch or replace its paging cursor.

## Migration and verification

The latest result and reserve cards import idempotently. Earlier seen projects
are recovered only where cached source evidence remains and indicates relevance.
Cached metadata is reused where available; otherwise missing metadata is stated
explicitly. The seen ledger also includes screened candidates, so recovered
projects are not a reconstruction of an exact former page order.

The live collection contains 24 undecided projects across three pages after
excluding existing watches. Verified Previous/Next, wrap to first page, reload
persistence, Dismiss/local replacement/Undo and three equal desktop columns at
https://ariadne.dia.net.au/rabbit-hole after the user restarted Ariadne core.
A supplied link for an already retained project was checked: no duplicate card,
explicit pending analysis, retry control, and unchanged GitHub request counters.
The temporary verification dismissal was undone; zero dismissals remain.

44 focused Python tests passed across the library, discovery, adapter/server,
assessment and quota guard. HTTP coverage additionally verified Dismiss/Undo and
direct-check routing without replacing the discovery cursor. Browser-logic checks
cover local navigation without discovery jobs, progress, duplicate-click guards,
completion, quota errors and direct-link submission. JavaScript syntax and Git
whitespace checks passed. Live quota failures were observed without bypassing caps.

Runtime database, source caches and screenshots remain outside Git. Browser proof:
`control-plane/runtime/rabbit-library-2026-10-10.png`.

## API and checkpoint boundary

- GET `/api/rabbit-hole/result`: current local page plus shared budget status.
- POST `/api/rabbit-hole/library`: browse (next/previous/first), dismiss or undo;
  these operations do not submit a GitHub job.
- POST `/api/rabbit-hole/check`: session-owned asynchronous check of a supplied
  repository; source collection uses the existing shared request guard.
- `ARIADNE_RABBIT_LIBRARY_PATH` overrides the default local database location.

Warren accepted the live workflow and requested this checkpoint on 10 October
2026. About, changelog, the control-plane guide, Watchlist guide and documentation
index now describe it. The checkpoint includes source, regression tests and
this record. Private runtime state and the independent KnowledgeVault repository
are excluded. Commit/push completion is confirmed by exact local/remote tip
comparison and clean status in the final handoff.
