#!/bin/sh
set -eu

ROOT=/volume1/docker/ariadne-article-cache-spike
APP="$ROOT/app"
DATA="$ROOT/data"
SOURCE=/volume1/docker/ariadne-discovery-service/discovery-service/data
NAME=ariadne-article-cache-spike
IMAGE=ariadne-article-cache-spike:0.1
DOCKER=/usr/local/bin/docker
ARTICLE_ID=article-fdb3b07fc84b1ddad3c0a0e47e30
REPORT="$ROOT/result.txt"

umask 022
fail() { printf 'FAIL: %s\n' "$*" | tee -a "$REPORT"; exit 1; }

mkdir -p "$DATA"
: > "$REPORT"
printf 'SPIKE_ROOT=%s\nSOURCE_DATA=%s\nARTICLE_ID=%s\n' "$ROOT" "$SOURCE" "$ARTICLE_ID" | tee -a "$REPORT"

[ -f "$APP/article-cache.Dockerfile" ] || fail "build context is missing"
[ -f "$SOURCE/discovery.sqlite3" ] || fail "Discovery database is missing at the read-only source path"
[ -x "$DOCKER" ] || fail "Docker CLI not found at $DOCKER"

if "$DOCKER" inspect "$NAME" >/dev/null 2>&1; then
  fail "container $NAME already exists; refusing to replace or restart it"
fi
if "$DOCKER" ps --format '{{.Ports}}' | grep -Eq '(^|,|[[:space:]])(0\.0\.0\.0:|\[::\]:)?8790->|127\.0\.0\.1:8790->'; then
  fail "host port 8790 is already published; refusing to interfere"
fi

"$DOCKER" ps --format '{{.Names}} {{.Image}} {{.Ports}}' | tee "$ROOT/production-containers-before.txt"
DISCOVERY_NAME=$("$DOCKER" ps --filter publish=8789 --format '{{.Names}}' | head -1)
SIGNAL_NAME=$("$DOCKER" ps --filter publish=8788 --format '{{.Names}}' | head -1)
[ -n "$DISCOVERY_NAME" ] || fail "no running container publishes Discovery port 8789"
[ -n "$SIGNAL_NAME" ] || fail "no running container publishes Signal port 8788"
DISCOVERY_STATE=$("$DOCKER" inspect -f '{{.Id}} {{.State.Status}} {{.RestartCount}}' "$DISCOVERY_NAME")
SIGNAL_STATE=$("$DOCKER" inspect -f '{{.Id}} {{.State.Status}} {{.RestartCount}}' "$SIGNAL_NAME")
printf 'PRODUCTION_DISCOVERY_CONTAINER_BEFORE=%s %s\n' "$DISCOVERY_NAME" "$DISCOVERY_STATE" | tee -a "$REPORT"
printf 'PRODUCTION_SIGNAL_CONTAINER_BEFORE=%s %s\n' "$SIGNAL_NAME" "$SIGNAL_STATE" | tee -a "$REPORT"

"$DOCKER" build --file "$APP/article-cache.Dockerfile" --tag "$IMAGE" "$APP"
"$DOCKER" run --detach \
  --name "$NAME" \
  --restart unless-stopped \
  --publish 8790:8790 \
  --mount "type=bind,src=$SOURCE,dst=/source,readonly" \
  --mount "type=bind,src=$DATA,dst=/cache" \
  "$IMAGE"

ready=0
for _ in $(seq 1 30); do
  if curl --silent --fail http://127.0.0.1:8790/v1/cache/health >/dev/null; then ready=1; break; fi
  sleep 1
done
  [ "$ready" -eq 1 ] || { "$DOCKER" logs "$NAME" 2>&1 | tail -80; fail "sidecar health check timed out"; }

printf 'CACHE_HEALTH=' | tee -a "$REPORT"
curl --silent --show-error --fail http://127.0.0.1:8790/v1/cache/health | tee -a "$REPORT"
printf '\n' | tee -a "$REPORT"

POPULATE=$(curl --silent --show-error --max-time 40 --request POST \
  "http://127.0.0.1:8790/v1/cache/articles/$ARTICLE_ID") || fail "populate request failed"
printf 'POPULATE_RESULT=%s\n' "$POPULATE" | tee -a "$REPORT"
printf '%s' "$POPULATE" | python3 -c 'import json,sys; r=json.load(sys.stdin); sys.exit(0 if r.get("ok") and r.get("content_ready") else 1)' \
  || fail "publisher fetch/extraction/cache write did not succeed"

GET_TIME=$(curl --silent --show-error --max-time 10 --output /tmp/article-cache-get.json \
  --write-out '%{time_total}' "http://127.0.0.1:8790/v1/cache/articles/$ARTICLE_ID") \
  || fail "local HTTP retrieval request failed"
GET_CHECK=$(python3 -c 'import json,os,sys; r=json.load(open("/tmp/article-cache-get.json",encoding="utf-8")); m=r.get("markdown",""); ok=r.get("ok") and r.get("retrieval",{}).get("storage")=="local_file" and r.get("retrieval",{}).get("network_fetch") is False and len(m.encode("utf-8"))>0; print("ok=%s bytes=%s network_fetch=%s storage=%s"%(ok,len(m.encode("utf-8")),r.get("retrieval",{}).get("network_fetch"),r.get("retrieval",{}).get("storage"))); sys.exit(0 if ok else 1)') \
  || fail "HTTP retrieval did not return local Markdown"
printf 'HTTP_RETRIEVAL=%s\nHTTP_RETRIEVAL_SECONDS=%s\n' "$GET_CHECK" "$GET_TIME" | tee -a "$REPORT"
rm -f /tmp/article-cache-get.json

# Prove the retrieval method succeeds in a container with networking disabled,
# while urlopen is instrumented to fail if the code attempts an HTTP fetch.
OFFLINE_RESULT=$("$DOCKER" run --rm --network none \
  --mount "type=bind,src=$DATA,dst=/cache,readonly" \
  --entrypoint python "$IMAGE" -c \
  'import hashlib,time,urllib.request; from discovery_service.article_cache import ArticleCache; calls=[]; urllib.request.urlopen=lambda *a,**k: (calls.append(1), (_ for _ in ()).throw(AssertionError("network fetch attempted")))[1]; c=ArticleCache("/not-opened/discovery.sqlite3","/cache/article-cache.sqlite3","/cache",read_only=True); t=time.perf_counter(); e,m=c.retrieve("article-fdb3b07fc84b1ddad3c0a0e47e30"); ms=(time.perf_counter()-t)*1000; print("offline_ok=%s bytes=%s network_fetch_calls=%s retrieval_ms=%.3f hash_match=%s"%(bool(e and m),len((m or "").encode()),len(calls),ms,bool(e and m and hashlib.sha256(m.encode()).hexdigest()==e["content_hash"]))); c.close(); raise SystemExit(0 if e and m and not calls else 1)') \
  || fail "offline retrieval proof failed"
printf 'OFFLINE_RETRIEVAL=%s\n' "$OFFLINE_RESULT" | tee -a "$REPORT"

printf 'CACHE_FILE=' | tee -a "$REPORT"
wc -c "$DATA/articles/$ARTICLE_ID.md" | tee -a "$REPORT"
printf 'CACHE_SHA256=' | tee -a "$REPORT"
sha256sum "$DATA/articles/$ARTICLE_ID.md" | tee -a "$REPORT"
printf 'INDEX_ROW=' | tee -a "$REPORT"
python3 -c 'import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print(c.execute("select article_id,title,source,url,published_at,summary,image_url,markdown_path,content_hash,content_ready,scraped_at,scrape_error from article_cache where article_id=?",(sys.argv[2],)).fetchone()); c.close()' "$DATA/article-cache.sqlite3" "$ARTICLE_ID" | tee -a "$REPORT"

printf 'PRODUCTION_DISCOVERY_CONTAINER_AFTER=%s ' "$DISCOVERY_NAME" | tee -a "$REPORT"
"$DOCKER" inspect -f '{{.Id}} {{.State.Status}} {{.RestartCount}}' "$DISCOVERY_NAME" | tee -a "$REPORT"
printf 'PRODUCTION_SIGNAL_CONTAINER_AFTER=%s ' "$SIGNAL_NAME" | tee -a "$REPORT"
"$DOCKER" inspect -f '{{.Id}} {{.State.Status}} {{.RestartCount}}' "$SIGNAL_NAME" | tee -a "$REPORT"

printf 'RESULT=PASS\n' | tee -a "$REPORT"
