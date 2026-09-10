"""Deterministic story clustering and diversity-aware ordering."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from .models import Article, source_domain, tokens


def _timestamp(value: str) -> float:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return 0.0


def _similarity(left: Article, right: Article) -> float:
    left_title = tokens(left.title)
    right_title = tokens(right.title)
    left_body = tokens(f"{left.title} {left.summary}")
    right_body = tokens(f"{right.title} {right.summary}")
    if not left_title or not right_title:
        return 0.0
    title_intersection = len(left_title & right_title)
    title_union = len(left_title | right_title)
    body_intersection = len(left_body & right_body)
    body_union = len(left_body | right_body)
    title_score = title_intersection / title_union if title_union else 0.0
    body_score = body_intersection / body_union if body_union else 0.0
    common = left_title & right_title
    return title_score * 0.72 + body_score * 0.28 if len(common) >= 2 else title_score * 0.45 + body_score * 0.55


def cluster_articles(articles: Iterable[Article], *, max_age_hours: int = 96, threshold: float = 0.28) -> list[list[Article]]:
    ordered = sorted(articles, key=lambda item: (_timestamp(item.published_at), _timestamp(item.discovered_at)), reverse=True)
    clusters: list[list[Article]] = []
    for article in ordered:
        placed = False
        for cluster in clusters:
            representative = cluster[0]
            if abs(_timestamp(article.published_at) - _timestamp(representative.published_at)) > max_age_hours * 3600:
                continue
            if _similarity(article, representative) >= threshold or any(_similarity(article, member) >= threshold + 0.06 for member in cluster[:4]):
                cluster.append(article)
                placed = True
                break
        if not placed:
            clusters.append([article])
    return clusters


def _independent_sources(cluster: list[Article]) -> tuple[list[str], list[str]]:
    domains: set[str] = set()
    evidence_signatures: set[tuple[str, str]] = set()
    names: set[str] = set()
    for article in cluster:
        domain = source_domain(article.source_url) or source_domain(article.canonical_url) or article.source_name.casefold()
        domains.add(domain)
        names.add(article.source_name)
        evidence_signatures.add((domain, article.content_hash))
    # Identical copied content across domains is one evidence stream, not many.
    distinct_content = {content_hash for _, content_hash in evidence_signatures}
    independent_count = min(len(domains), len(distinct_content)) if distinct_content else len(domains)
    return sorted(names, key=str.casefold), sorted(domains, key=str.casefold)[: max(1, independent_count)]


def story_from_cluster(cluster: list[Article]) -> dict[str, Any]:
    best = max(cluster, key=lambda item: (len(tokens(item.title)), len(item.summary), _timestamp(item.published_at)))
    anchor = min(cluster, key=lambda item: (_timestamp(item.published_at), _timestamp(item.discovered_at), item.article_id))
    summary = max((item.summary for item in cluster if item.summary), key=len, default=best.title)
    source_names, source_domains = _independent_sources(cluster)
    categories = sorted({item.category for item in cluster})
    unique_article_ids = list(dict.fromkeys(item.article_id for item in cluster))
    return {
        # The earliest observed article anchors the story. New corroboration
        # can therefore update a story without creating a second Signal card.
        "story_id": "story-" + anchor.article_id.removeprefix("article-"),
        "title": best.title,
        "summary": summary[:4_000],
        "url": anchor.canonical_url,
        "representative_url": best.canonical_url,
        "image_url": next((item.image_url for item in cluster if item.image_url), ""),
        "category": best.category,
        "categories": categories,
        "published_at": max((item.published_at for item in cluster), key=_timestamp, default=best.published_at),
        "first_seen_at": min((item.discovered_at for item in cluster), key=_timestamp, default=best.discovered_at),
        "last_seen_at": max((item.discovered_at for item in cluster), key=_timestamp, default=best.discovered_at),
        "article_count": len(unique_article_ids),
        "source_count": len(source_domains),
        "source_names": source_names,
        "source_domains": source_domains,
        "article_ids": unique_article_ids,
        "evidence": [{"title": item.title, "url": item.canonical_url, "source_name": item.source_name, "source_domain": source_domain(item.source_url) or source_domain(item.canonical_url), "published_at": item.published_at} for item in cluster[:12]],
        "rank_score": 0.0,
    }


def rank_stories(stories: Iterable[dict[str, Any]], *, now: float | None = None) -> list[dict[str, Any]]:
    current = now or datetime.now(timezone.utc).timestamp()
    values = []
    for story in stories:
        age_hours = max(0.0, (current - _timestamp(str(story.get("published_at") or ""))) / 3600)
        freshness = max(0.0, 1.0 - min(age_hours, 168.0) / 168.0)
        corroboration = min(1.0, max(0, int(story.get("source_count") or 0)) / 5.0)
        novelty = 1.0 if int(story.get("article_count") or 0) == 1 else 0.8
        score = freshness * 0.5 + corroboration * 0.35 + novelty * 0.15
        item = dict(story)
        item["rank_score"] = round(score, 4)
        values.append(item)
    return sorted(values, key=lambda item: (-float(item["rank_score"]), -int(item.get("source_count") or 0), str(item.get("story_id"))))


def diversify(stories: Iterable[dict[str, Any]], *, limit: int = 80) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for story in stories:
        bucket = str(story.get("category") or "Main News Feed")
        buckets.setdefault(bucket, []).append(story)
    ordered_buckets = sorted(buckets, key=lambda name: (-max(float(item.get("rank_score") or 0) for item in buckets[name]), name.casefold()))
    result: list[dict[str, Any]] = []
    while ordered_buckets and len(result) < max(1, int(limit)):
        next_buckets = []
        for bucket in ordered_buckets:
            values = buckets[bucket]
            if values:
                result.append(values.pop(0))
                if len(result) >= limit:
                    break
            if values:
                next_buckets.append(bucket)
        ordered_buckets = next_buckets
    return result


__all__ = ["cluster_articles", "diversify", "rank_stories", "story_from_cluster"]
