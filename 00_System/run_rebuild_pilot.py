"""Bounded advisory pilot for rebuild-v1. It never changes canonical source Markdown or legacy outputs."""
from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vault_rebuild import build_manifest, manifest_bytes, write_manifest

PILOT_SIZE = 12
POST_SNAPSHOT_NAMES = {
    "Checking out a friends AI created game2026-07-20T13_44_10+07_002026-07-19T23_35_35-07_00[[Garage Alchemy with Pope Kael]].md",
    "Controversial road rule axed for millions of Aussie drivers from today_ 'Unnecessary'.md",
    "Correspondence - Patreon support contact process2026-07-20T16_47_16+07_00.md",
    "Discord Ownership Breakdown2026-07-20T21_58_41+07_00.md",
    "K80 vs M4000 GPUs2026-07-18T12_40_54+07_00.md",
    "Liver Flukes in Thailand2026-07-18T12_26_31+07_00.md",
    "My Local System - Xpra Setup for MSI2026-07-21T12_56_57+07_00.md",
    "Now, even Russia's most elite hackers are using Clickfix to infect devices2026-07-20T22_08_37+07_002026-07-16T19_28_33+00_00[[Dan Goodin]].md",
    "Penis Shrine Thailand2026-07-11T17_42_32+07_00.md",
    "Pope Kael TV - I Asked for Snow, It Answered in Farsi2026-07-19T13_55_34+07_00.md",
    "Thailand Life Channel Mode v3 - 2026-07-09 12.45.md",
    "The Patreon Tribe - Bangkok Tourist Spots2026-07-14T12_29_20+07_00.md",
    "The Rabbit Hole - Currency and Conflict Dynamics2026-07-12T23_33_49+07_00.md",
    "The Rabbit Hole - Thai Signage Tax Rates2026-07-14T12_24_11+07_00.md",
    "URGENT_ Thailand Cannabis Laws Just Changed (July 2026 Update)2026-07-20T16_56_56+07_002026-07-20T00_00_26-07_00[[Anglo Siam Legal]].md",
    "Wordpress SEO Workflow - Branch · Thailand Vlog SEO Template2026-07-12T11_41_08+07_00.md",
    "_Fixing_ Thai Company Issues Associated with Nominees_2026-07-16T19_15_31+07_002026-07-16T01_45_28-07_00[[Integrity Legal Thailand]].md",
    "Arcoxia and Omeprazole Use2026-07-18T12_11_17+07_00.md",
}


def load_domains(root: Path) -> list[str]:
    data = json.loads((root / "00_System" / "DomainVocabulary.json").read_text(encoding="utf-8"))
    return sorted(str(item["name"]) for item in data.get("domains", []))


def tokens(value: str) -> set[str]:
    return set(re.findall(r"[\w-]{3,}", value.lower()))


def select_pilot(root: Path, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    readable = [record for record in records if not record.get("read_error")]
    texts = {record["relative_path"]: (root / record["relative_path"]).read_text(encoding="utf-8-sig") for record in readable}
    selected: list[dict[str, Any]] = []
    used: set[str] = set()

    def take(label: str, predicate: Any) -> None:
        for record in sorted(readable, key=lambda item: item["relative_path"].lower()):
            path = record["relative_path"]
            if path in used or not predicate(record, texts[path]):
                continue
            selected.append({**record, "pilot_reason": label})
            used.add(path)
            return

    take("ordinary chat Markdown", lambda r, t: r["source_type"] in {"chatgpt", "gemini", "openwebui"})
    take("front matter", lambda r, t: not any("front_matter" in warning for warning in r["parse_warnings"]))
    take("external links", lambda r, t: len(re.findall(r"https?://", t)) >= 2)
    take("people or handles", lambda r, t: bool(re.search(r"(?<!\w)@[A-Za-z][\w-]{2,}", t)))
    take("project material", lambda r, t: "ariadne" in (r["title"] + "\n" + t[:5000]).lower())
    take("technical material", lambda r, t: bool(re.search(r"\b(MCP|Ollama|Python|GPU|Linux|Xpra|Docker|PowerShell)\b", r["title"] + "\n" + t, re.I)))
    take("previously failed source", lambda r, t: r["workflow_state"] == "failed")
    take("post-snapshot source", lambda r, t: Path(r["relative_path"]).name in POST_SNAPSHOT_NAMES)
    for record in sorted(readable, key=lambda item: item["relative_path"].lower()):
        if len(selected) >= PILOT_SIZE:
            break
        if record["relative_path"] not in used:
            selected.append({**record, "pilot_reason": "representative fill"})
            used.add(record["relative_path"])
    if len(selected) != PILOT_SIZE:
        raise RuntimeError(f"Expected {PILOT_SIZE} pilot records, found {len(selected)}.")
    return selected


def schema(domains: list[str]) -> dict[str, Any]:
    names = {"type": "array", "items": {"type": "string"}, "maxItems": 3}
    strings = {"type": "array", "items": {"type": "string"}, "maxItems": 8}
    return {"type": "object", "additionalProperties": False, "properties": {
        "proposed_domains": names, "summary": {"type": "string"}, "entities": strings, "people": strings,
        "concepts": strings, "links": strings, "confidence": {"type": "number"}, "notes": {"type": "string"},
    }, "required": ["proposed_domains", "summary", "entities", "people", "concepts", "links", "confidence", "notes"]}


def model_request(record: dict[str, Any], text: str, domains: list[str]) -> dict[str, Any]:
    excerpt = text[:8000]
    prompt = (
        "Analyse one KnowledgeVault source. Return only the schema-constrained JSON object. "
        "This is advisory enrichment: do not create records or assume aliases. Use only supplied domains.\n\n"
        f"SOURCE ID: {record['stable_source_id']}\nTITLE: {record['title']}\nTYPE: {record['source_type']}\n"
        f"ALLOWED DOMAINS: {json.dumps(domains, ensure_ascii=False)}\n\nSOURCE EXCERPT:\n{excerpt}"
    )
    # GPT-OSS on the installed Ollama build returns an empty final field through
    # /api/generate. /api/chat with a JSON Schema is the verified adapter path.
    # Do not set think: GPT-OSS defaults to its supported reasoning level while
    # still returning the schema-constrained final answer in message.content.
    return {"model": "gpt-oss:20b", "messages": [{"role": "user", "content": prompt}], "stream": False,
            "format": schema(domains), "options": {"temperature": 0}}


def invoke_ollama(body: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None, str | None]:
    encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request("http://localhost:11434/api/chat", data=encoded,
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read().decode("utf-8", errors="replace")
        return raw, json.loads(raw), None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return None, None, str(exc)


def final_content(envelope: dict[str, Any] | None) -> tuple[str, str]:
    """Return GPT-OSS final content and separately captured reasoning."""
    message = envelope.get("message") if isinstance(envelope, dict) else None
    if not isinstance(message, dict):
        return "", ""
    return str(message.get("content") or ""), str(message.get("thinking") or "")


def validate_proposal(value: Any, domains: list[str]) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(value, dict):
        return None, "response_is_not_object"
    required = {"proposed_domains", "summary", "entities", "people", "concepts", "links", "confidence", "notes"}
    if set(value) != required:
        return None, "schema_keys_mismatch"
    if not isinstance(value["proposed_domains"], list) or any(item not in domains for item in value["proposed_domains"]):
        return None, "invalid_domain_proposal"
    for key in ("entities", "people", "concepts", "links"):
        if not isinstance(value[key], list) or not all(isinstance(item, str) for item in value[key]):
            return None, f"invalid_{key}"
    if not isinstance(value["summary"], str) or not isinstance(value["notes"], str) or not isinstance(value["confidence"], (int, float)):
        return None, "invalid_scalar_field"
    value["proposed_domains"] = list(dict.fromkeys(value["proposed_domains"]))[:3]
    for key in ("entities", "people", "concepts", "links"):
        value[key] = list(dict.fromkeys(item.strip() for item in value[key] if item.strip()))[:8]
    return value, None


def lexical_search(catalogue: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    query_tokens = tokens(query)
    scored = []
    for record in catalogue:
        haystack = " ".join([record["title"], record.get("summary", ""), record.get("excerpt", ""), " ".join(record.get("proposed_domains", []))])
        score = len(query_tokens & tokens(haystack))
        if score:
            scored.append({"stable_source_id": record["stable_source_id"], "title": record["title"],
                           "relative_path": record["relative_path"], "score": score})
    return sorted(scored, key=lambda item: (-item["score"], item["relative_path"]))[:5]


def write_review(path: Path, record: dict[str, Any], outcome: dict[str, Any]) -> None:
    lines = ["# Rebuild v1 Pilot Review", "", f"Source ID: `{record['stable_source_id']}`", f"Source: `{record['relative_path']}`",
             f"Selection: {record['pilot_reason']}", "", f"Outcome: {outcome['status']}"]
    if outcome.get("reason"):
        lines.append(f"Reason: {outcome['reason']}")
    proposal = outcome.get("proposal")
    if proposal:
        lines.extend(["", "## Advisory proposals", f"Domains accepted structurally: {', '.join(proposal['proposed_domains']) or 'None'}",
                      f"Summary accepted structurally: {proposal['summary']}",
                      f"Entities held for review: {', '.join(proposal['entities']) or 'None'}",
                      f"People held for review: {', '.join(proposal['people']) or 'None'}",
                      f"Concepts held for review: {', '.join(proposal['concepts']) or 'None'}",
                      f"Links held for review: {', '.join(proposal['links']) or 'None'}"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vault", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    root = args.vault.resolve()
    output = root / "00_System" / "Data" / "rebuild-v1"
    reviews = output / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(root)
    write_manifest(manifest, output / "source-manifest.json")
    if manifest["validation"]["unreadable_count"] or manifest["validation"]["duplicate_stable_ids"] or manifest["validation"]["duplicate_content_hashes"]:
        raise RuntimeError("Manifest validation failed; pilot not started.")
    domains = load_domains(root)
    pilot = select_pilot(root, manifest["records"])
    captures_path = output / "model-captures.jsonl"
    catalogue: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    with captures_path.open("w", encoding="utf-8") as captures:
        for record in pilot:
            text = (root / record["relative_path"]).read_text(encoding="utf-8-sig")
            request = model_request(record, text, domains)
            raw, envelope, error = invoke_ollama(request)
            content, thinking = final_content(envelope)
            proposal = None
            reason = error
            if not reason and not content.strip():
                reason = "empty_model_response"
            if not reason:
                try:
                    proposal, reason = validate_proposal(json.loads(content), domains)
                except json.JSONDecodeError:
                    reason = "invalid_json_in_model_response"
            status = "accepted_structurally" if proposal else "rejected"
            outcome = {"stable_source_id": record["stable_source_id"], "relative_path": record["relative_path"], "status": status,
                       "reason": reason, "proposal": proposal}
            outcomes.append(outcome)
            captures.write(json.dumps({"stable_source_id": record["stable_source_id"], "relative_path": record["relative_path"],
                                       "request": request, "raw_response": raw, "response_envelope": envelope,
                                       "thinking": thinking, "error": error}, ensure_ascii=False) + "\n")
            write_review(reviews / f"{record['canonical_content_sha256']}.md", record, outcome)
            catalogue.append({**record, "excerpt": text[:1200], "summary": proposal["summary"] if proposal else "",
                              "proposed_domains": proposal["proposed_domains"] if proposal else [],
                              "proposal_status": status, "proposal_reason": reason,
                              "entities_held_for_review": proposal["entities"] if proposal else [],
                              "people_held_for_review": proposal["people"] if proposal else [],
                              "concepts_held_for_review": proposal["concepts"] if proposal else [],
                              "links_held_for_review": proposal["links"] if proposal else []})
    (output / "pilot-catalogue.json").write_text(json.dumps(catalogue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    queries = ["AI librarian project", "Thailand source material", "local technical system"]
    retrieval = {query: lexical_search(catalogue, query) for query in queries}
    (output / "retrieval-results.json").write_text(json.dumps(retrieval, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    accepted = sum(item["status"] == "accepted_structurally" for item in outcomes)
    report = ["# Rebuild v1 Controlled Pilot", "", f"Pilot sources: {len(pilot)}", f"Structurally accepted responses: {accepted}",
              f"Rejected responses: {len(pilot) - accepted}", "", "## Sources"]
    report.extend(f"- `{item['relative_path']}` — {item['pilot_reason']}" for item in pilot)
    report.extend(["", "## Proposal policy", "- Domains and summaries are accepted only when the structured response validates.",
                   "- Entities, people, concepts, and links are held for review. No record or link was created.", "", "## Retrieval smoke results"])
    for query, results in retrieval.items():
        report.append(f"- `{query}`: {len(results)} result(s)")
    report.extend(["", "## Output", "- `00_System/Data/rebuild-v1/source-manifest.json`", "- `00_System/Data/rebuild-v1/pilot-catalogue.json`",
                   "- `00_System/Data/rebuild-v1/model-captures.jsonl`", "- `00_System/Data/rebuild-v1/reviews/`", "- `00_System/Data/rebuild-v1/retrieval-results.json`"])
    report_path = root / "Reports" / "rebuild-v1-pilot.md"
    report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"pilot={len(pilot)} accepted={accepted} rejected={len(pilot)-accepted} output={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
