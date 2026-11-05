# n8n News Service workflow exports

These sanitized workflow definitions document the Synology workflows. Workflow IDs, node parameters and connection topology are retained; credential IDs and execution pins are omitted. Credential names are mapping hints only. Webhook URLs and tokens remain encrypted in n8n and are never stored in these exports. The collector export includes local diagnostic nodes that are not present in the live workflow; see the dated live audit below.

- `news-service-collector.json` — `VP0Bro3F98I523YD`, **News Service - manual collector (draft)**. The live workflow is published as revision `50c07bf0-b88f-410b-a3e8-170af82d8040` and runs every 15 minutes. It collects enabled YouTube sources, maintains the publication cache in **News Service Publications**, keeps the newest two per source, synchronizes the card window and receipt history to Ariadne, and posts eligible cards through the saved destination webhooks.
- `news-service-publication-snapshot.json` — `HGKHZh3Uuw88fJrl`, **News Service - LAN snapshot (draft)**. Reads the publication table, sorts newest first, and returns the newest two per source.

## Feed and candidate diagnostics

The collector saves successful and failed production executions, including manual runs, in n8n. The RSS node's execution output is therefore available when a source feed fails or returns no items. Instance-level execution pruning still controls how long these detailed node inputs and outputs remain available. The extra feed and candidate diagnostic nodes described here remain in the local export and have not been added to the live workflow.

`Build Feed Diagnostics` upserts one compact status row per channel in **News Service Delivery Ledger**. It records the check time and execution ID, entry/error counts, newest feed item, and the last feed error. Stable per-channel keys keep the table bounded as 15-minute checks continue. `Prepare Discord Candidates` also records a stable per-video decision row when a feed item is newer than its source checkpoint, including whether it was skipped and the precise gate that skipped it, or whether it reached the delivery candidate stage. The existing inflight and delivery receipt rows remain authoritative for actual Discord attempts and outcomes. Diagnostic rows use separate key prefixes and are excluded from Ariadne's delivery-history sync.

This lets an operator distinguish a missing/empty RSS result, a candidate rejected by a named eligibility rule, and a candidate that entered Discord delivery but received an error or no receipt. It does not change the collector schedule or send/retry rules.

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

The authenticated n8n editor was used to publish the two live changes. The
local sanitized export contains the optional-node guard, the 15-minute trigger,
and four additional diagnostic nodes; its topology therefore still differs
from the live workflow and must not be described as an exact export of the
published version. Ariadne was not required for this scheduled run or its
Discord delivery.
