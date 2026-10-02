# Checkpoint before personal news recommendations

Date: 2026-10-02 (Asia/Bangkok). Warren requested a full Ariadne checkpoint
and GitHub push before implementing the agreed reaction and ranking work.
KnowledgeVault remains a separate repository and is outside this checkpoint.

## Current implementation

- Canonical browser origin: https://ariadne.dia.net.au/.
- HTTPS gateway, launchers, readiness endpoint, workspace proxy, cached Vault
  counts, independent Signal matching indicator, nonblocking Signal health,
  and background semantic recovery are included in this checkpoint.
- Background news polling preserves existing card DOM and order, appends new
  article IDs once, and does not remove displayed cards. Reload applies ranking.
- No new recommendation behaviour or reaction controls have been implemented.
- Live checks during checkpoint: canonical HTTPS Home returned 200; Signal
  build 20261002-private-https-4 reported service and semantic state healthy.
  No restart, deployment, or model workload was triggered for these checks.
- Earlier status paragraphs in the October 1 repair/design documents describe
  historical stages. The later October 2 activation evidence and this snapshot
  describe the accepted state.
- Internal HTTP transports, certificate renewal synchronization, upstream
  certificate verification, and workspace runtime acceptance remain separate
  limitations. Container cleanup is deferred.

## Agreed next-stage behaviour

- Three compact reactions with hover descriptions: thumbs up = Useful,
  heart = More like this, thumbs down = Not interested.
- One reaction per article; it can be changed or removed.
- Reacting marks the article seen and teaches future recommendations. Removing
  a reaction removes that preference evidence; it does not undo having seen it.
- Unseen stories lead seen stories on deliberate reload; seen stays accessible.
  Positive reactions must not pin the already-read story at the top.
- Explicit reactions outweigh opens. Ignoring a card is not negative evidence.
- Learning is conservative with sparse evidence, keeps room for exploration
  and wider news, and avoids broad source/country/category penalties from one
  rejected story. No numerical mix or learning weights have been agreed.
- Recommendation explanations should state meaningful reasons rather than
  treating rank scores as relevance percentages or confidence.

## Confirmed integration gaps

Home news feedback persists to the news backend and local snapshot, but does
not rebuild Signal's learned profile. News feedback does not mark consumed;
TLDR/discussion opens do. Local ranking currently boosts the rated article
itself. Signal uses a separate latest-feedback table and semantic-interest
aggregation; joining multiple interests can multiply source/category counts.
Article identity, replay safety, history preservation, and changed/removed
reactions need a coherent contract before connecting those paths.

## Research informing the next stage

- [Informfully Recommenders](https://github.com/Informfully/Recommenders):
  diversity-aware news re-ranking, including maximal marginal relevance, and
  evaluation of the breadth of the resulting feed.
- [Vowpal Wabbit](https://github.com/VowpalWabbit/vowpal_wabbit): incremental
  learning and controlled exploration. Its full engine is not a dependency
  decision; a small transparent implementation is the proposed starting point.
- [Recommenders](https://github.com/recommenders-team/recommenders): news
  models, long/short-term interests, and evaluation examples.
- [Gorse feedback semantics](https://gorse.io/docs/concepts/data-source):
  read-without-positive feedback can be negative training evidence, which
  conflicts with the agreed meaning of silence for Ariadne.

These are references, not proof of suitable weights for one user's sparse
daily feedback, and not evidence of Perplexity's proprietary implementation.

## Validation

- 19 focused control-plane tests passed: HTTPS entrypoints, real TLS gateway
  routing/streaming/idle connections, certificate verification, identity health.
- Signal inference/adaptive tests: 12 run, 11 passed; the recorded existing
  semantic category-threshold assertion still fails (AI Watch versus Main News
  Feed). The new cached semantic recovery regression passed.
- Home presentation: 4 run, 3 passed; the recorded existing assertion for
  `collapse.hidden = !meaningful` still fails.
- JavaScript syntax checks and the card-order regression passed.
- All 17 Rust host tests passed using the existing ignored target-https directory.
- Changed PowerShell scripts parsed without syntax errors. Diff check passed.
- Full suites are not claimed green. Earlier full Signal testing also recorded
  an intake HTTP fixture timeout; that full suite was not rerun here.

Next: implement the agreed reaction contract and a small ranking/profile
integration with conservative learning and diversity, preserving card order
through polling. Validate behavioural examples before deployment.
