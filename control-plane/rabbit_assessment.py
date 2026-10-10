"""Source-linked, conservative workstation assessment. No repository code runs."""
from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import uuid
from urllib.error import HTTPError

from watchlist_sources import read_url
from github_budget import BUDGET, GitHubPaused

PROFILE_PATH = Path(__file__).parent / "plugins" / "rabbit-hole" / "workstation.json"
CACHE_ROOT = Path(__file__).parent / "runtime" / "rabbit-hole-evidence"


def workstation_profile():
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8-sig"))


def inspect_repository(name):
    """Fetch only a bounded README, three releases and five updated issues."""
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", name):
        raise ValueError("Invalid GitHub repository name")
    root = "https://api.github.com/repos/" + name
    documents, errors, checked = [], [], []
    fetched_times = []
    releases = []
    for endpoint, label in [("/readme", "README"), ("/releases?per_page=3", "Releases"),
                             ("/issues?state=all&sort=updated&per_page=5", "Recent issues")]:
        try:
            raw, _ = read_url(root + endpoint, github=True)
            last_read = getattr(BUDGET.read_context, 'last', None)
            if last_read and last_read[0] == root + endpoint:
                fetched_times.append(last_read[1])
            payload = json.loads(raw)
            if label == "README":
                if not isinstance(payload, dict) or payload.get("encoding") != "base64":
                    raise ValueError("README encoding unavailable")
                documents.append(dict(label=label, url=payload.get("html_url") or "https://github.com/" + name,
                                      text=base64.b64decode(payload.get("content", "")).decode("utf-8", "replace")[:100000]))
            else:
                if not isinstance(payload, list):
                    raise ValueError("Unexpected GitHub response")
                for item in payload:
                    if not isinstance(item, dict) or "pull_request" in item or item.get("draft"):
                        continue
                    if label == "Releases":
                        releases.append(dict(tag=item.get("tag_name"), published_at=item.get("published_at"),
                                             prerelease=bool(item.get("prerelease")), url=item.get("html_url")))
                    documents.append(dict(label=label, url=item.get("html_url") or "https://github.com/" + name,
                                          state=item.get("state"), text=(str(item.get("title") or item.get("name") or "") + "\n" + str(item.get("body") or ""))[:30000]))
            checked.append(label)
        except HTTPError as exc:
            if exc.code == 404:
                checked.append(label)
            else:
                errors.append(f"{label}: GitHub HTTP {exc.code}")
                if exc.code in {403, 429} and (exc.headers or {}).get('X-RateLimit-Remaining') == '0':
                    errors.append('GitHub API allowance is exhausted; retry after its reset.')
                    break  # Do not spend two more calls against a known quota.
        except GitHubPaused as exc:
            errors.append(str(exc))
            break
        except Exception as exc:
            errors.append(f"{label}: {type(exc).__name__}")
    releases.sort(key=lambda r: str(r.get("published_at") or ""), reverse=True)
    return dict(documents=documents, latest_release=releases[0] if releases else None,
                checked=checked, errors=errors,
                checked_at=(datetime.fromtimestamp(min(fetched_times), timezone.utc) if fetched_times else datetime.now(timezone.utc)).isoformat(timespec="seconds"))


PATTERNS = {
    "Windows": r"\bwindows\b|\bwsl2?\b|win(?:32|64)",
    "AMD GPU": r"\bamd\b|\brocm\b|\bhip\b|\bvulkan\b|gfx1101|rdna\s*3|rx\s*7800",
    "VRAM": r"\bvram\b|gpu memory|video memory",
    "Disk": r"\bdisk\b|storage|download.{0,30}\b(?:gb|gib|mb)\b|\b(?:gb|gib)\b.{0,30}(?:model|weights)",
    "Local & integration": r"\blocal\b|\boffline\b|\bmcp\b|ollama|llama\.cpp|openai[- ]compatible",
}


def assess(candidate, inspection, profile):
    """Quotes show mentions, never certify that a keyword proves support."""
    evidence, docs_lines, issue_lines = [], [], []
    for document in inspection["documents"]:
        # Markdown prose commonly wraps sentences across lines. Keep the whole
        # paragraph so "Windows is not\nsupported" retains its negation.
        lines = [" ".join(line.split())[:2000] for line in re.split(r"\n\s*\n", document["text"]) if line.strip()]
        for line in lines:
            entry = dict(source=document["label"], url=document["url"], quote=line[:400])
            if document["label"] == "Recent issues":
                issue_lines.append((line, entry, document.get("state")))
            else:
                docs_lines.append((line, entry))
    for label, pattern in PATTERNS.items():
        matches = []
        for line, entry in docs_lines:
            match = re.search(pattern, line, re.I)
            if match:
                start = max(0, match.start() - 80)
                matches.append(dict(entry, quote=("…" if start else "") + line[start:start+400]))
        if matches:
            evidence.append(dict(label=label, status="Mentioned in documentation", **matches[0]))
        else:
            evidence.append(dict(label=label, status="Not stated in inspected documentation"))
    blockers = []
    for line, entry in docs_lines:
        if re.search(r"cuda[- ]only|nvidia[- ]only|(?:requires?|needs?|must have).{0,35}(?:nvidia|cuda)|(?:nvidia|cuda).{0,35}(?:required|mandatory)", line, re.I) and not re.search(r"optional|not required|does not require|no.{0,12}required", line, re.I):
            blockers.append(dict(reason="Documentation states an NVIDIA/CUDA requirement; our GPU is AMD.", **entry))
        memory = re.search(r"(?:minimum|requires?|at least|needs?).{0,25}?(\d+(?:\.\d+)?)\s*(?:gb|gib).{0,12}(?:vram|gpu memory)", line, re.I)
        if memory and float(memory.group(1)) > profile["vram_gb"]:
            blockers.append(dict(reason=f"Stated VRAM requirement exceeds our {profile['vram_gb']} GB GPU.", **entry))
    concerns = [dict(reason="Documentation flags a native Windows limitation. A WSL/Linux route needs separate assessment.", **entry)
                for line, entry in docs_lines if re.search(r"windows.{0,20}(?:not (?:yet )?(?:supported|available)|unsupported)|(?:does not|doesn't).{0,15}support.{0,15}windows", line, re.I)][:1]
    concerns += [dict(reason="An open issue mentions a workstation-related problem; this is a report, not a verified failure.", **entry)
                for line, entry, state in issue_lines if state == "open" and re.search(r"amd|rocm|gfx1101|windows|vram", line, re.I) and re.search(r"fail|broken|crash|not work|unsupported|error", line, re.I)][:2]
    anchors = [
        (r"\bmcp\b", "MCP tool connections with Ariadne"),
        (r"\bollama\b", "our existing Ollama model service"),
        (r"llama\.cpp", "local inference through llama.cpp"),
        (r"openai[- ]compatible", "our OpenAI-compatible client connections"),
        (r"local[- ](?:first|ai|inference|llm)|offline.{0,25}(?:ai|model|inference)", "our preference for local AI workloads"),
    ]
    integration = next(((entry, benefit) for pattern, benefit in anchors for line, entry in docs_lines if re.search(pattern, line, re.I)), None)
    description = candidate.get("description", "")
    documented = "\n".join(line for line, _ in docs_lines)
    # A hardware mention is useful evidence only alongside an actual AI/media
    # purpose; generic local variables or AMD issue reports are not sufficient.
    relevant = bool(integration or re.search(r"speech|audio|video|image|vision|\bai\b|inference|\bllm\b", description, re.I))
    exact_gpu = bool(re.search(r"rx\s*7800\s*xt|gfx1101", documented, re.I))
    amd = bool(re.search(PATTERNS['AMD GPU'], documented, re.I))
    windows = bool(re.search(PATTERNS['Windows'], documented, re.I))
    selection_score = 40 * exact_gpu + 15 * amd + 10 * windows + 20 * bool(integration)
    selection_score -= 10 * bool(concerns)
    # A tailored build for another GPU is less useful than an exact workstation
    # target, even when its README also includes a general GPU reference table.
    target_arch = re.findall(r"\bgfx\d+\b", description, re.I)
    if target_arch and not any(code.casefold() == 'gfx1101' for code in target_arch):
        selection_score -= 50
    if integration:
        why = f"The documentation mentions a connection to {integration[1]}. That is a concrete integration to investigate, not yet a verified fit."
    elif re.search(r"audio|speech|video|image|vision", description, re.I):
        why = "Potentially relevant to our local media workflow; the inspected sources do not yet establish a workstation fit."
    elif re.search(r"agent|automation|\bai\b", description, re.I):
        why = "An AI or automation experiment to follow; a direct fit for our local setup is not established yet."
    else:
        why = "No direct connection to our local AI workflow emerged from this bounded inspection."
    if exact_gpu and relevant:
        why = "The inspected documentation explicitly names our RX 7800 XT/gfx1101 hardware. " + why
    recommendation, reason = "Watch", "The inspected sources do not establish the full Windows/AMD setup or installed disk footprint."
    if not integration and not re.search(r"audio|speech|video|image|vision|agent|automation|\bai\b", description, re.I):
        recommendation, reason = "Ignore", "No clear connection to our local AI or media workflow was found in the inspected sources."
    if blockers:
        recommendation, reason = "Ignore", blockers[0]["reason"]
    elif concerns:
        recommendation, reason = "Watch", concerns[0]["reason"]
    if inspection["errors"]:
        recommendation = "Watch"
        reason = "Evidence collection was incomplete; no positive compatibility conclusion is available."
    if target_arch and not any(code.casefold() == 'gfx1101' for code in target_arch):
        relevant = False
        recommendation, reason = "Ignore", "This build targets another GPU architecture in its description, rather than our gfx1101 workstation."
    # An episode needs a demonstrable local task, rather than a generic API
    # keyword. These are editorial possibilities, never claims of a tested fit.
    episode = None
    for pattern, task in [
        (r'speech|transcri|whisper|dictation', 'Transcribe a short recording locally and compare accuracy, time and VRAM.'),
        (r'video|upscal|image|vision|3d model', 'Run one small media example locally and compare the output, time and disk cost.'),
        (r'inference|llama|benchmark|llm', 'Run a small local model workload and measure speed, VRAM and setup friction.'),
        (r'agent|automation|knowledge graph|rag', 'Try one useful local knowledge or automation task and show what works.'),
    ]:
        if relevant and not blockers and re.search(pattern, description, re.I) and re.search(r'\blocal\b|offline|llama\.cpp|rocm|vulkan|ollama', documented, re.I):
            episode = task
            selection_score += 15
            break
    return dict(profile=profile, recommendation=recommendation, reason=reason, why_care=why,
                episode_idea=episode,
                relevant=relevant, selection_score=selection_score,
                evidence=evidence, blockers=blockers[:2], concerns=concerns, latest_release=inspection["latest_release"],
                checked=inspection["checked"], errors=inspection["errors"], checked_at=inspection["checked_at"],
                summary="Source assessment only; no runtime test performed.")


def enrich(candidate):
    # Reuse unchanged evidence for a day: repeated exploration must not spend
    # thirty API calls just to redisplay the same projects. Check timestamps stay
    # attached to the evidence, including when it is reused.
    key = hashlib.sha256(candidate["repository_name"].casefold().encode()).hexdigest()
    path = CACHE_ROOT / (key + ".json")
    signature = [candidate.get("metrics", {}).get(field) for field in ("pushed_at", "updated_at")]
    inspection = None
    cached = None
    stale = False
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(cached["inspection"]["checked_at"])).total_seconds()
        if cached["signature"] == signature and 0 <= age < 86400:
            inspection = cached["inspection"]
    except (OSError, ValueError, KeyError, TypeError):
        cached = None
    if inspection is None:
        inspection = inspect_repository(candidate["repository_name"])
        if not inspection["errors"]:
            temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
            try:
                CACHE_ROOT.mkdir(parents=True, exist_ok=True)
                temporary.write_text(json.dumps(dict(signature=signature, inspection=inspection), ensure_ascii=False), encoding="utf-8")
                temporary.replace(path)
                for old in sorted(CACHE_ROOT.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[200:]:
                    old.unlink(missing_ok=True)
            except OSError:
                pass  # Caching is optional; evidence errors remain separate.
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        elif cached:
            # Keep useful prior evidence on a failed refresh, while making its
            # age and the failed current check explicit. Never certify old claims.
            inspection = dict(cached["inspection"], errors=inspection["errors"])
            stale = True
    candidate["assessment"] = assess(candidate, inspection, workstation_profile())
    if stale:
        candidate["assessment"]["summary"] = "Last successful evidence retained after a failed refresh; source claims may have changed. No runtime test performed."
    return candidate


def rescore_cached(candidate):
    """Apply today's scoring/profile to saved evidence without any network IO."""
    key = hashlib.sha256(candidate["repository_name"].casefold().encode()).hexdigest()
    try:
        inspection = json.loads((CACHE_ROOT / (key + '.json')).read_text(encoding='utf-8'))['inspection']
        result = assess(candidate, inspection, workstation_profile())
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(inspection['checked_at'])).total_seconds()
        if age >= 86400:
            result['recommendation'] = 'Watch'
            result['reason'] = 'Cached evidence is over a day old; refresh is needed before drawing a current compatibility conclusion.'
        return dict(candidate, assessment=result)
    except (OSError, ValueError, KeyError, TypeError):
        return candidate
