"""Personal, local news preferences. Immutable events; one current reaction/article.

No network/model calls. Hera remains the article store; the Interests registry
controls selection and this journal supplies learned behaviour and preserves imported legacy feedback without modifying it.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sqlite3
import threading

REACTIONS = {"useful", "interesting", "not_useful", ""}
WEIGHTS = {"useful": 0.6, "interesting": 1.0, "not_useful": -0.7, "": 0}
STOP = set("the a an and or of to in on for with from at by as is are was be it its this that new says said after over more about how why what your our their will has have news report reports update latest today story stories world".split())


def tokens(card):
    text = " ".join(str(card.get(k) or "") for k in ("title", "summary"))
    return {t.rstrip("s") for t in re.findall(r"[a-z][a-z0-9]+", text.casefold()) if len(t) > 2 and t not in STOP}


def similarity(left, right):
    shared = left & right
    return len(shared) / max(1, len(left | right)) if len(shared) >= 2 else 0.0


def interest_matches(card, configured):
    """Resolve evidence against current settings, never cached priorities.

    Signal embeds descriptions and aliases. Names/aliases also provide a
    lexical fallback when semantic matching is disabled or unavailable.
    """
    text = " ".join(str(card.get(k) or "") for k in ("title", "summary")).casefold()
    matches = []
    for interest in configured:
        if not interest.get("enabled", True):
            continue
        name = str(interest.get("name") or "")
        if not name:
            continue
        strength = 0.0
        if interest.get("semantic_enabled", True):
            for match in card.get("semantic_matches", []) or []:
                if not isinstance(match, dict):
                    continue
                match_id = match.get("interest_id")
                same = (match_id == interest.get("interest_id") if match_id else
                        (match.get("interest") or match.get("interest_name") or match.get("name")) == name)
                score = float(match.get("semantic_score") or match.get("score") or 0)
                if same and score > 0:
                    strength = max(strength, min(1.0, score))
        terms = [name, *(interest.get("aliases") or [])]
        if any(str(term).strip() and re.search(r"(?<!\w)" + re.escape(str(term).casefold()) + r"(?!\w)", text) for term in terms):
            strength = 1.0
        priority = max(0.0, min(5.0, float(interest.get("priority", 1.0))))
        if strength and priority:
            matches.append((name, priority * strength))
    return matches


def interest_labels(card, configured):
    return sorted(name for name, _ in interest_matches(card, configured))


class NewsRecommendations:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS articles (
                article_id TEXT PRIMARY KEY, card TEXT NOT NULL,
                seen INTEGER NOT NULL DEFAULT 0, opened INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
                article_id TEXT NOT NULL, reaction TEXT NOT NULL, created_at TEXT NOT NULL,
                origin TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS article_events ON events(article_id,sequence);
        """)

    def observe(self, cards):
        """Import legacy latest state once/article. Local clears always win later polls."""
        with self.lock, self.db:
            for card in cards:
                article_id = str(card.get("article_id") or "")
                if not article_id:
                    continue
                previous = self.db.execute("SELECT card FROM articles WHERE article_id=?", (article_id,)).fetchone()
                if previous:
                    card = {**json.loads(previous["card"]), **card}
                feedback = card.get("feedback") or {}
                value = str(feedback.get("value") or "")
                seen = card.get("interaction_state") == "consumed" or value in REACTIONS - {""}
                self.db.execute("""INSERT INTO articles(article_id,card,seen) VALUES(?,?,?)
                    ON CONFLICT(article_id) DO UPDATE SET card=excluded.card,
                    seen=MAX(articles.seen,excluded.seen)
                    WHERE articles.card!=excluded.card OR articles.seen<excluded.seen""",
                    (article_id, json.dumps(card, ensure_ascii=False, sort_keys=True), int(seen)))
                if value in REACTIONS - {""} and not self.db.execute(
                        "SELECT 1 FROM events WHERE article_id=? LIMIT 1", (article_id,)).fetchone():
                    self.db.execute("INSERT INTO events(event_id,article_id,reaction,created_at,origin) VALUES(?,?,?,?,?)",
                        ("legacy:" + article_id, article_id, value, str(feedback.get("updated_at") or ""), "hera_legacy"))

    def react(self, article_id, value, event_id):
        if not isinstance(value, str) or value not in REACTIONS or not isinstance(event_id, str) or not event_id or len(event_id) > 100:
            raise ValueError("A supported reaction and bounded event_id are required.")
        with self.lock, self.db:
            if not self.db.execute("SELECT 1 FROM articles WHERE article_id=?", (article_id,)).fetchone():
                raise ValueError("Article is not in the local news archive.")
            existing = self.db.execute("SELECT article_id,reaction FROM events WHERE event_id=?", (event_id,)).fetchone()
            if existing and (existing["article_id"] != article_id or existing["reaction"] != value):
                raise ValueError("event_id was already used for a different reaction.")
            self.db.execute("INSERT OR IGNORE INTO events(event_id,article_id,reaction,created_at,origin) VALUES(?,?,?,?,?)",
                (event_id, article_id, value, datetime.now(timezone.utc).isoformat(), "home"))
            self.db.execute("UPDATE articles SET seen=1 WHERE article_id=?", (article_id,))
            value = self.db.execute("SELECT reaction FROM events WHERE article_id=? ORDER BY sequence DESC LIMIT 1", (article_id,)).fetchone()[0]
        return {"ok": True, "article_id": article_id, "feedback": value,
                "persisted_locally": True, "seen": True}

    def opened(self, article_id):
        with self.lock, self.db:
            self.db.execute("UPDATE articles SET seen=1,opened=1 WHERE article_id=?", (article_id,))

    def records(self):
        with self.lock:
            rows = self.db.execute("""SELECT a.*,e.reaction,e.created_at FROM articles a
                LEFT JOIN events e ON e.sequence=(SELECT MAX(sequence) FROM events WHERE article_id=a.article_id)""").fetchall()
        return [{**json.loads(row["card"]), "seen": bool(row["seen"]), "opened": bool(row["opened"]),
                 "reaction": row["reaction"], "reaction_at": row["created_at"]} for row in rows]

    def profile(self, configured=()):
        counts = {}
        reacted = 0
        for card in self.records():
            value = card.get("reaction") or ""
            if not value:
                continue
            reacted += 1
            # A dislike is intentionally too ambiguous to penalize a broad interest.
            if value == "not_useful":
                continue
            for label in interest_labels(card, configured):
                entry = counts.setdefault(label, {"weight": 0, "evidence_count": 0})
                entry["weight"] += WEIGHTS[value]
                entry["evidence_count"] += 1
        interests = [{"label": label, "score": round(item["weight"] / (3 + item["evidence_count"]), 4),
                      "evidence_count": item["evidence_count"],
                      "state": "established evidence" if item["evidence_count"] >= 3 else "early evidence"}
                     for label, item in counts.items()]
        interests.sort(key=lambda entry: (-entry["score"], entry["label"]))
        return {"sources": [], "categories": [], "interests": interests, "rated_articles": reacted,
                "owner": "local_news_journal", "policy": "conservative-content-v1"}

    def rank(self, cards, configured=(), limit=100):
        self.observe(cards)
        records = self.records()
        by_id = {card["article_id"]: card for card in records}
        evidence = [(card, tokens(card), set(interest_labels(card, configured)))
                    for card in records if card.get("reaction") or card.get("opened")]
        candidates = []
        for original_position, card in enumerate(cards):
            item = dict(card)
            item["recommendation_input_position"] = original_position
            saved = by_id[item["article_id"]]
            item["interaction_state"] = "consumed" if saved["seen"] else "unseen"
            item["feedback"] = {"value": saved.get("reaction") or "", "updated_at": saved.get("reaction_at") or ""}
            terms = tokens(item)
            labels = interest_labels(item, configured)
            strength = 0.0
            evidence_count = 0
            for example, example_terms, example_labels in evidence:
                if example["article_id"] == item["article_id"]:
                    continue  # Rating teaches other stories, never boosts this one.
                overlap = similarity(terms, example_terms)
                value = example.get("reaction") or ""
                if value in {"useful", "interesting"}:
                    common = set(labels) & example_labels
                    overlap = max(overlap, .25 * len(common) / max(1, len(set(labels) | example_labels)))
                threshold = 0.35 if value == "not_useful" else 0.12
                if overlap >= threshold:
                    strength += overlap * (WEIGHTS[value] if value else 0.08)
                    evidence_count += 1
            base = float(item.get("briefing_score") or item.get("rank_score") or 0)
            stamp = item.get("published_at") or item.get("discovered_at")
            if stamp:
                try:
                    age = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))).total_seconds() / 3600)
                    base = min(base, max(0, 100 - age * 1.5))
                except (ValueError, TypeError):
                    pass
            interest_score = 12 * max((weight for _, weight in interest_matches(item, configured)), default=0.0)
            item["interest_rank_score"] = round(interest_score, 4)
            score = base + interest_score + max(-8, min(8, 12 * strength / (3 + evidence_count)))
            item["local_rank_score"] = round(score, 4)
            reasons = ["Seen" if saved["seen"] else "Unseen"]
            if labels:
                reasons.append("Matches " + ", ".join(labels[:2]))
            else:
                reasons.append("Wider news / discovery")
            if evidence_count:
                reasons.append("Related to your feedback" if strength >= 0 else "Similar to a story you declined")
            reasons.append("Recent" if base >= 70 else "Earlier coverage")
            item["recommendation_reasons"] = reasons
            candidates.append((item, terms))
        selected = []
        # Hard unseen/seen partition, then a greedy diversity pass inside each.
        for seen in (False, True):
            pool = [(item, terms) for item, terms in candidates if (item["interaction_state"] == "consumed") == seen]
            sources, categories = Counter(), Counter()
            while pool and len(selected) < limit:
                def adjusted(pair):
                    item, terms = pair
                    repeat = max((similarity(terms, chosen_terms) for _, chosen_terms in selected[-8:]), default=0)
                    return (item["local_rank_score"] - sources[str(item.get("source") or "")] * 3
                            - categories[str(item.get("category") or "")] * 1.5 - repeat * 16,
                            item["local_rank_score"], -item["recommendation_input_position"])
                # Retain breadth even with many high-priority stories from one
                # publisher. Relax only when alternatives run out.
                source_cap = max(3, math.ceil(min(limit, len(candidates)) * 0.4))
                eligible = [pair for pair in pool if sources[str(pair[0].get("source") or "")] < source_cap]
                if not eligible:
                    least_used = min(sources[str(pair[0].get("source") or "")] for pair in pool)
                    eligible = [pair for pair in pool if sources[str(pair[0].get("source") or "")] == least_used]
                winner = max(eligible, key=adjusted)
                pool.remove(winner)
                item, _ = winner
                sources[str(item.get("source") or "")] += 1
                categories[str(item.get("category") or "")] += 1
                selected.append(winner)
        return [{**item, "position": index, "local_ranked": True} for index, (item, _) in enumerate(selected, 1)]

    def seen_cards(self, limit=100):
        return [card for card in reversed(self.records()) if card["seen"]][:limit]
