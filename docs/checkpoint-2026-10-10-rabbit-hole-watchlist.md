# Rabbit Hole and Watchlist checkpoint — 10 October 2026

## Delivered

- A persistent local Watchlist for GitHub projects, broader public web questions
  and recurring reminders, with history, pause/archive controls and source evidence.
- Rabbit Hole discovery focused on the configured Windows/RX 7800 XT/gfx1101/
  RDNA3/16 GB workstation, local AI/media integrations, and practical Garage
  Alchemy episode experiments. Compatibility claims remain evidence-led; unknown
  installed disk requirements and untested runtime fit remain explicit.
- A compact introduction, shared Ariadne background, up to ten cards in two
  desktop columns, expandable statistics/evidence, saved-project exclusion,
  persistent unseen queues and Next ten. Refresh preserves the current page.
- Check all now queues active source watches, preserving reminders, paused and
  archived watches, and live worker claims. Tools order is Cleanup, Rabbit Hole,
  Watchlist, then background Services. Browser routes retain the canonical domain.
- Manual discovery, one fresh search batch per Bangkok day, and a shared durable
  GitHub budget/cache for discovery and watch checks. Ariadne caps network calls
  at 45 per rolling hour and 90 per Bangkok day, warns at 30/hour or 60/day, and
  also obeys GitHub quota reserves, pacing and persisted cooldowns. Cached cards
  remain available without spending requests; evidence retains its original age.
- About and the changelog reflect the implemented milestone.

## Validation

All 58 focused budget/discovery/assessment/Rabbit Hole/Watchlist tests passed.
They cover persistence, cursor retention after failure, saved exclusions,
replacement, source handling, local-test scoring, cache age and identity
isolation, hourly/daily stops, parallel requests at the final allowance slot,
server quota reserves, backoff and failure before network IO when storage fails.
JavaScript syntax and Git whitespace checks passed.
The related search-provider relevance, Signal client and plugin-registry suites
also passed (26 tests), giving 84 focused and related tests in total. The new
About milestone and everyday-testing panel were verified live on the canonical
domain before committing.

Live verification used https://ariadne.dia.net.au after Warren restarted the
core. Strata disappeared from discovery after being saved. After an initial
GitHub quota pause, its scheduled check succeeded with six sources and two new
or changed findings. Next ten displayed ten different reserve cards, preserved
by refresh. Compact Watchlist controls, Tools order, the desktop card columns
and shared background were checked in the browser. After the budget deployment,
both pages displayed the shared counters and cached cards showed Garage Alchemy
test ideas without network requests. Quota exhaustion itself was simulated in
isolated tests rather than deliberately spending the live allowance.

## Boundaries

Discovery reads bounded public metadata/documentation. It does not clone,
install or execute repository code. Scheduled Watchlist checks run while Ariadne
is running; there is no automatic daily discovery job. Local daily caps are
Ariadne policy, not a claim that GitHub has a daily quota. Other applications can
consume the same IP/account allowance; server headers can reduce our budget.

Full runtime machine-profile verification, Test Now execution, persistent Ignore
decisions, targeted historical issue searches, conversational watch management
and external notifications remain separate work.

## Publication boundary

The checkpoint contains reviewed framework code, tests, manifests, About,
changelog and documentation. SQLite state, source caches, runtime logs,
screenshots and credentials remain outside Git. The independent KnowledgeVault
repository and NAS services are unchanged by this source checkpoint.
The final commit, GitHub parity and clean worktree are verified after publication
and reported in the handoff; this file does not embed its own commit hash.
