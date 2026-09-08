# Ariadne Signal Service

The Signal Service is a portable, independently running information refinery.
It gathers RSS/Atom candidates, normalizes them into canonical signals, removes
duplicates, applies deterministic basic ranking, and stores a ready-to-render
briefing in SQLite.

## Run locally

From this directory:

```text
python run.py
```

The service listens on `http://127.0.0.1:8788` by default. It performs a
background refresh on startup and every 15 minutes. Configure feeds with
`SIGNAL_SERVICE_FEEDS`, using `Name|URL` entries separated by commas.

## API

- `GET /v1/health` reports configured sources, collection state, and cache state.
- `GET /v1/briefing?limit=30` returns the cached briefing. If collection has
  failed, the last successful briefing is returned with `stale: true` and the
  source errors.
- `POST /v1/intake/candidates` accepts a candidate list or common wrappers such
  as `{ "items": [...] }`, `{ "candidates": [...] }`, or one candidate object.
- Intake candidates may include `category` (`Main News Feed`, `Thailand Focus`,
  `AI Watch`, or `Watchlist`). The existing n8n intake path defaults to
  `Thailand Focus` when no category is supplied, so the current news workflow
  can remain the producer without a schema rewrite.
- `GET /v1/watchlist/topics` returns active persistent topics.
- `POST /v1/watchlist/topics` accepts `{ "topic": "...", "active": true }`
  and stores the topic for future local collection.

Candidates may use common names such as `title`/`headline`, `url`/`link`,
`summary`/`description`, and `published_at`/`published`/`pubDate`. The original
source URL and feed provenance are retained. The endpoint is intentionally
tolerant of n8n-shaped wrappers without making n8n a service dependency.

## Docker / Synology

Copy `compose.example.yml` to a deployment directory and run `docker compose
up -d --build`. Keep `/data` on persistent storage. The default compose bind
address is suitable for a private Docker host; add network authentication or a
reverse proxy before exposing it beyond the trusted local network.

The service has no Ariadne or n8n dependency. Ariadne calls it through the
small client in `control-plane/signal_service_client.py`; n8n can POST into the
candidate intake endpoint when its exact current output shape is confirmed.

The `SignalRanker` protocol in `signal_service/ranking.py` is the extension
point for future profile-aware ranking. v0.1 deliberately uses only recency,
content completeness, title quality, and source diversity.
