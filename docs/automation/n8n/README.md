# n8n News Service workflow exports

These JSON files were exported from the active Synology n8n workflows on 2026-11-03 using n8n's built-in **Export JSON** action. The workflow IDs, names, nodes, parameters, connections, settings, and active state are retained. Credential references and pinned/static execution data are omitted; no credential values, tokens, or secrets are stored here.

- `news-service-collector.json` — `VP0Bro3F98I523YD`, **News Service - manual collector (draft)**. Active; retains the hourly Schedule Trigger and Data Table upsert keyed by `video_id`.
- `news-service-publication-snapshot.json` — `HGKHZh3Uuw88fJrl`, **News Service - LAN snapshot (draft)**. Active; reads the existing **News Service Publications** table, sorts by YouTube `published_at` descending with stable source/video tie-breakers, and returns the newest two per source.

The “draft” text is part of each workflow name; the exported `active` field is `true` for both. Credentials remain configured only in n8n.