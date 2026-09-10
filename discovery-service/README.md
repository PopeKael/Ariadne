# Ariadne Discovery Engine

This is the always-on collector beside the Ariadne Signal Service. It runs on
Hera, retains its own SQLite state, and sends materialized story cards to the
Signal Service intake. Ariadne Home only consumes the Signal Service briefing.

The service does not run an LLM. Every refresh is bounded and deterministic:

```text
RSS/Atom sources + optional SearXNG
  -> conditional polling
  -> URL/content normalization
  -> rolling article store
  -> deterministic story clustering
  -> corroboration and diversity-aware ranking
  -> Signal Service intake
```

## Runtime contract

- `GET /v1/health` reports source, article, story, refresh, and push state.
- `GET /v1/stories?limit=240` exposes the materialized stories for diagnostics.
- `GET /v1/sources` exposes polling state without credentials.
- `POST /v1/refresh` runs one bounded refresh for an operator or scheduler.
- Background refresh starts immediately and repeats every 900 seconds by default.
- Source `ETag` and `Last-Modified` values are retained. Failed sources do not
  stop the rest of the refresh.
- `source_count` means distinct source domains with distinct content hashes;
  copied versions of the same article are not counted repeatedly.

The default catalog contains broad coverage rather than only AI feeds. Add
more sources by mounting a `sources.json` in the same shape as
`sources.example.json`. Hundreds of sources are supported; concurrency and
per-source item limits keep the NAS workload bounded.

## Hera deployment

Create a separate Container Manager project from this directory. Keep
`./data` on the NAS for persistence and confirm the existing Signal Service is
reachable at `http://192.168.1.200:8788` before enabling the push URL. The
included compose example uses port `8789` and a 15-minute interval.

SearXNG is optional. Set `DISCOVERY_SERVICE_SEARXNG_URL` only after its actual
NAS endpoint has been confirmed. The engine remains useful with feeds alone.

After startup, verify:

```text
http://192.168.1.200:8789/v1/health
http://192.168.1.200:8789/v1/stories?limit=10
```

Then check the existing Signal Service briefing and confirm that its
`source_status` still represents the Signal Service itself. Discovery push
failures are visible in Discovery health and do not erase the last materialized
stories.
