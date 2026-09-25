from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

from news_backend.service import NewsStore


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: verify_local_retrieval.py ARTICLE_ID [ARTICLE_ID ...]", file=sys.stderr)
        return 2
    attempted: list[str] = []

    def deny_http(request: object, *args: object, **kwargs: object) -> object:
        attempted.append(str(request))
        raise AssertionError("publisher HTTP fetch attempted during local retrieval")

    urllib.request.urlopen = deny_http  # type: ignore[assignment]
    data_root = Path(__import__("os").environ.get("NEWS_BACKEND_DATA", "/data"))
    # The live container stores /data/... paths in SQLite. This host-side check
    # maps that mountpoint to the same bind-mounted Hera directory.
    resolve_path = Path.resolve

    def resolve_container_data_path(path: Path, strict: bool = False) -> Path:
        if path.as_posix().startswith("/data/articles/"):
            path = data_root / "articles" / path.name
        return resolve_path(path, strict=strict)

    Path.resolve = resolve_container_data_path  # type: ignore[method-assign]
    store = NewsStore(data_root, read_only=True)
    results: list[dict[str, object]] = []
    elapsed: list[float] = []
    try:
        for article_id in sys.argv[1:]:
            started = time.perf_counter_ns()
            metadata, markdown = store.get_article(article_id)
            elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
            elapsed.append(elapsed_ms)
            results.append({
                "article_id": article_id,
                "found": metadata is not None,
                "content_ready": bool(metadata and metadata["content_ready"]),
                "markdown_bytes": len((markdown or "").encode("utf-8")),
                "hash_valid": bool(metadata and markdown and hashlib.sha256(markdown.encode("utf-8")).hexdigest() == metadata["content_hash"]),
                "retrieval_ms": round(elapsed_ms, 3),
            })
    finally:
        store.close()
    payload = {
        "retrieval": "local SQLite metadata + Markdown file",
        "articles": results,
        "publisher_http_attempts": attempted,
        "publisher_http_attempt_count": len(attempted),
        "average_retrieval_ms": round(sum(elapsed) / len(elapsed), 3) if elapsed else None,
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if all(row["found"] and row["content_ready"] and row["markdown_bytes"] and row["hash_valid"] for row in results) and not attempted else 1


if __name__ == "__main__":
    raise SystemExit(main())
