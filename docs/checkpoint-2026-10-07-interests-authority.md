# Interests authority checkpoint — 2026-10-07

The existing Interests registry now controls Discover/Home ranking. Priority is
used across its full 0–5 range; disabled interests cannot influence explicit
ranking, and names, descriptions, aliases and semantic settings retain their
existing roles. Arbitrary user-defined interests require no source-code changes.

Removed the hard-coded personal topic list, the local snapshot's competing
preference sort, priority saturation above 1 and the semantic top-six cutoff.
The fast Home snapshot uses the same cached registry as normal selection. Partial
Interest edits preserve the other settings. Existing publisher diversity,
freshness, duplicate handling, feedback, Seen/history and polling remain intact.

Validation: 51 control-plane tests and the JavaScript card-order regression passed.
46 Signal tests passed; two additional failures were independently reproduced on
the unchanged parent source and remain documented in
[news-recommendations.md](news-recommendations.md).

The edited Home selection was inspected using the real 100-card snapshot, current
Hera Interests and semantic evidence, and a backup of the reading journal. The
first 20 cards contained six publishers, none above five cards. An in-memory
priority change demonstrated both score and position changes. Production settings
and history were unchanged.

This checkpoint includes only the reviewed implementation, tests and documentation.
No runtime databases, cached feeds or generated artifacts are included. No services
were restarted and no NAS deployment was performed. GitHub publication preserves
this source checkpoint; deployment remains separate.
