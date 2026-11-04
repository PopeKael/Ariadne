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
const refs=fixtures.refs;
const $=name=>refs[name];
const $input={all:()=>fixtures.input||[],first:()=>fixtures.input?.[0]};
const $execution={mode:fixtures.mode||'manual'};
const outputs=vm.runInNewContext('(function(){'+fixtures.code+'\n})()',
  { $, $input, $execution, Date, Map, Set, Number, String, Math, JSON });
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
    trigger = node("Schedule Trigger")
    assert trigger["parameters"]["rule"]["interval"]
    upsert = node("Upsert row(s)")["parameters"]["columns"]
    assert upsert["mappingMode"] == "defineBelow"
    assert set(upsert["value"]) == {"video_id", "source_id", "title", "published_at", "summary"}
    assert node("Prepare Active Publication Window")
    assert node("Persist Publication Deduplication")
    assert node("Prune Retired Publications")["parameters"]["operation"] == "deleteRows"
    assert node("Post Discord Card")["credentials"]["discordBotApi"]["name"] == "Ariadne Integration"
    assert node("HTTP Request1")["parameters"]["headerParameters"]["parameters"][0]["name"] == "X-Ariadne-Dry-Run"


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
    disabled = item("Disabled001", "2027-01-16T01:00:00.000Z", enabled=False)
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
    history = [
        sent_other_destination,
        {"delivery_key": uncertain_key, "video_id": uncertain["video_id"], "status": "uncertain", "attempts": 1},
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
 'Code in JavaScript1':{all:()=>f.items.map(json=>({json}))}
}; const $=name=>refs[name]; const $execution={mode:'manual'};
const result=vm.runInNewContext('(function(){'+f.code+'\n})()',
  { $, $execution, Date, Map, Set, Number, String, Math, JSON });
process.stdout.write(JSON.stringify(result));
"""
    result = subprocess.run(
        [shutil.which("node"), "-e", harness, json.dumps({"code": code, "context": context,
         "items": [eligible, historic, disabled, sent, uncertain]})],
        check=True, capture_output=True, text=True, timeout=10,
    )
    rows = json.loads(result.stdout)
    assert [row["json"]["video_id"] for row in rows] == [eligible["video_id"]]
    assert rows[0]["json"]["dry_run"] is True
    assert rows[0]["json"]["state_json"]


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
 'Prepare Registry Context':{first:()=>({json:{delivery_history:[]}})}};
const $=name=>refs[name];const result=vm.runInNewContext('(function(){'+f.code+'\n})()',
  {$,Date,Map,Number,String});process.stdout.write(JSON.stringify(result));
"""
    sorted = subprocess.run([shutil.which("node"), "-e", sort_harness,
        json.dumps({"code": sort_code, "feed": feed})], check=True,
        capture_output=True, text=True, timeout=10)
    publications = json.loads(sorted.stdout)[0]["json"]["publications"]
    assert [x["video_id"] for x in publications] == ["OtherVid001", "ShortVideo1", "RegularVid1"]
