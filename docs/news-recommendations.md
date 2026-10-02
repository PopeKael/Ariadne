# Personal news recommendations

Implemented 2026-10-02. Home uses three reactions: 👍 Useful, ❤️ More like
this, 👎 Not interested. Click the selected reaction again to remove it.
Each reaction marks the article seen; clearing it preserves seen status.
Refresh applies the current ranking. Background polling keeps existing cards
in place and appends new article IDs. The Seen button opens retained history.

## Persistence and ownership

Home's authoritative journal is `runtime/news-recommendations.sqlite3`.
Article IDs connect a retained article snapshot with immutable reaction events.
Retries reuse an event ID and do not duplicate evidence. The latest event wins,
including an empty reaction. Legacy Hera ratings are imported once per article;
later Hera refreshes cannot resurrect a cleared local rating. Existing Hera
history is preserved. New reactions are saved locally, not mirrored to Hera's
legacy feedback system. Article opens continue to mirror asynchronously to Hera.
The visible Home preference profile and ranking use this same local journal.

Back up the journal with SQLite's backup API, or copy it while Ariadne is stopped
(including its WAL if present). It contains personal reading preferences and is
ignored by Git. Rolling back the code should preserve this file for later use.

## Transparent starting policy

No new model calls or recommender libraries are required. Matching uses title
and summary words, plus already-cached Signal semantic interests. Configured
interests and the explicitly stated starting topics supply a small baseline.
The declared topics are Thailand, Middle East developments, Trump / US politics,
AI, and practical local AI / hardware; these are editable in the module.

Unseen articles always precede seen articles. Within each group, ranking starts
with cached freshness, capped by current article age, adds 12 for an interest
match, and adds a conservative feedback adjustment bounded to +/-8. A heart
weighs 1, useful .6, dislike -.7, and an open .08. Shared content must include
at least two substantive words; positive similarity must reach .12 and negative
similarity .35. Evidence is divided by 3 plus the number of matching stories.
An article's reaction never boosts that same article. Silence is neutral.

Dislikes do not penalize entire interests, countries, categories or sources.
The positive profile counts distinct currently rated articles, not semantic
join rows. Fewer than three examples remain labelled early evidence.
Positive interest overlap also contributes a small similarity signal (at most
.25); negatives always require the narrower substantive-word match. Missing
fields in later cache snapshots preserve retained semantic and image metadata.

A final diversity pass subtracts 3 per repeated source, 1.5 per category and
up to 16 for overlap with recently selected stories. Wider news remains in the
candidate set. There is no fixed exploration ratio or claim of optimal weights;
these provisional settings need tuning against actual reading experience.
Reasons describe interests, related feedback and freshness instead of showing
rank scores as relevance or confidence percentages.

## Validation

Focused tests cover durable changes/clears and idempotency, stale legacy replay,
unseen priority, no self boost, narrow negatives, bounded related learning,
diversity, retained seen articles and the real cached semantic-match contract.
HTTP integration covers session-bound local saving and cache-only article opens.
The JavaScript card-order regression protects reading position during polling.

Final verification: 27 focused Python tests passed across recommendations, news
snapshot/action integration and Home Signal integration. JavaScript syntax and
the card-order regression passed; Python compilation and Git diff checks passed.
The activated core returned 100 locally ranked cards and seven retained Seen
articles. The canonical HTTPS Home was visually checked for reaction labels,
readable reasons and Discover/Seen switching. No personal test ratings were added.
Unrelated full-suite failures recorded in the earlier checkpoint remain outside
this validation. Warren accepted the compact action row and requested the
October 2 recommendation checkpoint. The next stage is a few days of everyday
testing before tuning the provisional ranking weights.

Design references are credited on About: Informfully Recommenders, Vowpal Wabbit
and Recommenders. No code from those projects is included.
