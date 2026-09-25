#!/bin/sh
set -eu

ROOT=/volume1/docker/ariadne-news-backend
NAME=ariadne-news-backend
DOCKER=/usr/local/bin/docker

fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "run this script through sudo on Hera"
[ -x "$DOCKER" ] || fail "Docker CLI not found"

printf 'STAGE_TWO_DEPLOY_BEGIN\n'
sh "$ROOT/update_hera.sh"

ready=0
for _ in $(seq 1 360); do
  logs=$("$DOCKER" logs "$NAME" 2>&1 || true)
  if printf '%s\n' "$logs" | grep -q '^COLLECTION ' && printf '%s\n' "$logs" | grep -q '^CURATOR '; then
    ready=1
    break
  fi
  sleep 1
done
[ "$ready" -eq 1 ] || { "$DOCKER" logs --tail 120 "$NAME" 2>&1; fail "fresh collection/curation cycle did not finish within six minutes"; }

printf '\nCOLLECTION_CYCLE_2='
"$DOCKER" exec "$NAME" python -m news_backend collect-once
printf '\nCOLLECTION_CYCLE_3='
"$DOCKER" exec "$NAME" python -m news_backend collect-once
printf '\nCURATOR_LOCAL_ONLY_PROOF='
"$DOCKER" exec "$NAME" python -m news_backend curate-verify --runs 5
printf '\nNEWS_HEALTH='
curl --silent --show-error --fail http://127.0.0.1:8791/health
printf '\nNEWS_BRIEFING_TOP_15='
"$DOCKER" exec "$NAME" python -c 'import json,urllib.request; p=json.load(urllib.request.urlopen("http://127.0.0.1:8791/briefing",timeout=5)); [print("{rank} | {source} | {title} | {summary} | image={image} | {article_id}".format(rank=i,source=a["source"],title=a["title"],summary=a["summary"],image="yes" if a.get("image_url") else "no",article_id=a["article_id"])) for i,a in enumerate(p.get("articles",[])[:15],1)]'
printf '\nNEWS_CONTAINER_MEMORY='
"$DOCKER" stats --no-stream --format '{{.Name}} {{.MemUsage}} {{.MemPerc}}' "$NAME"
printf '\nRESULT=STAGE_TWO_ACCEPTANCE_RUN_COMPLETE\n'
