# n8n News Service workflow exports

These sanitized workflow definitions document the Synology workflows. Workflow IDs, node parameters and connection topology are retained; credential IDs and execution pins are omitted. Credential names are mapping hints only. Webhook URLs and tokens remain encrypted in n8n and are never stored in these exports. The collector export describes the intended RSS retry, YouTube page fallback, collection status, and candidate diagnostics. Its live publication state is tracked in the dated audit below.

- `news-service-collector.json` — `VP0Bro3F98I523YD`, **News Service - manual collector (draft)**. Read-only inspection on 2026-11-15 confirmed the live workflow is active at version `f13814b4-d730-4374-b8fc-04e3078c363f` with its 15-minute schedule. Recent runs collect and synchronize publications, but fail later in Discord delivery on an unconfigured destination; see the live audit below. The intended workflow collects enabled YouTube sources, maintains the publication cache in **News Service Publications**, keeps the newest two per source, synchronizes the card window and receipt history to Ariadne, and posts eligible cards through the saved destination webhooks.
- `news-service-publication-snapshot.json` — `HGKHZh3Uuw88fJrl`, **News Service - LAN snapshot (draft)**. Reads the publication table, sorts newest first, and returns the newest two per source.

## Feed recovery and diagnostics

The local export retries RSS reads twice with a short delay. For each failed or empty YouTube feed it fetches the channel's videos, Shorts, and live tabs, then opens candidate watch pages and validates channel identity, video ID, publication time, and public availability before treating a result as a publication. The parser limits recovery to a small recent set per source. Normalized RSS and recovered publications rejoin the same cache, eligibility, and delivery path, so existing publication and Discord receipt keys continue to prevent duplicate posts. The HTTP fallback uses bounded retries and does not depend on the Ariadne sync endpoint. The live workflow's matching fallback nodes were present and executed in run #1270 on 2026-11-15; that confirms the path ran, not that every source returned results.

`Build Feed Diagnostics` upserts one compact status row per channel in **News Service Delivery Ledger**, plus a collection summary. A healthy RSS collection is `ok`; recoveries alongside RSS failures or empty feeds are `degraded`; and a run with no usable RSS or fallback results is `failed`. It records RSS errors, fallback outcome, latest recovered item, collection counts, and last error. This prevents a green n8n execution from being mistaken for a successful collection when every source failed. The node is present in the active workflow and executed in run #1270.

`Build Feed Diagnostics` records the check time and execution ID, entry/error counts, newest feed item, and the last feed error. Stable per-channel keys keep the table bounded as 15-minute checks continue. `Prepare Discord Candidates` also records a stable per-video decision row when a feed item is newer than its source checkpoint, including whether it was skipped and the precise gate that skipped it, or whether it reached the delivery candidate stage. The existing inflight and delivery receipt rows remain authoritative for actual Discord attempts and outcomes. Diagnostic rows use separate key prefixes and are excluded from Ariadne's delivery-history sync.

These diagnostics let an operator distinguish an RSS outage recovered through channel pages, a source on which both collection methods failed, a candidate rejected by a named eligibility rule, and a candidate that entered Discord delivery but received an error or no receipt. The 15-minute Synology schedule is active and triggering as of 2026-11-15. Ariadne synchronization is a separate best-effort branch; an unavailable Ariadne service should not stop cache updates or Discord delivery.

## Discord delivery path

`Prepare Discord Candidates` retains the established activation boundary, source rules, delivery-key deduplication and Components V2 formatter. `Production Delivery Guard` and `Mark Delivery Inflight` precede `Code in JavaScript4`, which parses the persisted `state_json` and exposes its validated `destination`. The three-rule `Switch` then routes on `$json.destination`: `ai_noise`, `thailand_news`, or `news_3cf8b10043ab9e794da82060`. Each branch uses the matching encrypted `discordWebhookApi` credential through `Ariadne Discord Webhook`. All three sender outputs pass through `Build Delivery Receipt` and `Persist Delivery Receipt`. The bot-based `Post Discord Card` node is removed from the active workflow. Manual Ariadne publishing controls and unrelated Discord bot workflows are unchanged.

The sender returns Discord's message ID and safe response metadata to the existing ledger. The route extracts the validated destination from the persisted receipt, then sends through the destination's existing encrypted webhook credential. The Switch node was upgraded through the n8n editor to type version 3.4, and its three rules now evaluate the destination expression without a literal equals sign. All successful sender outputs still pass through the existing receipt ledger. Sent, inflight, uncertain, rejected and pending deliveries remain suppressed; rate-limited and known pre-send failures follow the existing bounded retry rules. Historical rows and the activation timestamp are preserved. Only production-mode executions send or synchronize live; editor/manual runs remain dry runs. Production execution #284 synchronized with `dry_run=false` (1 inserted, 41 duplicates, 8 baseline skips), then delivered two `ai_noise` cards through the saved webhook credential. Discord returned message IDs `1567554369748992194` and `1567554385129771181`; both successful responses were recorded as `sent` with HTTP 200 in the existing ledger. The Ariadne sync node runs before the send/receipt nodes, so those two receipt states are picked up by a later production synchronization. Manual execution #285 succeeded as a dry run, read the 21-row existing delivery ledger including both sent receipts, and did not execute any webhook sender; it produced no sender outputs and did not resend either card. At that time, the published revision was `f5e117a9-726b-4b6b-a5bc-d04b75b68f94`.

## Verification

Before the 2026-11-05 repair, the collector was hourly and ran on Synology independently of Ariadne. The 2026-11-04 22:00:59 scheduled execution (#284) completed successfully with 375 feed items across 25 enabled sources and the 50-publication review window (two per source). The production sync inserted one new publication into Ariadne, counted 41 duplicates and skipped the 8 historical baseline items. Two eligible AI Noise cards received HTTP 200 from Discord and their message IDs were recorded in the persistent ledger. The new Less Bitter card, “AI was responsible for the biggest hack in human history,” appeared first in Ariadne's Following view; “AI Lurches Towards RSI” appeared beneath it. Manual execution #285 completed successfully as a dry run and did not resend either card. Earlier bot failures remain historical rejected receipts and were not replayed.

`control-plane/test_news_n8n_stage4_workflow.py` checks the sanitized workflow's activation gate, delivery deduplication, two-item retention, destination routing, webhook credentials, and receipt-ledger connections. Run it with the focused Ariadne News Service tests.

## Live audit — 2026-11-05

The live Synology database was inspected read-only through the existing SMB
mapping (`V:\n8n\data`). Initially, the active collector was hourly at published
revision `f5e117a9-726b-4b6b-a5bc-d04b75b68f94`. Production executions #303 and
#304 (2026-11-05 07:00 and 08:00 UTC) both failed in `Prepare Discord
Candidates` with `Node 'Code in JavaScript' hasn't been executed`. The cached
registry fallback (`Code in JavaScript3`) had run, while the live registry node
had not. Candidate preparation reads the live registry node without guarding
for that fallback case, so none of the three Discord sender nodes ran in those
executions. The active revision also does not contain the newer feed and
candidate diagnostic nodes at all; those four nodes exist only in the local
export. That is why no `__feed__:` or `__candidate__:` rows were present.

The repair was published in n8n at 2026-11-05 15:55:59 Bangkok time as
revision `50c07bf0-b88f-410b-a3e8-170af82d8040`. It changes the schedule trigger
to every 15 minutes and catches the missing live registry node when the cached
registry branch runs. The next production execution (#305) started at 16:00:59
Bangkok time and completed successfully at 16:05:56 while Ariadne was not
running. It processed 15 delivery candidates: five `ai_noise` and ten
`thailand_news`; all 15 webhook requests returned HTTP 200, Discord message IDs
were recorded, and the corresponding ledger receipts are `sent`.

Discord transport had already been working: before the repair, the receipt
ledger showed 12 successful sends. The failures were in candidate preparation
when the cached registry path was used, before any sender ran. The active live
workflow still does not include the four optional feed/candidate diagnostic
nodes described by the local export.

The authenticated n8n editor was used to publish the optional-node guard and
15-minute trigger. Ariadne was not required for that scheduled run or its
Discord delivery.

## Live audit — 2026-11-15

The Ariadne Home Following view loaded 52 cached videos. An earlier check
mistook the maximum `publications.collected_at` value for a sync heartbeat. It
is the insert time of the newest publication, so it does not show when the last
duplicate-only synchronization occurred.

Read-only inspection of the active n8n SQLite database confirmed workflow
`VP0Bro3F98I523YD` is active at version
`f13814b4-d730-4374-b8fc-04e3078c363f`. The schedule trigger continues to run
every 15 minutes. In execution #1270, collection, publication-window
maintenance, and the Ariadne sync node all completed. The sync returned
`ok=true`, `inserted=0`, `duplicates=52`, and `baseline_skipped=2`; the existing
Following set was synchronized, with no new publications found in that run.

Execution #1270 then failed at `Code in JavaScript4` with
`Unknown delivery destination`. Its five delivery candidates included one
`news_d997167fc9cb243735d0fcc1` item, which is the Ariadne destination **Videos
to Watch**. The active validator and Switch routes accept only `ai_noise`,
`thailand_news`, and `news_3cf8b10043ab9e794da82060` (**Wazza's Video Blog**).
Because the validation node maps the whole batch and throws on this destination,
the run stops before any candidate reaches a sender. The latest 20 executions
were marked `error`.

Therefore the publication collection and Following sync were running at this
check; the failed stage was Discord delivery. The live workflow has no verified
sender/credential mapping for **Videos to Watch**, so no route or credential was
changed during this audit.

## RSS outage audit and fallback — 2026-11-12

Production executions between 13:30 and 14:30 Bangkok time reported YouTube
RSS HTTP 404 responses across all 27 configured sources. A later scheduled run
recovered without a configuration change and delivered Pope Kael's missed
video at 15:01 Bangkok time. At 15:06, matched feed requests returned HTTP 200
from both Synology and the Windows desktop, with YouTube RSS server headers and
valid Atom entries. This points to a transient YouTube RSS outage rather than a
persistent Synology IP block or malformed local request. The failed 404 bodies
were not preserved in those executions, so the exact upstream response page
could not be compared retrospectively. FreeTube maintainers have also
documented recurring YouTube-wide RSS outages in 2026.

The recovery implementation in `news-service-collector.json` adds bounded RSS
and page-request retries, a YouTube channel/watch-page parser fallback, source
and collection status rows, and a placeholder item so the publication merge
still executes when RSS returns no normalized items. The 2026-11-15 live audit
above confirms these fallback and diagnostic nodes are present in the active
workflow and ran in execution #1270. The remaining failure is the separate
Discord destination routing gap recorded in that audit.
