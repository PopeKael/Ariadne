#!/bin/sh
set -eu

ROOT=/volume1/docker/ariadne-news-backend
APP="$ROOT/app"
DATA="$ROOT/data"
NAME=ariadne-news-backend
IMAGE=ariadne-news-backend:0.1
DOCKER=/usr/local/bin/docker

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

[ -x "$DOCKER" ] || fail "Docker CLI not found"
[ -f "$APP/Dockerfile" ] || fail "isolated app build context missing"
[ -d "$DATA/articles" ] || fail "new Markdown directory missing"
[ ! -e "$DATA/news.sqlite3" ] || fail "refusing to overwrite a pre-existing database"
if "$DOCKER" inspect "$NAME" >/dev/null 2>&1; then fail "container $NAME already exists; refusing to replace it"; fi
if "$DOCKER" ps --filter publish=8791 --format '{{.Names}}' | grep -q .; then fail "port 8791 is already in use"; fi

DISCOVERY_NAME=$("$DOCKER" ps --filter publish=8789 --format '{{.Names}}' | head -1)
SIGNAL_NAME=$("$DOCKER" ps --filter publish=8788 --format '{{.Names}}' | head -1)
[ -n "$DISCOVERY_NAME" ] || fail "could not identify running Discovery service on 8789"
[ -n "$SIGNAL_NAME" ] || fail "could not identify running Signal service on 8788"
DISCOVERY_BEFORE=$("$DOCKER" inspect -f '{{.Id}} {{.State.Status}} {{.RestartCount}}' "$DISCOVERY_NAME")
SIGNAL_BEFORE=$("$DOCKER" inspect -f '{{.Id}} {{.State.Status}} {{.RestartCount}}' "$SIGNAL_NAME")
printf 'PRODUCTION_DISCOVERY_BEFORE=%s %s\n' "$DISCOVERY_NAME" "$DISCOVERY_BEFORE"
printf 'PRODUCTION_SIGNAL_BEFORE=%s %s\n' "$SIGNAL_NAME" "$SIGNAL_BEFORE"

"$DOCKER" build --file "$APP/Dockerfile" --tag "$IMAGE" "$APP"
"$DOCKER" run --detach --name "$NAME" --restart unless-stopped \
  --publish 8791:8791 \
  --mount "type=bind,src=$DATA,dst=/data" \
  "$IMAGE"

ready=0
for _ in $(seq 1 90); do
  if curl --silent --fail http://127.0.0.1:8791/health >/dev/null; then ready=1; break; fi
  sleep 1
done
[ "$ready" -eq 1 ] || { "$DOCKER" logs "$NAME" 2>&1 | tail -80; fail "health endpoint did not become ready"; }

DISCOVERY_AFTER=$("$DOCKER" inspect -f '{{.Id}} {{.State.Status}} {{.RestartCount}}' "$DISCOVERY_NAME")
SIGNAL_AFTER=$("$DOCKER" inspect -f '{{.Id}} {{.State.Status}} {{.RestartCount}}' "$SIGNAL_NAME")
printf 'PRODUCTION_DISCOVERY_AFTER=%s %s\n' "$DISCOVERY_NAME" "$DISCOVERY_AFTER"
printf 'PRODUCTION_SIGNAL_AFTER=%s %s\n' "$SIGNAL_NAME" "$SIGNAL_AFTER"
[ "$DISCOVERY_BEFORE" = "$DISCOVERY_AFTER" ] || fail "Discovery container state changed during deployment"
[ "$SIGNAL_BEFORE" = "$SIGNAL_AFTER" ] || fail "Signal container state changed during deployment"
printf 'NEWS_HEALTH='; curl --silent --show-error --fail http://127.0.0.1:8791/health; printf '\n'
printf 'RESULT=DEPLOYED\n'
