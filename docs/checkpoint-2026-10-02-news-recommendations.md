# Personal news recommendations checkpoint

Date: 2026-10-02 (Asia/Bangkok).

Warren accepted the first recommendation implementation and the single-row
card controls, and requested this checkpoint and GitHub push. The next stage
is a few days of normal reading to evaluate and tune the balance.

## Included

- Three compact reactions: 👍 Useful, ❤️ More like this, 👎 Not interested.
  Hover descriptions, keyboard controls and pressed state; click again to clear.
- Durable local SQLite journal keyed by article ID, immutable reaction events,
  idempotent retries and retained article snapshots. Latest reaction wins;
  clearing removes preference evidence and preserves seen status.
- One Home ranking/profile owner. Legacy Hera feedback imports once per article,
  while local changes and clears override stale upstream snapshots. Existing
  Hera history is preserved. New reactions persist locally; cache-only article
  opens continue to mirror asynchronously to Hera.
- Conservative positive learning from content and cached semantic interests.
  Opens are weaker evidence; silence is neutral. Narrow dislikes avoid broad
  country, source or category penalties. Reactions never boost the same story.
- Hard unseen-before-seen ordering on deliberate refresh, retained Seen view,
  readable reasons and a final diversity pass. No new model or GPU workload.
- Initial cached-card paint and later activity responses use the same ranking.
  Background polling preserves existing card identity, order and scroll position.
- Reactions, TLDR, info and discussion controls align on one row. Desktop keeps
  two card columns; narrow feed containers use one column to fit the controls.
- About records the major milestone, credits the three inspiration projects,
  and labels the first version as entering everyday testing.

Policy details and backup guidance: [news-recommendations.md](news-recommendations.md).
The SQLite runtime journal is ignored by Git and is not part of the push.
KnowledgeVault is a separate repository, outside this checkpoint.

## Validation and acceptance

- 27 focused Python tests passed: recommendation behaviour, Home news snapshot
  and action HTTP integration, and Home Signal integration.
- JavaScript syntax check and the card-order regression passed.
- Python compilation and Git diff checks passed.
- The activated core returned 100 ranked cards and seven retained Seen stories.
  Canonical HTTPS Home was visually verified for hints, readable reasons,
  Discover/Seen switching and a single action row at 1920px desktop width.
- Warren accepted the live layout: “that looks clean.”
- Unrelated full-suite failures recorded in the previous checkpoint were not
  addressed here; this does not claim the entire repository suite is green.

## Next: a few days of reading

Watch whether related future stories become useful, stale/seen stories stay
behind new stories on refresh, repeated subjects and sources remain restrained,
and wider news is still discoverable. Check changed and cleared reactions across
reloads. Early evidence should remain modest until several different stories
support an interest. Use actual experience to tune weights; no fixed mix or
optimal recommendation quality is claimed yet.

No automated monitoring or extra background inference is scheduled by this
checkpoint. Original Knowledge Vault inspiration attribution remains pending
confirmation of the person's name and source.
