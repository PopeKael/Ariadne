"""HTTP sidecar for the article-cache spike; it does not replace Discovery."""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from .article_cache import ArticleCache


class ArticleCacheHandler(BaseHTTPRequestHandler):
    server: "ArticleCacheHTTPServer"
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send(self, payload: object, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _article_id(self) -> str | None:
        prefix = "/v1/cache/articles/"
        path = urlparse(self.path).path
        if not path.startswith(prefix):
            return None
        value = unquote(path.removeprefix(prefix)).strip("/")
        return value if value and "/" not in value else None

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/v1/cache/health":
            self._send(self.server.cache.health())
            return
        article_id = self._article_id()
        if article_id:
            entry, markdown = self.server.cache.retrieve(article_id)
            if not entry or markdown is None:
                self._send({"ok": False, "article_id": article_id, "error": "Article is not cached."}, 404)
                return
            self._send({"ok": True, "article": entry, "markdown": markdown, "retrieval": {"storage": "local_file", "network_fetch": False}})
            return
        self._send({"ok": False, "message": "Not found."}, 404)

    def do_POST(self) -> None:  # noqa: N802
        article_id = self._article_id()
        if not article_id:
            self._send({"ok": False, "message": "Not found."}, 404)
            return
        result = self.server.cache.populate(article_id)
        self._send(result, 200 if result.get("ok") else 502)


class ArticleCacheHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], cache: ArticleCache):
        super().__init__(address, ArticleCacheHandler)
        self.cache = cache


def main() -> None:
    cache = ArticleCache(
        os.environ.get("ARTICLE_CACHE_SOURCE_DATABASE", "/source/discovery.sqlite3"),
        os.environ.get("ARTICLE_CACHE_INDEX_DATABASE", "/cache/article-cache.sqlite3"),
        os.environ.get("ARTICLE_CACHE_ROOT", "/cache"),
    )
    server = ArticleCacheHTTPServer((os.environ.get("ARTICLE_CACHE_BIND_ADDRESS", "0.0.0.0"), int(os.environ.get("ARTICLE_CACHE_PORT", "8790"))), cache)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        cache.close()


if __name__ == "__main__":
    main()
