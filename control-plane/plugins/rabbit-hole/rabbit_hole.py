"""Read-only GitHub repository discovery for Ariadne's Rabbit Hole tool.

This adapter only calls GitHub's public REST search endpoint. It never clones,
downloads, installs, imports, or executes repository contents.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import os
import re
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
MAX_RESULTS = 5
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
    cutoff = (now - timedelta(days=120)).date().isoformat()
    base = f"is:public archived:false fork:false pushed:>={cutoff}"
    return [
        f"{base} (topic:ai OR topic:agents OR topic:local-ai)",
        f"{base} (topic:computer-vision OR topic:multimodal OR topic:generative-art)",
        f"{base} (topic:simulation OR topic:creative-coding OR topic:visualization)",
        f"{base} (topic:automation OR topic:robotics OR topic:browser-automation)",
        f'{base} (experimental OR "proof of concept" OR unusual)'
    ]


def _request_json(query: str, token: str | None) -> dict[str, Any]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Ariadne-Rabbit-Hole/0.1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{GITHUB_SEARCH_URL}?{urlencode({'q': query, 'sort': 'updated', 'order': 'desc', 'per_page': 30})}"
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=SEARCH_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("GitHub returned a non-object search response.")
    return payload


def _keyword_score(text: str, words: tuple[str, ...], weight: float) -> float:
    return min(weight, sum(1 for word in words if word in text) * (weight / max(3, len(words))))


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
        recency = 25.0 if days <= 7 else 18.0 if days <= 30 else 10.0 if days <= 120 else 0.0
    unusual = _keyword_score(text, ("experimental", "weird", "unusual", "surprising", "playground", "prototype", "generative", "simulation", "toy", "odd"), 25)
    demo = _keyword_score(text, ("visual", "interactive", "demo", "browser", "camera", "3d", "real-time", "shader", "audio", "video", "game"), 20)
    useful = _keyword_score(text, ("ai", "agent", "automation", "vision", "multimodal", "local", "offline", "robot", "finance", "data"), 15)
    language_bonus = 4.0 if str(item.get("language") or "").casefold() in {"python", "javascript", "typescript", "rust", "go"} else 0.0
    stars = item.get("stargazers_count") if isinstance(item.get("stargazers_count"), (int, float)) else 0
    popularity_tiebreak = min(6.0, math.log1p(max(0, stars)) / 2.5)
    return recency + unusual + demo + useful + language_bonus + popularity_tiebreak


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
    }


def explore(config: dict[str, Any] | None = None, report: REPORT | None = None) -> dict[str, Any]:
    """Search GitHub and return 3-5 ranked, read-only exploration candidates."""
    del config
    now = datetime.now(timezone.utc)
    token = os.environ.get("ARIADNE_GITHUB_TOKEN", "").strip() or None
    report = report or (lambda _stage, _status, _progress=None: None)
    found: dict[str, tuple[dict[str, Any], float]] = {}
    warnings: list[str] = []
    queries = _query_strings(now)
    for index, query in enumerate(queries, start=1):
        report("searching", f"Searching GitHub for unusual active projects ({index}/{len(queries)})…", (index - 1) / len(queries) * 80)
        try:
            payload = _request_json(query, token)
        except Exception as exc:
            warnings.append(f"Search {index} failed: {str(exc)[:180]}")
            continue
        for item in payload.get("items", []) if isinstance(payload.get("items"), list) else []:
            if not isinstance(item, dict) or item.get("archived") or item.get("fork"):
                continue
            full_name = str(item.get("full_name") or "").casefold()
            if not full_name:
                continue
            score = _score(item, now)
            current = found.get(full_name)
            if current is None or score > current[1]:
                found[full_name] = (item, score)
        report("ranking", f"Ranking {min(len(found), MAX_CANDIDATES)} candidate repositories…", index / len(queries) * 80)

    ranked = sorted(found.values(), key=lambda pair: (-pair[1], str(pair[0].get("full_name") or "").casefold()))[:MAX_CANDIDATES]
    results: list[dict[str, Any]] = []
    seen_languages: dict[str, int] = {}
    for item, score in ranked:
        language = str(item.get("language") or "Not stated").casefold()
        # Keep the small result set from becoming five near-identical repositories.
        if seen_languages.get(language, 0) >= 2:
            continue
        candidate = _candidate(item, now, score)
        if candidate is None:
            continue
        results.append(candidate)
        seen_languages[language] = seen_languages.get(language, 0) + 1
        if len(results) == MAX_RESULTS:
            break
    report("completed", f"Found {len(results)} rabbit-hole candidate{'s' if len(results) != 1 else ''}.", 100)
    return {
        "ok": bool(results),
        "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "GitHub public REST repository search",
        "authenticated": bool(token),
        "queries_attempted": len(queries),
        "results": results,
        "warnings": warnings,
        "message": "Ariadne found a few things worth trying." if results else "GitHub returned no usable candidates; try again later.",
    }


__all__ = ["MAX_RESULTS", "explore"]
