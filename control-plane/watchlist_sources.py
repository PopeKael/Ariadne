"""Bounded public evidence adapters, with redirect/DNS checks for saved URLs."""
from __future__ import annotations

import base64
import ipaddress
import json
import os
import re
import socket
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from source_article import extract_article_text
from github_budget import BUDGET, GitHubPaused


def public_url(value, *, resolve=True):
    parts = urlsplit(value)
    if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password
            or parts.port not in {None, 80, 443}):
        raise ValueError("Sources must be public HTTP(S) URLs without credentials or custom ports.")
    host = parts.hostname.casefold()
    if host in {"localhost", "metadata.google.internal"} or host.endswith((".local", ".internal", ".localhost")):
        raise ValueError("Use a public source URL.")
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        addresses = [ipaddress.ip_address(row[4][0]) for row in socket.getaddrinfo(host, parts.port or 443)] if resolve else []
    if any(not address.is_global for address in addresses):
        raise ValueError("Private and reserved source addresses are unavailable.")
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, ""))


class PublicRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        # GitHub's bearer token is never forwarded to a different origin.
        if req.has_header("Authorization"):
            raise ValueError("Authenticated GitHub API redirects are unavailable.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def read_url(url, *, github=False):
    public_url(url)
    headers = {"User-Agent": "Ariadne-Watchlist/1.0", "Accept": "application/vnd.github+json" if github else "text/html, text/plain, application/json"}
    if github and os.environ.get("ARIADNE_GITHUB_TOKEN", "").strip():
        headers["Authorization"] = "Bearer " + os.environ["ARIADNE_GITHUB_TOKEN"].strip()
    if github:
        return BUDGET.request(Request(url, headers=headers), build_opener(PublicRedirects()).open)
    with build_opener(PublicRedirects()).open(Request(url, headers=headers), timeout=10) as response:
        raw = response.read(512_001)
        if len(raw) > 512_000:
            raise ValueError("Source exceeded the 512 KB evidence limit.")
        return raw, response.headers.get_content_charset() or "utf-8"


def excerpt(text, purpose):
    # Show actual source words around watch terms, never infer compatibility.
    terms = re.findall(r"[\w.+-]{3,}", purpose.casefold())
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    matching = [line for line in lines if any(term in line.casefold() for term in terms)]
    return "\n".join((matching or lines)[:12])[:1800]


def github_sources(url, purpose):
    parts = urlsplit(url)
    path = parts.path.strip("/").split("/")
    if len(path) < 2 or not all(re.fullmatch(r"[\w.-]+", p) for p in path[:2]):
        raise ValueError("Use a GitHub repository URL.")
    repo = "/".join(path[:2]).removesuffix(".git")
    root = "https://api.github.com/repos/" + repo
    result = []
    for endpoint, label in [("/readme", "README"), ("/releases?per_page=3", "Releases"), ("/issues?state=all&sort=updated&per_page=5", "Recent issues")]:
        try:
            raw, _ = read_url(root + endpoint, github=True)
            payload = json.loads(raw)
        except HTTPError as exc:
            if exc.code == 404 and label in {"README", "Releases"}:
                continue
            raise
        if isinstance(payload, dict) and label == "README":
            text = base64.b64decode(payload.get("content", "")).decode("utf-8", "replace")
            result.append(dict(title=repo + " · README", url=payload.get("html_url") or url,
                               snippet=excerpt(text, purpose)))
        elif isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict) or "pull_request" in item:
                    continue
                result.append(dict(title=str(item.get("name") or item.get("title") or label)[:250],
                                   url=item["html_url"], snippet=excerpt(str(item.get("body") or ""), purpose)))
    return result


def collect_evidence(watch, search):
    evidence, failures = [], []
    focus = watch["purpose"] + " " + watch["query"]
    for url in watch["sources"]:
        try:
            if urlsplit(url).hostname == "github.com":
                evidence.extend(github_sources(url, focus))
            else:
                raw, charset = read_url(url)
                text, title = extract_article_text(raw, charset)
                if not text.strip():
                    raise ValueError("Source returned no readable text.")
                evidence.append(dict(title=title or url, url=url, snippet=excerpt(text, focus)))
        except Exception as exc:
            failures.append(f"{url}: {str(exc) if isinstance(exc, GitHubPaused) else type(exc).__name__}")
    if watch["query"]:
        # An encyclopedia fallback can explain a subject but cannot monitor developments.
        result = search(watch["query"], limit=5, fetch_limit=0, exclude_provider_ids=("wikipedia-api",))
        if not result.get("ok"):
            failures.append("Search returned no usable evidence or its provider was unavailable.")
        else:
            for item in result.get("results", []):
                url = public_url(str(item.get("url") or ""), resolve=False)
                evidence.append(dict(title=str(item.get("title") or url)[:250], url=url,
                                     snippet=str(item.get("snippet") or "")[:1800]))
    # Partial collection must not quietly certify a successful check.
    if failures:
        raise ValueError("Some sources could not be checked. " + "; ".join(failures))
    return list({item["url"]: item for item in evidence}.values())
