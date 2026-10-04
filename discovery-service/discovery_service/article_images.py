"""Deterministic representative-image extraction and validated local storage."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import threading
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from PIL import Image

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
}
META_ORDER = ("og:image", "og:image:url", "og:image:secure_url", "twitter:image", "twitter:image:src")
EXCLUDED = re.compile(r"(?:logo|avatar|icon|tracking|advert|related|recommend|sidebar|social|newsletter)", re.I)
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def _image_values(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _image_values(item)
    elif isinstance(value, dict):
        for key in ("url", "contentUrl", "@id"):
            if isinstance(value.get(key), str):
                yield value[key]


def _jsonld_images(value):
    if isinstance(value, list):
        for item in value:
            yield from _jsonld_images(item)
    elif isinstance(value, dict):
        types = value.get("@type", [])
        types = [types] if isinstance(types, str) else types
        # Organisation logos and unrelated graph nodes are not article images.
        if any(str(t).lower().endswith(("article", "posting", "webpage", "videoobject")) for t in types):
            for key in ("image", "primaryImageOfPage"):
                yield from _image_values(value.get(key))
        for key in ("@graph", "mainEntity"):
            yield from _jsonld_images(value.get(key))


class ImageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {key: [] for key in META_ORDER}
        self.links = []
        self.heroes = []
        self.jsonld = []
        self.description = ""
        self.stack = []
        self.script = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        hint = " ".join(str(a.get(k) or "") for k in ("id", "class", "alt", "src"))
        excluded = bool(EXCLUDED.search(hint)) or tag in {"nav", "footer", "aside"} or "hidden" in a or a.get("aria-hidden") == "true"
        article_scope = tag in {"article", "main"} or bool(re.search(r"article[-_ ]?(?:body|content)|story[-_ ]?(?:body|content)|hero|lead[-_ ]image|featured[-_ ]image", hint, re.I))
        if tag not in VOID:
            self.stack.append((tag, excluded, article_scope))
        if tag == "script" and str(a.get("type", "")).lower() == "application/ld+json":
            self.script = []
        if tag == "meta":
            key = str(a.get("property") or a.get("name") or "").lower()
            if key in {"og:description", "description"} and a.get("content") and not self.description:
                self.description = a["content"]
            if key in self.meta and a.get("content"):
                self.meta[key].append(a["content"])
        if tag == "link" and "image_src" in str(a.get("rel", "")).lower().split() and a.get("href"):
            self.links.append(a["href"])
        in_article = any(scope for _, _, scope in self.stack) or article_scope
        if tag in {"img", "source"} and in_article and not excluded and not any(drop for _, drop, _ in self.stack):
            for key in ("data-src", "src", "data-srcset", "srcset"):
                value = a.get(key)
                if value:
                    if "srcset" in key:
                        value = value.split(",")[-1].strip().split()[0]
                    self.heroes.append(value)
                    break

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.script is not None:
            try:
                self.jsonld.extend(_jsonld_images(json.loads("".join(self.script))))
            except (ValueError, TypeError):
                pass
            self.script = None
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break


def image_candidates(page: str, final_url: str, feed_image: str = "", *, parser=None):
    if parser is None:
        parser = ImageParser()
        parser.feed(page)
    sources = [("rss", [feed_image])]
    sources += [(key, parser.meta[key]) for key in META_ORDER]
    sources += [("jsonld", parser.jsonld), ("image_src", parser.links), ("article_hero", parser.heroes)]
    seen = set()
    for method, values in sources:
        for value in values:
            url = urljoin(final_url, str(value).strip())
            if value and urlsplit(url).scheme in {"http", "https"} and url not in seen:
                seen.add(url)
                yield method, url


def prepare_image(page: str, final_url: str, feed_image: str, root: Path, timeout: float = 8):
    attempts = []
    parser = ImageParser()
    parser.feed(page)
    result = {"image_url": "", "image_method": "none", "image_origin": final_url,
              "image_status": "no_url_found", "image_error": "", "image_cache_path": "", "image_cache_mime": "", "article_description": parser.description}
    deadline = time.monotonic() + 20
    for method, url in image_candidates(page, final_url, feed_image, parser=parser):
        if time.monotonic() >= deadline or len(attempts) >= 16:
            break
        attempt = {"method": method, "url": url}
        try:
            request = urllib.request.Request(url, headers={**HEADERS, "Referer": final_url})
            with urllib.request.urlopen(request, timeout=min(timeout, max(0.5, deadline - time.monotonic()))) as response:
                mime = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                attempt.update(http_status=getattr(response, "status", 200), content_type=mime,
                               final_url=response.geturl() if hasattr(response, "geturl") else url)
                raw = response.read(12_000_001)
            if len(raw) > 12_000_000 or not raw:
                raise ValueError("Empty image or image exceeds 12 MB")
            if mime and not mime.startswith("image/"):
                raise ValueError(f"Non-image Content-Type: {mime}")
            with Image.open(io.BytesIO(raw)) as image:
                if image.width < 160 or image.height < 90 or image.width * image.height > 40_000_000:
                    raise ValueError("Not a representative image size")
                fmt = image.format
                image.verify()
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
            formats = {"JPEG": (".jpg", "image/jpeg"), "PNG": (".png", "image/png"), "WEBP": (".webp", "image/webp"), "GIF": (".gif", "image/gif"), "AVIF": (".avif", "image/avif")}
            suffix, mime = formats[fmt]
            filename = "image-" + hashlib.sha256(raw).hexdigest()[:40] + suffix
            root.mkdir(parents=True, exist_ok=True)
            path = root / filename
            temporary = path.with_suffix(suffix + f".{threading.get_ident()}.tmp")
            temporary.write_bytes(raw)
            os.replace(temporary, path)
            attempt["status"] = "cached"
            attempts.append(attempt)
            result.update(image_url=url, image_method=method, image_status="cached", image_cache_path=filename, image_cache_mime=mime)
            break
        except Exception as exc:
            if getattr(exc, "code", None):
                attempt["http_status"] = exc.code
            attempt.update(status="failed", error=f"{type(exc).__name__}: {exc}"[:500])
            attempts.append(attempt)
    if result["image_status"] != "cached" and attempts:
        result.update(image_status="remote_image_failed", image_error=attempts[-1].get("error", "Image attempt budget exhausted"))
    result["image_attempts"] = attempts
    return result
