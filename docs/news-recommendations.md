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

No new model calls or recommender libraries are required. The Interests registry
managed by the existing page is the sole source of explicit preferences. Signal
embeds each enabled semantic interest's name, description and aliases, then stores
qualifying matches. All qualifying matches survive; there is no similarity-only
top-six cut. Signal ranking multiplies semantic strength by the full 0–5 priority.

Home joins cached matches by article ID and resolves them against current registry
records by interest ID (name for legacy matches). Disabled/deleted interests are
ignored, cached priorities cannot override current settings, and semantic-disabled
interests use only the existing lexical name/alias fallback. Descriptions retain
their upstream embedding role. The fast snapshot and Seen routes use a durable
local registry projection in the existing Signal briefing cache, so they apply the
same settings without waiting for a network request. Successful page edits update
this projection immediately. No separate default list of personal topics exists.

The article backend supplies freshness/diversity/duplicate-filtered candidates.
NewsBriefingCache merges IDs and preserves images and interaction state in incoming
order; its former source/category/learned-interest preference sort is removed.
Home's existing NewsRecommendations pass owns final selection. The frontend retains
that order on reload and preserves reading position during background polling.

Unseen articles always precede seen articles. Within each group, ranking starts
with cached freshness, capped by current article age, adds
`12 * max(priority * match_strength)` across enabled configured interests, and adds
a conservative feedback adjustment bounded to +/-8. Semantic strength is its
upstream cosine score; an exact name/alias match has strength 1. Priority is used
continuously across 0–5, so equal .8 matches contribute 9.6, 19.2 and 48 points at
priorities 1, 2 and 5. Priority 0 contributes nothing. A heart
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
candidate set. The same diversity pass also caps a publisher at 40% of the
requested projection (minimum three cards) while alternatives exist; if all
publishers reach that cap, it selects the least represented available publisher
before applying the existing scores. When other sources are exhausted it fills
from remaining candidates. This protects the front of a feed without discarding
the archive. There is no fixed exploration ratio or claim of optimal weights;
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


## Interests authority verification — 2026-10-07

51 control-plane tests passed (recommendations, cache, Home snapshot/action and
Signal integration, client cache), and the JavaScript card-order regression passed.
46 Signal tests passed after excluding two failures independently reproduced on
unchanged HEAD: intake-only refresh lacks `mode`, and an old category-projection
test expects an unrelated specialist-feed item to move to Main News Feed. These
failures were not changed as part of this fix. Tests used the bundled Python.

Focused coverage includes priorities 0/.5/1/2/3/4/5, disabled/deleted/stale matches,
semantic-disabled lexical fallback, arbitrary descriptions/aliases, partial edits
preserving settings, more than six matches, publisher dominance with priority 5,
and first-paint selection with cached Interests and no health/network request.
Existing feedback, narrow dislike, Seen/archive, image and polling checks passed.

Representative verification replayed current Hera Interests and semantic evidence
against the real 100-card local snapshot and a SQLite backup of the reading journal,
through `home_today_payload`. Production settings/history were unchanged. Ten
configured interests were present. The first card was “OpenAI drops another batch
of mathematical breakthroughs”, matched to the configured OpenAI interest. The
first 20 cards had six publishers, with no publisher above five cards. Wider news
was still present; Trump/Middle East/Thailand received no implicit interest boost.
A temporary in-memory AI priority change from 1 to 5 raised the AI-tsar article's
score from 12.9 to 60.9 and moved it from position 65 to 54, retaining Seen ordering.

The full 100-card candidate snapshot already contains 70 Al Jazeera articles;
a complete archive projection still contains those after other publishers are
exhausted. The fix controls order and short-feed selection, not upstream supply.
This is verification of the edited code with live input, not a production restart
or NAS deployment. No running service was restarted.
