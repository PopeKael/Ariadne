"""Regression checks for the sanitized Synology News Service workflow export."""
import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "docs/automation/n8n/news-service-collector.json"


def workflow():
    return json.loads(WORKFLOW.read_text(encoding="utf-8-sig"))


def node(name):
    return next(item for item in workflow()["nodes"] if item["name"] == name)


def run_node_js(name, code, inputs):
    executable = shutil.which("node")
    if not executable:
        raise RuntimeError("Node.js is required to exercise the exported n8n Code nodes")
    harness = r"""
const vm=require('node:vm');
const fixtures=JSON.parse(process.argv[1]);
const refs=fixtures.refs||{};
if (fixtures.candidate) refs['Prepare Discord Candidates']={
 itemMatching:()=>({json:fixtures.candidate}), all:()=>[{json:fixtures.candidate}]
};
const $=name=>refs[name];
const $input={all:()=>fixtures.input||[],first:()=>fixtures.input?.[0]};
const $execution={mode:fixtures.mode||'manual'};
const $json=fixtures.output||{};
const $itemIndex=0;
const outputs=vm.runInNewContext('(function(){'+fixtures.code+'\n})()',
  { $, $input, $execution, $json, $itemIndex, Date, Map, Set, Number, String, Math, JSON });
process.stdout.write(JSON.stringify(outputs));
"""
    fixture = {"code": code, **inputs}
    result = subprocess.run(
        [executable, "-e", harness, json.dumps(fixture)],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return json.loads(result.stdout)


def test_export_keeps_schedule_and_active_table_schema():
    data = workflow()
    assert data["id"] == "VP0Bro3F98I523YD"
    assert data["settings"]["saveDataSuccessExecution"] == "all"
    assert data["settings"]["saveDataErrorExecution"] == "all"
    assert data["settings"]["saveManualExecutions"] is True
    exported = json.dumps(data)
    assert "discord.com/api/webhooks" not in exported
    assert "webhookUri" not in exported
    trigger = node("Schedule Trigger")
    assert trigger["parameters"]["rule"]["interval"]
    upsert = node("Upsert row(s)")["parameters"]["columns"]
    assert upsert["mappingMode"] == "defineBelow"
    assert set(upsert["value"]) == {"video_id", "source_id", "title", "published_at", "summary"}
    assert node("Prepare Active Publication Window")
    assert node("Persist Publication Deduplication")
    assert node("Prune Retired Publications")["parameters"]["operation"] == "deleteRows"
    assert "Post Discord Card" not in {item["name"] for item in workflow()["nodes"]}
    switch = node("Switch")
    assert switch["typeVersion"] == 3.4
    rules = switch["parameters"]["rules"]["values"]
    assert [rule["conditions"]["conditions"][0]["rightValue"] for rule in rules] == [
        "ai_noise", "thailand_news", "news_3cf8b10043ab9e794da82060"
    ]
    assert all(rule["conditions"]["conditions"][0]["leftValue"] == "={{$json.destination}}" for rule in rules)
    assert all(rule["conditions"]["options"]["version"] == 3 for rule in rules)
    connections = workflow()["connections"]
    assert connections["Mark Delivery Inflight"]["main"][0][0]["node"] == "Code in JavaScript4"
    extractor = node("Code in JavaScript4")["parameters"]["jsCode"]
    assert "JSON.parse(json.state_json)" in extractor
    assert "json: { ...json, destination }" in extractor
    assert connections["Code in JavaScript4"]["main"][0][0]["node"] == "Switch"
    for name, credential in (
        ("Ariadne Discord Webhook", "Discord Webhook account 3"),
        ("Ariadne Discord Webhook1", "Discord Webhook account 2"),
        ("Ariadne Discord Webhook2", "Discord Webhook account 4"),
    ):
        sender = node(name)
        assert sender["type"] == "n8n-nodes-ariadne-discord.ariadneDiscordWebhook"
        assert sender["credentials"]["discordWebhookApi"]["name"] == credential
        assert "id" not in sender["credentials"]["discordWebhookApi"]
        assert connections[name]["main"][0][0]["node"] == "Build Delivery Receipt"
    assert connections["Build Delivery Receipt"]["main"][0][0]["node"] == "Persist Delivery Receipt"
    assert connections["RSS Read"]["main"][0][1]["node"] == "Build Feed Diagnostics"
    assert connections["Build Feed Diagnostics"]["main"][0][0]["node"] == "Persist Feed Diagnostics"
    assert connections["Prepare Discord Candidates"]["main"][0][0]["node"] == "Route Candidate Diagnostics"
    assert connections["Route Candidate Diagnostics"]["main"][0][0]["node"] == "Production Delivery Guard"
    assert connections["Route Candidate Diagnostics"]["main"][1][0]["node"] == "Persist Candidate Diagnostics"
    sync_header = node("HTTP Request1")["parameters"]["headerParameters"]["parameters"][0]
    assert sync_header["name"] == "X-Ariadne-Dry-Run"
    assert sync_header["value"] == "=  {{$execution.mode !== 'production' ? 'true' : 'false'}}"


def test_candidate_gate_excludes_activation_history_disabled_and_prior_delivery():
    code = node("Prepare Discord Candidates")["parameters"]["jsCode"]
    activation = "2027-01-15T00:00:00.000Z"
    stage3 = "2027-01-01T00:00:00.000Z"
    checkpoint = "2027-01-14T00:00:00.000Z"

    def item(video_id, timestamp, **extra):
        return {
            "source_id": "UC" + "A" * 22,
            "video_id": video_id,
            "title": "Card " + video_id,
            "published_at": timestamp,
            "creator": "Channel",
            "destination": "news",
            "discord_channel_id": "123456789012345678",
            "enabled": True,
            "stage3_activated_at": stage3,
            "checkpoint_published_at": checkpoint,
            "original_url": "https://www.youtube.com/watch?v=" + video_id,
            "image_url": "https://i.ytimg.com/vi/" + video_id + "/hqdefault.jpg",
            **extra,
        }

    eligible = item("NewShort001", "2027-01-16T00:00:00.000Z")
    # YouTube IDs are 11 characters; the fixtures remain valid for the workflow.
    eligible["video_id"] = "NewShort01A"
    historic = item("OldVideo01A", "2027-01-14T23:00:00.000Z")
    disabled = item("Disabled001", "2027-01-16T01:00:00.000Z", source_id="UC" + "B" * 22, enabled=False)
    sent_video = "SentVideo001"
    sent_video = sent_video[:11]
    sent = item(sent_video, "2027-01-16T02:00:00.000Z")
    sent_other_destination = {
        "delivery_key": "old-destination",
        "video_id": sent_video,
        "status": "sent",
        "attempts": 1,
    }
    uncertain = item("Uncertain001"[:11], "2027-01-16T03:00:00.000Z")
    uncertain_key = f"{uncertain['video_id']}:{uncertain['destination']}:{uncertain['discord_channel_id']}"
    failed = item("Failed001"[:11], "2027-01-16T04:00:00.000Z")
    failed["checkpoint_published_at"] = "2027-01-17T00:00:00.000Z"
    failed_key = f"{failed['video_id']}:{failed['destination']}:{failed['discord_channel_id']}"
    exhausted = item("FailedToo01"[:11], "2027-01-16T05:00:00.000Z")
    exhausted_key = f"{exhausted['video_id']}:{exhausted['destination']}:{exhausted['discord_channel_id']}"
    history = [
        sent_other_destination,
        {"delivery_key": uncertain_key, "video_id": uncertain["video_id"], "status": "uncertain", "attempts": 1},
        {"delivery_key": failed_key, "video_id": failed["video_id"], "status": "failed", "attempts": 1},
        {"delivery_key": exhausted_key, "video_id": exhausted["video_id"], "status": "failed", "attempts": 3},
    ]
    # Use an activation instant immediately before the synthetic publication dates.
    context = {
        "stage4_activated_at": activation,
        "publication_ids": [historic["video_id"]],
        "delivery_history": history,
    }
    # JSON fixtures cannot carry callbacks; this harness binds the n8n node references directly.
    harness = r"""
const vm=require('node:vm'); const f=JSON.parse(process.argv[1]);
const refs={
 'Prepare Registry Context':{first:()=>({json:f.context})},
 'Get Existing Publications':{all:()=> (f.cached||[]).map(json=>({json}))},
 'Code in JavaScript':{all:()=> (f.sources||[]).map(json=>({json}))},
 'Code in JavaScript3':{all:()=>{throw new Error('node did not execute')}},
 'Code in JavaScript1':{all:()=>f.items.map(json=>({json}))}
}; const $=name=>refs[name]; const $execution={mode:f.mode||'manual'};
const result=vm.runInNewContext('(function(){'+f.code+'\n})()',
  { $, $execution, Date, Map, Set, Number, String, Math, JSON });
process.stdout.write(JSON.stringify(result));
"""
    result = subprocess.run(
        [shutil.which("node"), "-e", harness, json.dumps({"code": code, "context": context,
         "items": [eligible, historic, disabled, sent, uncertain, failed, exhausted],
         "cached": [failed], "sources": [eligible, historic, disabled, sent, uncertain, failed, exhausted]})],
        check=True, capture_output=True, text=True, timeout=10,
    )
    rows = json.loads(result.stdout)
    candidates = [row["json"] for row in rows if row["json"]["record_type"] == "candidate"]
    diagnostics = [row["json"] for row in rows if row["json"]["record_type"] == "diagnostic"]
    assert [row["video_id"] for row in candidates] == [failed["video_id"], eligible["video_id"]]
    assert candidates[1]["dry_run"] is True
    assert candidates[1]["state_json"]
    assert json.loads(candidates[1]["state_json"])["payload"] == candidates[1]["payload"]
    assert candidates[0]["attempt"] == 2
    skipped = {json.loads(row["state_json"])["video_id"]: json.loads(row["state_json"])
               for row in diagnostics if json.loads(row["state_json"])["decision"] == "skipped"}
    assert skipped[disabled["video_id"]]["reason"] == "source_disabled"
    assert skipped[uncertain["video_id"]]["reason"] == "prior_uncertain"

    cached_retry = subprocess.run(
        [shutil.which("node"), "-e", harness, json.dumps({"code": code, "context": context,
         "items": [], "cached": [failed], "sources": [failed]})],
        check=True, capture_output=True, text=True, timeout=10,
    )
    cached_rows = json.loads(cached_retry.stdout)
    cached_candidates = [row["json"] for row in cached_rows if row["json"]["record_type"] == "candidate"]
    assert [row["video_id"] for row in cached_candidates] == [failed["video_id"]]
    assert cached_candidates[0]["attempt"] == 2

    production = subprocess.run(
        [shutil.which("node"), "-e", harness, json.dumps({"code": code, "context": context,
         "items": [eligible], "sources": [eligible], "mode": "production"})],
        check=True, capture_output=True, text=True, timeout=10,
    )
    production_rows = json.loads(production.stdout)
    production_candidates = [row["json"] for row in production_rows if row["json"]["record_type"] == "candidate"]
    assert production_candidates[0]["dry_run"] is False


def test_receipt_retries_only_known_preflight_failures():
    code = node("Build Delivery Receipt")["parameters"]["jsCode"]
    candidate = {"delivery_key": "video:news:123456789012345678", "video_id": "video", "destination": "news",
                 "source_id": "UC" + "A" * 22, "published_at": "2027-01-16T00:00:00Z",
                 "discord_channel_id": "123456789012345678", "payload": {"components": []},
                 "attempt": 1, "started_at": "2027-01-16T00:00:00Z"}
    preflight = run_node_js("Build Delivery Receipt", code, {"candidate": candidate,
        "output": {"error": 'The value in the "JSON Body" field is not valid JSON'}})
    assert json.loads(preflight["json"]["state_json"])["status"] == "failed"
    ambiguous = run_node_js("Build Delivery Receipt", code, {"candidate": candidate,
        "output": {"error": {"message": "socket timed out"}}})
    assert json.loads(ambiguous["json"]["state_json"])["status"] == "uncertain"
    sent = run_node_js("Build Delivery Receipt", code, {"candidate": candidate,
        "output": {"statusCode": 200, "body": {"id": "discord-message-1", "timestamp": "2027-01-16T00:00:01Z"}}})
    sent_state = json.loads(sent["json"]["state_json"])
    assert sent_state["status"] == "sent"
    assert sent_state["discord_message_id"] == "discord-message-1"
    assert sent_state["payload"] == candidate["payload"]


def test_feed_diagnostics_keep_source_level_errors_and_empty_feeds():
    code = node("Build Feed Diagnostics")["parameters"]["jsCode"]
    channel_a = "UC" + "A" * 22
    channel_b = "UC" + "B" * 22
    sources = [
        {"source_id": channel_a, "name": "Channel A", "creator": "Channel A"},
        {"source_id": channel_b, "name": "Channel B", "creator": "Channel B"},
    ]
    harness = r"""
const vm=require('node:vm');const f=JSON.parse(process.argv[1]);
const refs={
 'Code in JavaScript':{all:()=>f.sources.map(json=>({json})),itemMatching:()=>({json:f.sources[0]})},
 'Code in JavaScript3':{all:()=>{throw new Error('fallback not used')}},
 'Get Delivery Ledger':{all:()=>f.previous.map(json=>({json}))}
}; const $=name=>refs[name];const $input={all:()=>f.items.map(json=>({json}))};
const $execution={id:'run-123',mode:'production'};
const result=vm.runInNewContext('(function(){'+f.code+'\n})()',{$,$input,$execution,Date,Map,Set,Number,String,Array,JSON});
process.stdout.write(JSON.stringify(result));
"""
    entries = [{"id": "yt:video:abcdefghijk", "title": "New video", "isoDate": "2027-01-16T00:00:00Z"},
               {"error": {"message": "HTTP 503"}}]
    result = subprocess.run(
        [shutil.which("node"), "-e", harness,
         json.dumps({"code": code, "sources": sources, "items": entries, "previous": []})],
        check=True, capture_output=True, text=True, timeout=10,
    )
    rows = {json.loads(row["json"]["state_json"])["source_id"]:
            json.loads(row["json"]["state_json"]) for row in json.loads(result.stdout)}
    assert rows[channel_a]["status"] == "feed_error"
    assert rows[channel_a]["entry_count"] == 1
    assert rows[channel_a]["error_count"] == 1
    assert rows[channel_a]["newest_video_id"] == "abcdefghijk"
    assert rows[channel_b]["status"] == "empty_feed"
    assert rows[channel_b]["entry_count"] == 0
    assert rows[channel_a]["execution_id"] == "run-123"


def test_retention_and_feed_sort_keep_two_per_source_and_newest_first():
    active_code = node("Prepare Active Publication Window")["parameters"]["jsCode"]
    rows = [
        {"source_id": "UC" + "A" * 22, "video_id": "OldVideo001", "published_at": "2025-01-01T00:00:00Z"},
        {"source_id": "UC" + "A" * 22, "video_id": "NewestVid01", "published_at": "2025-01-03T00:00:00Z"},
        {"source_id": "UC" + "A" * 22, "video_id": "ShortVideo1", "published_at": "2025-01-02T00:00:00Z"},
    ]
    harness = r"""
const vm=require('node:vm');const f=JSON.parse(process.argv[1]);
const $=name=>({'Code in JavaScript1':{all:()=>f.rows.map(json=>({json}))}}[name]);
const result=vm.runInNewContext('(function(){'+f.code+'\n})()',{ $, Date, Map, Set, Number, String });
process.stdout.write(JSON.stringify(result));
"""
    result = subprocess.run([shutil.which("node"), "-e", harness,
        json.dumps({"code": active_code, "rows": rows})], check=True,
        capture_output=True, text=True, timeout=10)
    active = json.loads(result.stdout)
    assert [x["json"]["video_id"] for x in active] == ["NewestVid01", "ShortVideo1"]

    retention_code = node("Prepare Publication Retention")["parameters"]["jsCode"]
    retention_harness = r"""
const vm=require('node:vm');const f=JSON.parse(process.argv[1]);
const refs={
 'Get Existing Publications':{all:()=>f.prior.map(json=>({json}))},
 'Prepare Active Publication Window':{all:()=>f.current.map(json=>({json}))},
 'Code in JavaScript1':{all:()=>f.collected.map(json=>({json}))},
 'Prepare Registry Context':{first:()=>({json:f.context})}
}; const $=name=>refs[name];
const result=vm.runInNewContext('(function(){'+f.code+'\n})()',{ $, Date, Map, Set, Number, String, Math });
process.stdout.write(JSON.stringify(result));
"""
    result = subprocess.run([shutil.which("node"), "-e", retention_harness,
        json.dumps({"code": retention_code, "prior": rows, "current": rows[1:], "collected": rows,
                    "context": {"publication_ids": [], "delivery_history": []}})], check=True,
        capture_output=True, text=True, timeout=10)
    state = json.loads(result.stdout)[0]["json"]
    assert set(state["stale_video_ids"]) == {"OldVideo001"}
    assert set(state["video_ids"]) == {row["video_id"] for row in rows}


def test_cached_registry_fallback_works_without_ariadne_and_exports_sorted_cards():
    fallback_code = node("Code in JavaScript3")["parameters"]["jsCode"]
    snapshot = {
        "version": 1,
        "stage3_activated_at": "2025-01-01T00:00:00Z",
        "sources": [
            {"id": "UC" + "A" * 22, "enabled": True, "video_id": "NewestVid01",
             "published_at": "2025-01-03T00:00:00Z", "destination": "news", "discord_channel_id": "123456789012345678"},
            {"id": "UC" + "B" * 22, "enabled": False, "video_id": "Disabled001",
             "published_at": "2025-01-03T00:00:00Z", "destination": "news", "discord_channel_id": "123456789012345678"},
        ],
    }
    harness = r"""
const vm=require('node:vm');const f=JSON.parse(process.argv[1]);
const $input={all:()=>[{json:{source_id:'__registry__',state_json:JSON.stringify(f.snapshot)}}]};
const result=vm.runInNewContext('(function(){'+f.code+'\n})()',{$input,Date,String,Array});
process.stdout.write(JSON.stringify(result));
"""
    result = subprocess.run([shutil.which("node"), "-e", harness,
        json.dumps({"code": fallback_code, "snapshot": snapshot})], check=True,
        capture_output=True, text=True, timeout=10)
    sources = json.loads(result.stdout)
    assert len(sources) == 1
    assert sources[0]["json"]["source_id"] == snapshot["sources"][0]["id"]
    assert sources[0]["json"]["discord_channel_id"] == "123456789012345678"

    sort_code = node("Code in JavaScript2")["parameters"]["jsCode"]
    feed = [
        {"source_id": "UC" + "A" * 22, "video_id": "RegularVid1", "title": "Regular", "published_at": "2025-01-02T00:00:00Z"},
        {"source_id": "UC" + "A" * 22, "video_id": "ShortVideo1", "title": "Short", "published_at": "2025-01-03T00:00:00Z"},
        {"source_id": "UC" + "B" * 22, "video_id": "OtherVid001", "title": "Other", "published_at": "2025-01-04T00:00:00Z"},
    ]
    sort_harness = r"""
const vm=require('node:vm');const f=JSON.parse(process.argv[1]);
const refs={'Code in JavaScript1':{all:()=>f.feed.map(json=>({json}))},
     'Get Existing Publications':{all:()=> (f.existing||[]).map(json=>({json}))},
 'Prepare Registry Context':{first:()=>({json:{delivery_history:f.delivery_history||[]}})}};
const $=name=>refs[name];const result=vm.runInNewContext('(function(){'+f.code+'\n})()',
  {$,Date,Map,Number,String,Set});process.stdout.write(JSON.stringify(result));
"""
    sorted = subprocess.run([shutil.which("node"), "-e", sort_harness,
        json.dumps({"code": sort_code, "feed": feed, "existing": [], "delivery_history": [
            {"delivery_id":"legacy#1","video_id":"nHf-JZVXDxM","destination":"thailand_news","status":"failed","started_at":"2026-11-04T03:01:16.867Z"},
            {"delivery_id":"valid#1","video_id":"NewShort01A","destination":"news","status":"sent","started_at":"2026-11-04T03:02:16.867Z"},
            {"delivery_id":"rejected#2","video_id":"OldVideo001","destination":"news","status":"rejected","started_at":"2026-11-04T03:02:17.867Z"},
            {"delivery_id":"bad-timestamp","video_id":"NewShort01A","destination":"news","status":"sent","started_at":"2026-11-04T03:02:16.867"}
        ]})], check=True,
        capture_output=True, text=True, timeout=10)
    publications = json.loads(sorted.stdout)[0]["json"]["publications"]
    assert [x["video_id"] for x in publications] == ["OtherVid001", "ShortVideo1", "RegularVid1"]
    receipts = json.loads(sorted.stdout)[0]["json"]["delivery_history"]
    assert [x["delivery_id"] for x in receipts] == ["valid#1", "rejected#2"]
