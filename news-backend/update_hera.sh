#!/bin/sh
set -eu

ROOT=/volume1/docker/ariadne-news-backend
APP="$ROOT/app"
DATA="$ROOT/data"
NAME=ariadne-news-backend
NEW_IMAGE=ariadne-news-backend:0.3
DOCKER=/usr/local/bin/docker

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "run this script with sudo"
[ -x "$DOCKER" ] || fail "Docker CLI not found"
[ -f "$APP/Dockerfile" ] || fail "isolated app build context missing"
[ -f "$DATA/news.sqlite3" ] || fail "existing isolated database missing; refusing fresh install"
[ -d "$DATA/articles" ] || fail "existing Markdown cache missing"
"$DOCKER" inspect "$NAME" >/dev/null 2>&1 || fail "expected isolated service container is missing"

DISCOVERY_NAME=$("$DOCKER" ps --filter publish=8789 --format '{{.Names}}' | head -1)
SIGNAL_NAME=$("$DOCKER" ps --filter publish=8788 --format '{{.Names}}' | head -1)
[ -n "$DISCOVERY_NAME" ] || fail "could not identify running Discovery service on 8789"
[ -n "$SIGNAL_NAME" ] || fail "could not identify running Signal service on 8788"
DISCOVERY_BEFORE=$("$DOCKER" inspect -f '{{.Id}} {{.State.Status}} {{.RestartCount}}' "$DISCOVERY_NAME")
SIGNAL_BEFORE=$("$DOCKER" inspect -f '{{.Id}} {{.State.Status}} {{.RestartCount}}' "$SIGNAL_NAME")
OLD_IMAGE=$("$DOCKER" inspect -f '{{.Config.Image}}' "$NAME")
printf 'PRODUCTION_DISCOVERY_BEFORE=%s %s\n' "$DISCOVERY_NAME" "$DISCOVERY_BEFORE"
printf 'PRODUCTION_SIGNAL_BEFORE=%s %s\n' "$SIGNAL_NAME" "$SIGNAL_BEFORE"
printf 'NEWS_BACKEND_OLD_IMAGE=%s\n' "$OLD_IMAGE"

rollback() {
  "$DOCKER" rm --force "$NAME" >/dev/null 2>&1 || true
  "$DOCKER" run --detach --name "$NAME" --restart unless-stopped \
    --publish 8791:8791 \
    --mount "type=bind,src=$DATA,dst=/data" \
    "$OLD_IMAGE" >/dev/null
}

"$DOCKER" build --file "$APP/Dockerfile" --tag "$NEW_IMAGE" "$APP"
"$DOCKER" stop "$NAME"
"$DOCKER" rm "$NAME"
"$DOCKER" run --detach --name "$NAME" --restart unless-stopped \
  --publish 8791:8791 \
  --mount "type=bind,src=$DATA,dst=/data" \
  "$NEW_IMAGE"

ready=0
for _ in $(seq 1 120); do
  if curl --silent --fail http://127.0.0.1:8791/health >/dev/null; then ready=1; break; fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  "$DOCKER" logs "$NAME" 2>&1 | tail -100
  rollback
  fail "service health endpoint failed; restored previous image $OLD_IMAGE"
fi

briefing_ready=0
for _ in $(seq 1 120); do
  if curl --silent --fail http://127.0.0.1:8791/briefing >/dev/null; then briefing_ready=1; break; fi
  sleep 1
done
if [ "$briefing_ready" -ne 1 ]; then
  rollback
  fail "curator did not persist a briefing after collection; restored previous image $OLD_IMAGE"
fi

printf 'CURATOR_REPEAT_PROOF='
"$DOCKER" exec "$NAME" python -m news_backend curate-verify --runs 5
printf 'NEWS_HEALTH='
curl --silent --show-error --fail http://127.0.0.1:8791/health
printf '\nNEWS_BRIEFING='
curl --silent --show-error --fail http://127.0.0.1:8791/briefing
printf '\n'

DISCOVERY_AFTER=$("$DOCKER" inspect -f '{{.Id}} {{.State.Status}} {{.RestartCount}}' "$DISCOVERY_NAME")
SIGNAL_AFTER=$("$DOCKER" inspect -f '{{.Id}} {{.State.Status}} {{.RestartCount}}' "$SIGNAL_NAME")
printf 'PRODUCTION_DISCOVERY_AFTER=%s %s\n' "$DISCOVERY_NAME" "$DISCOVERY_AFTER"
printf 'PRODUCTION_SIGNAL_AFTER=%s %s\n' "$SIGNAL_NAME" "$SIGNAL_AFTER"
[ "$DISCOVERY_BEFORE" = "$DISCOVERY_AFTER" ] || fail "Discovery container state changed"
[ "$SIGNAL_BEFORE" = "$SIGNAL_AFTER" ] || fail "Signal container state changed"
printf 'RESULT=UPDATED_ISOLATED_NEWS_BACKEND\n'
