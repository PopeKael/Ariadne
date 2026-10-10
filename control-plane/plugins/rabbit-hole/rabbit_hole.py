"""Read-only GitHub repository discovery for Ariadne's Rabbit Hole tool.

This adapter reads public GitHub metadata and bounded documentation. It never
clones, installs, imports, or executes repository code.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from github_budget import BUDGET, GitHubPaused

# Like the trusted plugin adapter itself, load its assessment helper afresh for
# each exploration so source fixes do not require restarting the resident core.
_assessment_spec = importlib.util.spec_from_file_location("ariadne_rabbit_assessment", Path(__file__).resolve().parents[2] / "rabbit_assessment.py")
_assessment = importlib.util.module_from_spec(_assessment_spec)
_assessment_spec.loader.exec_module(_assessment)
enrich = _assessment.enrich


GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
MAX_RESULTS = 9
MAX_CANDIDATES = 120
SEARCH_TIMEOUT_SECONDS = 20
REPORT = Callable[[str, str, float | int | None], None]


def _clean_text(value: object, limit: int = 320) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    text = re.sub(r"^[*`_ ]*(?:github description|description)\s*:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[*`_]", "", text)
    return text[:limit].rstrip() if len(text) > limit else text


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _age_text(value: object, now: datetime) -> str:
    timestamp = _parse_timestamp(value)
    if timestamp is None:
        return "recent activity date was not supplied"
    days = max(0, (now - timestamp).days)
    if days == 0:
        return "pushed today"
    if days == 1:
        return "pushed yesterday"
    if days < 30:
        return f"pushed {days} days ago"
    months = max(1, round(days / 30))
    return f"pushed about {months} month{'s' if months != 1 else ''} ago"


def _query_strings(now: datetime) -> list[str]:
    # Mature projects remain eligible; recency is a tie-break, not a gate.
    base = "is:public archived:false fork:false in:name,description,readme"
    return [
        f'{base} "RX 7800 XT" inference',
        f'{base} gfx1101 inference',
        f'{base} Windows AMD "local AI"',
        f'{base} Ollama MCP',
        f'{base} "llama.cpp" Windows',
    ]


def _request_json(query: str, token: str | None, page: int = 1) -> dict[str, Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Ariadne-Rabbit-Hole/0.1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # No explicit sort: GitHub best-match relevance includes README matches.
    url = f"{GITHUB_SEARCH_URL}?{urlencode({'q': query, 'per_page': 30, 'page': page})}"
    request = Request(url, headers=headers, method="GET")
    raw, _ = BUDGET.request(request, urlopen, timeout=SEARCH_TIMEOUT_SECONDS, max_bytes=4_000_000)
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("GitHub returned a non-object search response.")
    return payload


def _score(item: dict[str, Any], now: datetime) -> float:
    text = " ".join([
        _clean_text(item.get("name")),
        _clean_text(item.get("description")),
        " ".join(str(topic) for topic in item.get("topics", []) if isinstance(topic, str)),
    ]).casefold()
    pushed = _parse_timestamp(item.get("pushed_at"))
    recency = 0.0
    if pushed:
        days = max(0, (now - pushed).total_seconds() / 86400)
        recency = 3.0 if days <= 30 else 1.0 if days <= 120 else 0.0
    stars = item.get("stargazers_count") if isinstance(item.get("stargazers_count"), (int, float)) else 0
    popularity_tiebreak = min(6.0, math.log1p(max(0, stars)) / 2.5)
    hardware = 30 * bool(re.search(r"rx.?7800|gfx1101|rdna.?3", text))
    integration = 20 * bool(re.search(r"ollama|llama\.cpp|\bmcp\b|openai[- ]compatible|local[- ]ai|inference", text))
    media = 8 * bool(re.search(r"speech|audio|video|image|vision", text))
    return hardware + integration + media + recency + popularity_tiebreak


def _why_interesting(item: dict[str, Any]) -> str:
    text = " ".join([
        _clean_text(item.get("name")),
        _clean_text(item.get("description")),
        " ".join(str(topic) for topic in item.get("topics", []) if isinstance(topic, str)),
    ]).casefold()
    hooks = []
    if any(term in text for term in ("visual", "browser", "3d", "camera", "shader", "interactive")):
        hooks.append("it looks like it has something tangible to see")
    if any(term in text for term in ("agent", "automation", "robot", "computer vision", "multimodal")):
        hooks.append("it pushes an AI or automation idea into the real world")
    if any(term in text for term in ("experimental", "playground", "prototype", "simulation", "generative", "toy")):
        hooks.append("it has an experimental rather than conventional-product shape")
    if not hooks:
        hooks.append("the combination of its subject and recent activity is worth a closer look")
    return "Ariadne spotted this because " + "; ".join(hooks[:2]) + "."


def _platform_concern(item: dict[str, Any]) -> str:
    text = " ".join([
        _clean_text(item.get("description")),
        " ".join(str(topic) for topic in item.get("topics", []) if isinstance(topic, str)),
        str(item.get("language") or ""),
    ]).casefold()
    if any(term in text for term in ("cuda", "nvidia", "pytorch", "tensorflow")):
        return "Likely GPU-heavy and may assume NVIDIA/CUDA; Windows and AMD compatibility need checking."
    if any(term in text for term in ("opencv", "computer vision", "camera", "video", "real-time")):
        return "May need camera/media drivers and meaningful GPU or CPU headroom; inspect Windows setup first."
    if any(term in text for term in ("rust", "c++", "cuda", "compiler", "kernel")):
        return "May need a compiler or native build tools on Windows."
    if any(term in text for term in ("browser", "web", "wasm", "javascript", "typescript")):
        return "Could be one of the easier Windows experiments if its browser demo is self-contained."
    if str(item.get("language") or "").casefold() in {"python", "javascript", "typescript", "rust", "go"}:
        return "The main language is plausible on Windows; dependency and model requirements still need inspection."
    return "No obvious platform requirement is visible in search metadata; inspect the README before trying it."


def _candidate(item: dict[str, Any], now: datetime, score: float) -> dict[str, Any] | None:
    full_name = _clean_text(item.get("full_name"), 160)
    url = _clean_text(item.get("html_url"), 500)
    if not full_name or not url or not url.startswith("https://github.com/"):
        return None
    description = _clean_text(item.get("description"), 280) or "GitHub did not provide a short description."
    pushed = _age_text(item.get("pushed_at"), now)
    updated = _age_text(item.get("updated_at"), now).replace("pushed", "updated")
    issues = item.get("open_issues_count") if isinstance(item.get("open_issues_count"), int) else None
    maintenance = f"{pushed}; {updated}"
    if issues is not None:
        maintenance += f"; {issues} open issue{'s' if issues != 1 else ''}"
    return {
        "repository_name": full_name,
        "description": description,
        "why_interesting": _why_interesting(item),
        "maintenance_signal": maintenance + ".",
        "platform_concerns": _platform_concern(item),
        "github_url": url,
        "language": _clean_text(item.get("language"), 40) or "Not stated",
        "topics": [str(topic) for topic in item.get("topics", [])[:8] if isinstance(topic, str)],
        "score": round(score, 2),
        "metrics": {key: item.get(key) for key in (
            "created_at", "pushed_at", "updated_at", "stargazers_count", "forks_count",
            "open_issues_count", "size", "archived", "disabled")},
    }


def search_candidates(page: int, report: REPORT) -> dict[str, Any]:
    """Search GitHub and return up to 10 ranked, read-only exploration candidates."""
    now = datetime.now(timezone.utc)
    try:
        BUDGET.begin_discovery()
    except GitHubPaused as exc:
        return dict(ok=False, candidates=[], warnings=[str(exc)], queries_attempted=0)
    token = os.environ.get("ARIADNE_GITHUB_TOKEN", "").strip() or None
    report = report or (lambda _stage, _status, _progress=None: None)
    found: dict[str, tuple[dict[str, Any], float]] = {}
    warnings: list[str] = []
    queries = _query_strings(now)
    # User-nominated projects go through the same evidence screening.
    nominations = []
    try:
        nominations = json.loads(Path(__file__).with_name("discovery-sources.json").read_text(encoding="utf-8"))["repositories"][:5]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    queries += ["repo:" + name for name in nominations if isinstance(name, str) and re.fullmatch(r"[\w.-]+/[\w.-]+", name)]
    if page > 1:
        queries = queries[:5]  # Explicit nominations have no second result page.
    successful = 0
    for index, query in enumerate(queries, start=1):
        report("searching", f"Searching GitHub for workstation and workflow matches ({index}/{len(queries)})…", (index - 1) / len(queries) * 80)
        try:
            payload = _request_json(query, token, page)
            successful += 1
        except Exception as exc:
            warnings.append(f"Search {index} failed: {str(exc)[:180]}")
            if isinstance(exc, GitHubPaused):
                break
            continue
        for position, item in enumerate(payload.get("items", []) if isinstance(payload.get("items"), list) else []):
            if not isinstance(item, dict) or item.get("archived") or item.get("fork"):
                continue
            full_name = str(item.get("full_name") or "").casefold()
            if not full_name:
                continue
            # Keep README-only matches in contention ahead of source collection.
            score = _score(item, now) + (30 if index <= 2 else 15) + max(0, 15 - position)
            if str(item.get("full_name")) in nominations:
                score += 100
            current = found.get(full_name)
            if current is None or score > current[1]:
                found[full_name] = (item, score)
        report("ranking", f"Ranking {min(len(found), MAX_CANDIDATES)} candidate repositories…", index / len(queries) * 80)

    ranked = sorted(found.values(), key=lambda pair: (-pair[1], str(pair[0].get("full_name") or "").casefold()))[:MAX_CANDIDATES]
    results: list[dict[str, Any]] = []
    for item, score in ranked:
        candidate = _candidate(item, now, score)
        if candidate is None:
            continue
        results.append(candidate)
    return dict(ok=bool(successful), candidates=results, search_matches=len(found),
                queries_attempted=len(queries), warnings=warnings)


def explore(config: dict[str, Any] | None = None, report: REPORT | None = None) -> dict[str, Any]:
    config = config or {}
    report = report or (lambda *_: None)
    if config.get('mode') == 'check':
        from rabbit_library import repository_name
        repository = repository_name(config.get('repository'))
        report('inspecting', f'Checking {repository}: metadata, README, releases and issues…', 10)
        headers = {'Accept': 'application/vnd.github+json', 'User-Agent': 'Ariadne-Rabbit-Hole/0.1'}
        token = os.environ.get('ARIADNE_GITHUB_TOKEN', '').strip()
        if token: headers['Authorization'] = 'Bearer ' + token
        warnings = []
        try:
            request = Request('https://api.github.com/repos/' + repository, headers=headers)
            raw, _ = BUDGET.request(request, urlopen, timeout=SEARCH_TIMEOUT_SECONDS, max_bytes=1_000_000)
            metadata = json.loads(raw)
            candidate = _candidate(metadata, datetime.now(timezone.utc), 0)
            if not candidate: raise ValueError('GitHub did not return a repository.')
        except GitHubPaused as exc:
            # Keep the user's suggestion even when source collection must wait.
            warnings.append(str(exc))
            candidate = _candidate({'full_name': repository, 'html_url': 'https://github.com/' + repository,
                                    'description': 'Suggested by you. Source checks are waiting for the GitHub allowance.'}, datetime.now(timezone.utc), 0)
        candidate = enrich(candidate)
        candidate['nominated'] = True
        candidate['source_check_pending'] = bool(warnings or candidate.get('assessment', {}).get('errors'))
        warnings.extend(candidate.get('assessment', {}).get('errors', []))
        message = f'{repository} added to your cards.' + (' Source checks are incomplete; retry when the GitHub allowance is available.' if candidate['source_check_pending'] else ' Source assessment complete; no runtime test performed.')
        report('completed', message, 100)
        return dict(ok=True, results=[candidate], warnings=warnings, message=message)
    spec = importlib.util.spec_from_file_location('ariadne_rabbit_discovery', Path(__file__).resolve().parents[2] / 'rabbit_discovery.py')
    discovery = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(discovery)
    result = discovery.advance(config, search_candidates, enrich, report)
    result['authenticated'] = bool(os.environ.get('ARIADNE_GITHUB_TOKEN', '').strip())
    return result


__all__ = ["MAX_RESULTS", "explore"]
