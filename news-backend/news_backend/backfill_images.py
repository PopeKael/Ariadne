"""Selective image repair for existing ready cards; never rebuild the database."""
from __future__ import annotations

import argparse
import json
import urllib.request

from discovery_service.models import Article
from .service import NewsStore, _fetch_publisher


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    store = NewsStore()
    try:
        with urllib.request.urlopen("http://192.168.1.200:8788/v1/briefing?limit=200", timeout=15) as response:
            healthy_urls = {s["url"] for s in json.load(response).get("signals", []) if s.get("image_cache_url")}
        cards = (store.curated_briefing() or {}).get("articles", [])
        if args.ids:
            placeholders = ",".join("?" for _ in args.ids)
            cards = [dict(row) for row in store.db.execute(f"SELECT * FROM articles WHERE content_ready=1 AND article_id IN ({placeholders})", args.ids)]
        selected = []
        for card in cards:
            prepared = store.prepared_metadata(card["article_id"])
            if prepared.get("image_status") == "cached" or card["canonical_url"] in healthy_urls:
                continue
            if not args.ids and (card.get("image_cache_url") or (card.get("image_url") and not prepared)):
                continue
            selected.append(card)
            if len(selected) >= max(1, min(args.limit, 100)):
                break
        print(json.dumps({"apply": args.apply, "selected": [{"article_id": c["article_id"], "title": c["title"]} for c in selected]}, ensure_ascii=False), flush=True)
        if not args.apply:
            return
        for card in selected:
            # The feed hint belongs to the original article, not a downstream cache URL.
            article = Article.from_candidate({"url": card["canonical_url"], "title": card["title"], "summary": card["summary"],
                                              "source_name": card["source"], "category": card["category"], "published_at": card["published_at"],
                                              "image_url": ""})
            if article.article_id != card["article_id"]:
                raise RuntimeError("Article identity mismatch; refusing to alter record")
            ok, error = _fetch_publisher(article, store)
            metadata = store.prepared_metadata(article.article_id)
            print(json.dumps({"article_id": article.article_id, "ok": ok, "error": error,
                              "image_method": metadata.get("image_method"), "image_status": metadata.get("image_status"),
                              "image_cache_url": metadata.get("image_cache_url")}, ensure_ascii=False), flush=True)
        store.curate_top100()
    finally:
        store.close()


if __name__ == "__main__":
    main()
