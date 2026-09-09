"""Promote a Discover signal into the existing Knowledge Vault Inbox."""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen


ARTICLE_FETCH_TIMEOUT_SECONDS = 8.0
ARTICLE_MAX_BYTES = 2_000_000
ARTICLE_MAX_CHARS = 100_000
_PROMOTION_LOCK = threading.RLock()
_SIGNAL_ID_RE = re.compile(r"^signal-[A-Za-z0-9_-]{8,80}$")
_WHITESPACE_RE = re.compile(r"\s+")
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "nav", "footer", "header", "aside", "form"}
_BLOCK_TAGS = {"address", "blockquote", "dd", "div", "dl", "dt", "h1", "h2", "h3", "h4", "h5", "h6", "li", "main", "p", "pre", "section", "table", "tr"}
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


class _Node:
    def __init__(self, tag: str, attrs: dict[str, str] | None = None) -> None:
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list[_Node | str] = []


class _ArticleHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document")
        self.stack = [self.root]
        self.page_title = ""
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.casefold()
        node = _Node(name, {str(key).casefold(): str(value or "") for key, value in attrs})
        self.stack[-1].children.append(node)
        if name == "title":
            self._in_title = True
        if name not in _VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == tag.casefold() and tag.casefold() not in _VOID_TAGS:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        name = tag.casefold()
        if name == "title":
            self._in_title = False
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == name:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.page_title += data
        self.stack[-1].children.append(data)


class _GoogleNewsParamsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.params: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "div":
            return
        values = {str(key).casefold(): str(value or "").strip() for key, value in attrs}
        if values.get("data-n-a-id"):
            for key in ("data-n-a-id", "data-n-a-ts", "data-n-a-sg"):
                if values.get(key):
                    self.params[key] = values[key]


def _clean_text(value: object) -> str:
    return _WHITESPACE_RE.sub(" ", str(value or "")).strip()


def _node_text(node: _Node | str) -> str:
    if isinstance(node, str):
        return node
    if node.tag in _SKIP_TAGS:
        return ""
    return " ".join(_node_text(child) for child in node.children if _node_text(child)).strip()


def _iter_nodes(node: _Node) -> list[_Node]:
    result: list[_Node] = []
    for child in node.children:
        if not isinstance(child, _Node):
            continue
        result.append(child)
        result.extend(_iter_nodes(child))
    return result


def _article_score(node: _Node) -> int:
    if node.tag in _SKIP_TAGS:
        return -1
    text = _clean_text(_node_text(node))
    if len(text) < 80:
        return -1
    paragraphs = sum(1 for child in _iter_nodes(node) if child.tag == "p")
    identity = f"{node.attrs.get('id', '')} {node.attrs.get('class', '')}".casefold()
    score = min(len(text), 12_000) // 40 + paragraphs * 80
    if node.tag == "article":
        score += 2_000
    elif node.tag == "main":
        score += 1_500
    if re.search(r"article|story|post|entry|content|body", identity):
        score += 700
    if re.search(r"comment|sidebar|related|recommend|newsletter|cookie", identity):
        score -= 1_200
    return score


def _collect_blocks(node: _Node, lines: list[str]) -> None:
    if node.tag in _SKIP_TAGS:
        return
    if node.tag in {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre"}:
        text = _clean_text(_node_text(node))
        if text:
            lines.append(text)
        return
    for child in node.children:
        if isinstance(child, _Node):
            _collect_blocks(child, lines)


def extract_article_text(raw: bytes, charset: str = "utf-8") -> tuple[str, str]:
    parser = _ArticleHTMLParser()
    parser.feed(raw.decode(charset, errors="replace"))
    nodes = [node for node in _iter_nodes(parser.root) if node.tag not in _SKIP_TAGS]
    selected = max(nodes, key=_article_score, default=parser.root)
    lines: list[str] = []
    _collect_blocks(selected, lines)
    if not lines:
        fallback = _clean_text(_node_text(selected))
        lines = [fallback] if fallback else []
    cleaned: list[str] = []
    seen: set[str] = set()
    for line in lines:
        value = _clean_text(line)
        if len(value) < 3 or value.casefold() in seen:
            continue
        seen.add(value.casefold())
        cleaned.append(value)
    return "\n\n".join(cleaned)[:ARTICLE_MAX_CHARS].strip(), _clean_text(parser.page_title)


def _google_news_article_id(url: str) -> str:
    parts = urlsplit(url)
    if (parts.hostname or "").casefold() != "news.google.com":
        return ""
    segments = [segment for segment in parts.path.split("/") if segment]
    for marker in ("articles", "read"):
        if marker in segments:
            index = segments.index(marker)
            return segments[index + 1] if index + 1 < len(segments) else ""
    return ""


def resolve_article_url(url: str, timeout: float = ARTICLE_FETCH_TIMEOUT_SECONDS) -> str:
    """Resolve Google News wrappers while leaving ordinary URLs to urllib redirects."""
    article_id = _google_news_article_id(url)
    if not article_id:
        return url
    bounded_timeout = max(1.0, min(float(timeout), 15.0))
    try:
        page_request = Request(
            f"https://news.google.com/articles/{article_id}?hl=en-US&gl=US&ceid=US:en",
            headers={"Accept": "text/html,application/xhtml+xml", "User-Agent": "Mozilla/5.0 Ariadne/1.0"},
        )
        with urlopen(page_request, timeout=bounded_timeout) as response:
            raw = response.read(ARTICLE_MAX_BYTES)
            charset = response.headers.get_content_charset() or "utf-8"
        parser = _GoogleNewsParamsParser()
        parser.feed(raw.decode(charset, errors="replace"))
        source = parser.params
        if not all(source.get(key) for key in ("data-n-a-id", "data-n-a-ts", "data-n-a-sg")):
            return url
        inner = [
            "garturlreq",
            [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
            source["data-n-a-id"], int(source["data-n-a-ts"]), source["data-n-a-sg"],
        ]
        articles_request = ["Fbv4je", json.dumps(inner, separators=(",", ":"))]
        body = urlencode({"f.req": json.dumps([[articles_request]], separators=(",", ":"))}).encode("utf-8")
        batch_request = Request(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute",
            data=body,
            headers={"Accept": "*/*", "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8", "User-Agent": "Mozilla/5.0 Ariadne/1.0"},
        )
        with urlopen(batch_request, timeout=bounded_timeout) as response:
            result = response.read(200_000).decode(response.headers.get_content_charset() or "utf-8", errors="replace")
        decoded = json.loads(json.loads(result.split("\n\n", 1)[1])[0][2])
        resolved = decoded[1] if isinstance(decoded, list) and len(decoded) > 1 else ""
        return resolved if isinstance(resolved, str) and resolved.startswith(("http://", "https://")) else url
    except (OSError, ValueError, TypeError, IndexError, KeyError, json.JSONDecodeError):
        return url


def _fetch_article(url: str, timeout: float = ARTICLE_FETCH_TIMEOUT_SECONDS) -> tuple[str, str, str]:
    resolved = resolve_article_url(url, timeout)
    request = Request(resolved, headers={"Accept": "text/html,application/xhtml+xml", "User-Agent": "Mozilla/5.0 Ariadne/1.0"})
    with urlopen(request, timeout=max(1.0, min(float(timeout), 15.0))) as response:
        raw = response.read(ARTICLE_MAX_BYTES)
        final_url = response.geturl() if hasattr(response, "geturl") else resolved
        charset = response.headers.get_content_charset() or "utf-8"
    text, page_title = extract_article_text(raw, charset)
    return final_url, page_title, text


def _yaml_string(value: object) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 _-]+", "", value).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned[:80].rstrip(" .-") or "source-article")


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            try:
                os.unlink(temporary_name)
            except OSError:
                pass


def _front_matter_signal_id(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8-sig")[:20_000]
    except OSError:
        return ""
    match = re.search(r"(?m)^signal_id:\s*(.+?)\s*$", text.split("\n---", 1)[0])
    if not match:
        return ""
    value = match.group(1).strip()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = value.strip("'")
    return str(parsed) if parsed else ""


def _cached_article_text(path: Path) -> str:
    """Return previously fetched article text from a promoted note, if present."""
    try:
        content = path.read_text(encoding="utf-8-sig")
    except OSError:
        return ""
    match = re.search(r"(?ms)^## Article text\s*\n\s*(.*?)\s*\n\s*## ", content)
    if not match:
        return ""
    value = match.group(1).strip()
    if not value or value.casefold().startswith("no article text was available"):
        return ""
    return value


def _cached_front_matter_value(path: Path, key: str) -> str:
    try:
        header = path.read_text(encoding="utf-8-sig").split("\n---", 1)[0]
    except OSError:
        return ""
    match = re.search(rf"(?m)^{re.escape(key)}:\s*(.+?)\s*$", header)
    if not match:
        return ""
    try:
        value = json.loads(match.group(1).strip())
    except json.JSONDecodeError:
        value = match.group(1).strip().strip("'\"")
    return str(value or "")


def _find_existing_note(vault_root: Path, signal_id: str) -> Path | None:
    for folder in ("Inbox", "Processed", "Failed"):
        directory = vault_root / folder
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md"), key=lambda item: item.name.casefold()):
            if _front_matter_signal_id(path) == signal_id:
                return path
    return None


def _signal_matches(signal: dict[str, Any]) -> list[str]:
    matches = signal.get("watchlist_matches")
    if not isinstance(matches, list):
        return []
    values: list[str] = []
    for match in matches:
        value = match if isinstance(match, str) else match.get("topic") if isinstance(match, dict) else ""
        if value and str(value) not in values:
            values.append(str(value))
    return values


def _provenance(signal: dict[str, Any], resolved_url: str, original_url: str) -> list[str]:
    source = str(signal.get("source_name") or "Ariadne Signal Service").strip()
    values = [source] if source else []
    if resolved_url and resolved_url != original_url:
        values.append("resolved publisher URL")
    return values


def _render_note(signal: dict[str, Any], *, resolved_url: str, article_text: str, captured_at: str, fetch_error: str = "") -> str:
    signal_id = str(signal.get("signal_id") or "")
    title = str(signal.get("title") or "Signal")
    source = str(signal.get("source_name") or "Unknown source")
    original_url = str(signal.get("url") or "")
    published_at = str(signal.get("published_at") or "")
    category = str(signal.get("category") or "Main News Feed")
    summary = str(signal.get("summary") or signal.get("content") or "").strip()
    image_url = str(signal.get("image_url") or "")
    matches = _signal_matches(signal)
    provenance = _provenance(signal, resolved_url, original_url)
    lines = [
        "---",
        "type: source-article",
        f"signal_id: {_yaml_string(signal_id)}",
        f"title: {_yaml_string(title)}",
        f"source: {_yaml_string(source)}",
        f"source_url: {_yaml_string(original_url)}",
        f"resolved_url: {_yaml_string(resolved_url)}",
        f"published_at: {_yaml_string(published_at)}",
        f"captured_at: {_yaml_string(captured_at)}",
        f"category: {_yaml_string(category)}",
        f"image_url: {_yaml_string(image_url)}",
        f"description: {_yaml_string(summary)}",
        f"article_status: {_yaml_string('unavailable' if fetch_error else 'ready')}",
    ]
    if fetch_error:
        lines.append(f"article_error: {_yaml_string(fetch_error)}")
    if matches:
        lines.append("watchlist_matches:")
        lines.extend(f"  - {_yaml_string(value)}" for value in matches)
    else:
        lines.append("watchlist_matches: []")
    if provenance:
        lines.append("provenance:")
        lines.extend(f"  - {_yaml_string(value)}" for value in provenance)
    else:
        lines.append("provenance: []")
    lines.extend([
        "---",
        "",
        f"# {title}",
        "",
        "## Signal context",
        "",
        "The following fields are the stored Signal Service record, not additional article reporting.",
        f"- Title: {title}",
        f"- Digest: {summary or 'No digest was supplied.'}",
        f"- Source: {source}",
        f"- URL: {original_url}",
        "",
        "## Source",
        "",
        f"{source}, {published_at or 'publication date unavailable'}.",
        f"Original link: {original_url}",
        f"Resolved publisher: {resolved_url}",
        "",
        "## Article text",
        "",
        article_text.strip() or str(signal.get("content") or signal.get("summary") or "No article text was available."),
        "",
        "## Ariadne context",
        "",
        "This article was promoted from Discover using “Think with Ariadne”. Keep article claims separate from Ariadne inference or analogy; any comparison to Warren's setup must be labelled as inference, not attributed to the article.",
        "",
    ])
    return "\n".join(lines)


def promote_signal(vault_root: Path, signal: dict[str, Any], *, timeout: float = ARTICLE_FETCH_TIMEOUT_SECONDS, captured_at: str | None = None) -> dict[str, Any]:
    signal_id = str(signal.get("signal_id") or "").strip()
    if not _SIGNAL_ID_RE.fullmatch(signal_id):
        raise ValueError("A valid signal_id is required.")
    original_url = str(signal.get("url") or "").strip()
    if not original_url.startswith(("http://", "https://")):
        raise ValueError("The signal does not contain a valid source URL.")
    root = Path(vault_root).resolve()
    timestamp = captured_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    with _PROMOTION_LOCK:
        existing = _find_existing_note(root, signal_id)
        cached_text = _cached_article_text(existing) if existing else ""
    if cached_text:
        resolved_url = _cached_front_matter_value(existing, "resolved_url") or original_url
        page_title = str(signal.get("title") or "")
        article_text = cached_text
        fetch_error = ""
        cache_hit = True
    else:
        cache_hit = False
        try:
            resolved_url, page_title, article_text = _fetch_article(original_url, timeout)
        except (OSError, ValueError, TypeError) as exc:
            resolved_url = original_url
            page_title = ""
            article_text = ""
            fetch_error = str(exc)[:240]
        else:
            fetch_error = ""
    if page_title and not signal.get("title"):
        signal = {**signal, "title": page_title}
    with _PROMOTION_LOCK:
        existing = _find_existing_note(root, signal_id)
        path = existing or (root / "Inbox" / f"{_safe_filename(str(signal.get('title') or 'source-article'))}__{signal_id}.md")
        if path.parent != root / "Inbox" and (existing is None or path.parent not in {root / "Processed", root / "Failed"}):
            raise ValueError("Source article path escaped the Knowledge Vault.")
        _atomic_write(path, _render_note(signal, resolved_url=resolved_url, article_text=article_text, captured_at=timestamp, fetch_error=fetch_error))
    return {
        "signal_id": signal_id,
        "path": path.relative_to(root).as_posix(),
        "updated": existing is not None,
        "resolved_url": resolved_url,
        "article_title": page_title or str(signal.get("title") or ""),
        "article_text_chars": len(article_text),
        "fetch_error": fetch_error,
        "cache_hit": cache_hit,
    }


__all__ = ["extract_article_text", "promote_signal", "resolve_article_url"]
