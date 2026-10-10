# Universal Watchlist — first workflow

Implemented locally on 2026-10-10. This document records scope and verification;
it is not a claim that a running core has reloaded the Python changes.

The control plane owns durable follow-ups at `/watchlist`. Rabbit Hole discovers
repositories; its **Watch this project** action adds a project to this same store.
Saving a repository again returns its existing entry, including paused/archived
decisions. Explorations never replace or delete watches. There is no five-watch
cap. Archive is reversible; there is deliberately no delete endpoint.

## Watch types and interaction

- Project: one or more source URLs, with an optional web query.
- Topic: a saved web query, source URLs, or both.
- Reminder: a recurring task for Warren; it never searches or changes Windows.

Every entry has a title, purpose, frequency, next check, state, last check,
last successful check, linked evidence and persistent history. The editor can
change its question/sources and explicitly set its next check in the browser's
local timezone. Dates are stored in UTC. Intervals mean elapsed days; 28 days is
four weeks. A reminder remains due until **Mark done**, which schedules the next
occurrence from acknowledgement. **Reviewed** clears an evidence notification;
**Snooze a week**, **Pause**, **Resume** and **Archive** retain the entry/history.

The **Needs a look** view surface first findings, new or
changed evidence, failed checks and due reminders. There are no external
notifications in this version. Routine unchanged checks remain in History.

## Evidence and scheduling

`watchlist_sources.py` uses the existing SearchProviderRegistry, excluding the
built-in Wikipedia fallback for monitoring (Home's default routing is unchanged).
Searches return up to five linked snippets. GitHub repository URLs read the
README, three latest releases and five recently updated issues; pull requests
are excluded. Other public HTTP(S) pages use the existing readable-text parser.
Source excerpts favour words in the saved purpose/question. They are quotations
of evidence, not an AI judgement that AMD support works or a change is important.
Older issues beyond that window are not exhaustively searched in this version.

Requests use timeouts and response size limits. Saved source URLs and redirects
are checked against private/reserved addresses; credentials and custom ports
are rejected. An optional ARIADNE_GITHUB_TOKEN is used only for GitHub API calls
and is not included in stored evidence. No repository is cloned, installed or
executed, and source text is never treated as an instruction.

The first successful collection establishes a reviewable baseline. Later checks
compare evidence by source URL and content digest, ignoring search order and
disappearing results. Search-result movement is not proof of a real-world event.
If a configured source fails, or collection returns no usable evidence, the
watch retains its previous successful findings and shows an error. Failed checks
retry after one hour. History retains check-time queries, sources and evidence.

One background worker runs while the Ariadne Python core is running, independently
of open browser pages and without model/GPU work. Due checks are picked up on
startup, with no repeated catch-up runs. SQLite claims prevent duplicate workers
and repeated Check now requests from running the same check concurrently. Editing,
pausing, snoozing or archiving invalidates in-flight results. A normal shutdown
releases this worker's claims; after a hard crash a claim expires in 15 minutes.

## Existing news topics

Signal Service still owns news-topic matching and Home news ranking is unchanged.
**Bring in existing news topics** explicitly reads its active topics and creates
broader follow-ups here. Import is idempotent and never reactivates paused or
archived watches. It is a one-way import, not two-way synchronization. Changing a
follow-up does not edit its original Signal news-matching topic.

ChatGPT scheduled tasks are not imported. Missing old tasks cannot be reconstructed
from names alone. No live watches are silently seeded from the conversation.

## Storage and operation

- Default: `control-plane/runtime/watchlist.sqlite3` (runtime data, not Git source).
- Override: `ARIADNE_WATCHLIST_PATH` for an isolated instance/test store.
- Source: `control-plane/watchlist.py`, `watchlist_sources.py`, `watchlist.*`.
- API: GET/POST `/api/watchlist`, POST `/api/watchlist/<id>/action`,
  GET `/api/watchlist/<id>/history?before=<cursor>` (30 checks per page).
- Import: POST `/api/watchlist/import-news-topics`.

Back up the SQLite file while the core is stopped. Reverting source does not
delete the database. Loading these server routes and starting the scheduler
requires restarting the core through the resident Rust host's tray menu.

## Verification

`test_watchlist.py` covers persistence beyond five entries, duplicate Rabbit Hole
identities and archived decisions, baseline/change detection, search reordering,
failure retention, four-week reminders across restart, snooze/pause/resume,
concurrent claims, expired-claim recovery, paginated history, browser-independent
scheduling, source validation/redirects, GitHub evidence and HTTP routes/import.

Actual read-only collection returned llama.cpp README/release/issue evidence and
five relevant results for `local audio AI AMD`. The first attempt exposed the
encyclopedia fallback; the monitoring exclusion was added and the search repeated.

Browser acceptance uses a separate verification database on loopback port 8877,
not Warren's live Watchlist. Created a topic and a project with real sources,
acknowledged an overdue Windows schedule reminder and saw its next date 28 days
later, reloaded persisted entries, and saved/archived a Rabbit Hole project. Its
Rabbit Hole card retained the archived decision.

The targeted Watchlist/Rabbit Hole/search/client/Home-signal/plugin suite passed
62 tests. After the final acknowledgement, reminder-rescheduling, claim-release
and shared GitHub-identity corrections, all 19 Watchlist tests passed again.
JavaScript syntax and Git whitespace checks passed. Restarting the isolated
preview retained the watches and their linked check history.

The isolated verification server has been stopped and its helper removed. Its
SQLite database is retained as verification evidence. User-facing entry points
stay on the canonical domain: `https://ariadne.dia.net.au/plugins`, with Rabbit
Hole and Watchlist registered as Tools. Watchlist is not a primary navigation
item; both tool pages highlight Tools and link to each other. The running core
must be restarted through the resident host tray to load the new backend routes.

Two unrelated existing presentation tests expect strings already absent from
the checkpoint: Home expects `collapse.hidden = !meaningful`, and WorkspaceShell
expects an old `http://localhost:8766/` link in Create. Those files were not changed.

## Rabbit Hole follow-through

### Rabbit Hole lifecycle and workstation evidence

Cards now preserve GitHub repository `created_at`, `pushed_at`, `updated_at`, stars,
forks, open issues/PRs and repository size from search results. Creation is labeled
**Repository created**, not project inception. A recent push is labeled as such,
not proof of sustained maintenance. Published releases and prereleases are
distinguished; no release is not treated as proof of abandonment. GitHub size
excludes the installed dependency/model footprint and is never used as an install
space estimate.

The trusted read-only adapter inspects the selected ten repositories with four
bounded workers: README (up to 100,000 decoded characters), three release notes
and five updated issues (up to 30,000 characters each). Existing public-source
transport caps each API response at 512 KB and ten seconds. Pull requests and
draft releases are excluded. Only metadata and text are read; no repository code
is cloned, imported, installed or executed.

`control-plane/plugins/rabbit-hole/workstation.json` records Warren's supplied
Windows/RX 7800 XT/gfx1101/RDNA3/16 GB profile. The assessment compares explicit
minimum VRAM requirements against that budget, flags explicit NVIDIA/CUDA
requirements and linked open issue reports, and shows Windows, AMD, VRAM, disk
and integration excerpts. A mention is not proof of compatibility. Missing data,
404 absence, partial failures and rate limits remain distinct. Keyword matching
is conservative source screening, not a hardware test or a full dependency audit.

The assessment explains relevance to MCP, Ollama, llama.cpp, OpenAI-compatible
connections or local AI when documentation contains those anchors. Generic local
variables do not count as local AI support. Recommendations are Watch when fit is
unconfirmed, Ignore for explicit resource/platform conflicts or no found workflow
connection. This first source screen does not issue Test Now or certify that
software runs on this machine. There is no persistent Ignore decision yet.

The first live pass collected metrics and complete bounded evidence for ten
cards. The GitHub record for EvoMesh showed creation on 30 August 2026 and a push
on 10 October 2026. Ten assessment tests cover evidence boundaries, missing
sources, optional CUDA, configured VRAM comparisons, issue reports and relevance
false positives, wrapped negations and evidence-cache invalidation; five existing Rabbit Hole tests also passed. The assessment
helper loads afresh with each trusted adapter run. Unchanged successful source inspections are cached for one hour, with at most
40 repository entries. Push/update changes invalidate the cache; failures are
not cached as successful checks. Anonymous GitHub quotas can
limit repeated explorations; source failures remain visible in each assessment.

Rabbit Hole's presentation was subsequently redesigned and visually verified on
`https://ariadne.dia.net.au/rabbit-hole`: shared Ariadne background, readable
shortlist, expandable discovery notes, direct GitHub links and persistent
Watchlist actions. Warren's refinement removes the workstation panel, compresses
the introduction to 198 pixels below the desktop navigation and places the Find
button on the right. A fresh live search returned ten projects, verified as two
columns and five rows; narrow screens use one column. The five Rabbit Hole tests
passed with a larger fixture checking the ten-result limit. Cards explicitly
mark workstation compatibility and disk requirements as still unchecked. The
Rabbit Hole and Watchlist suites passed 25 tests after the presentation changes;
JavaScript syntax checks passed. Static page changes appeared on refresh without
another core restart.

### Selection correction (10 October 2026)

The previous discovery searched broad novelty topics, sorted by update time,
then chose ten with a two-per-language cap before inspection. That admitted
unrelated trading/game projects while useful README-only and older matches could
be missed. Selection now searches README/name/description using GitHub best match
for RX 7800 XT, gfx1101, Windows/AMD local AI, Ollama/MCP and llama.cpp/Windows.
There is no creation/push-date gate and no programming-language quota.

Up to sixteen unique candidates receive bounded source inspection; documented
hardware/integration connections determine final ordering. Known Ignore results,
irrelevant projects and incomplete source checks are excluded before presenting
up to ten cards. Fewer good matches are preferable to filler. Explicitly nominated
repositories live in `plugins/rabbit-hole/discovery-sources.json`; Warren's
`Niko1221/Strata` nomination is included without bypassing assessment or creating
a saved watch. This is bounded discovery, not an exhaustive GitHub catalogue.

A live canonical-domain run searched six queries, found 136 unique matches,
inspected sixteen and displayed ten, including Strata. Its README explicitly lists
RX 7800 XT, Windows, a 12 GB VRAM minimum and approximately 80 GB free disk space;
the inspected release/issue evidence also contains Windows AMD problems and fixes.
Statistics are collapsed by default. The focused assessment, Rabbit Hole and
Watchlist suites passed 38 tests, including rejecting irrelevant results before
selection, backfilling ten cards and allowing a mature project to rank first.
An additional screen excludes builds explicitly described as targeting another
GPU architecture, even when a generic README reference table mentions our card.

### Initial persistent browsing and bulk checks (10 October 2026)

Historical implementation below; local navigation and retention are superseded
by the persistent library described in the next section.

Rabbit Hole now commits its current page, inspected reserve, pending metadata,
search page and consumed repository names together through Core's existing atomic
last-result writer. Refresh keeps the current page. Saved GitHub projects are
excluded authoritatively from every result response, including saves from another
tab; inspected reserve cards replace their slots immediately. If needed, a refill
job collects more evidence while preserving the other cards. Next ten consumes
the current page and then the reserve/pending queue, fetching deeper GitHub pages
when that queue is empty. Search/source failures retain the previous page and
cursor; unknown or irrelevant projects are never filler. The GitHub search window
is bounded to 33 pages per query, below the API's 1,000-result ceiling.

The Watchlist header includes Check all now. One transaction queues active
projects/topics, preserves live worker claims, and leaves reminders and paused or
archived watches unchanged. Collection remains asynchronous in the existing
worker. The header/cards are tighter and use Ariadne's shared background.
Tools now presents Cleanup, Rabbit Hole and Watchlist before a Services heading
containing Document Analysis and Signal Service.

All 45 focused discovery, assessment, Rabbit Hole and Watchlist tests pass, covering durable
multi-page browsing, replacement without disturbing other cards, refresh
stability, failed-check cursor retention, bulk queue exclusions and HTTP dispatch.
Warren restarted the core, and live browser checks confirmed saved Strata is
absent from Rabbit Hole, the Tools order, and Check all now queueing Strata.
GitHub's anonymous quotas were initially exhausted during live verification;
the failed Next ten attempt retained the existing page and browsing position.
After the quota reset, Strata's scheduled check succeeded at 10:22 with six
sources and two new or changed findings. At 12:34, live Next ten advanced to ten
different, previously inspected reserve cards with no repeated projects; a
refresh preserved that exact page. Desktop screenshots confirmed the two-column
Rabbit Hole layout, compact Watchlist controls and shared background. Deeper
search-page progression is additionally covered with deterministic source data.

### Persistent undecided library and supplied projects (10 October 2026)

The current workflow keeps undecided projects in a separate local SQLite library.
Nine cards per page appear in three desktop columns. Previous/Next loops locally,
and refresh preserves the page. Watching or dismissing a project replaces its
slot from the library; dismissal persists and Undo restores the last dismissal.
Discovery appends or updates cards without reversing decisions.

Check this project accepts a GitHub repository link or owner/project and runs the
same bounded metadata, README, releases and issues assessment. Explicit suggestions
remain visible even if rated Ignore. A quota pause preserves the suggestion with
pending analysis and Retry; it does not claim verified workstation compatibility.
Direct checks leave the discovery cursor intact. Earlier seen projects are
recovered from surviving evidence where possible, with missing metadata labelled.

See [library implementation and verification](rabbit-hole-library-2026-10-10.md)
for storage, API, migration limits, tests and accepted live behaviour. This
supersedes the earlier Next ten and automatic network refill behaviour.

### GitHub safety budget and local episode value (10 October 2026)

Discovery remains manual; there is no daily discovery job. Opening, refreshing,
Previous/Next, Dismiss/Undo and saving a watch use the local project library
without GitHub requests. Find something new advances the bounded discovery
queue under the shared guard. Check this project inspects a supplied repository
under the same request caps without spending a fresh search batch.

`github_budget.py` owns a shared SQLite ledger/cache in
`control-plane/runtime/github-budget.sqlite3`. Rabbit Hole search and both
repository evidence readers use it. Ariadne's own safety caps are 45 network
requests per rolling hour, 90 per Bangkok calendar day, six search requests per
rolling minute, and one fresh discovery search batch per Bangkok day (including
partial/failed attempts). These are local limits, not GitHub daily limits.
Existing watches retain their own schedules and share the same request caps.
Requests are serialized and spaced by at least two seconds in the running core.
Response headers enforce a reserve of ten core requests and one search request
when GitHub reports a live quota window. Other software on the same IP/account
may spend allowance too; the local ledger cannot account for those calls, so
GitHub's headers can reduce the available budget at any point.

403/429 responses persist a shared pause, respect Retry-After and exhausted
quota reset times, and increase cooldown on repeated refusals. No button bypasses
the guard; storage failure prevents the network call. Fresh cached responses
remain available during a pause, are isolated by hashed authentication identity,
expire after 24 hours, and are bounded to 200 entries with seven-day eviction.
Credentials are never stored in the ledger. Existing older inspection files
retain original evidence timestamps; cache reuse cannot make evidence younger.

Both pages display local hourly/daily counts. Warnings start at 30 hourly or 60
daily requests, or a low reported GitHub allowance, before the hard stop. A
discovery attempt is remembered across restarts until the next Bangkok
day. Saved cards are rescored from cached evidence without fetching sources;
evidence over a day old is explicitly marked as needing refresh. A practical local
demonstration adds an editorial score and a Garage Alchemy test idea, while
documented hardware blockers still prevent a positive recommendation.

Verification: all 58 focused budget/discovery/assessment/Rabbit Hole/Watchlist
tests passed, including hourly/daily stops, cross-instance persistence, parallel
requests competing for the last slot, source-cache age, identity isolation,
server quota reserves, backoff and failure before IO when storage is unavailable.
After Warren restarted the core, the canonical live Rabbit Hole and Watchlist
pages displayed the shared budget. Cached cards displayed Garage Alchemy test
ideas with zero network requests in the new ledger. JavaScript syntax checks
and git diff whitespace checks passed. Rate-limit failures were simulated in
isolated tests; verification did not deliberately exhaust the live allowance.

### Remaining follow-through

This completes the shared follow-up foundation and initial source screening.
Runtime machine-profile verification,
full Test Now analysis and persistent Ignore decisions,
targeted historical issue searches, natural-language chat management and external
notifications remain separate work. The Watchlist is usable through its editor
and Rabbit Hole; it does not claim those later capabilities.
