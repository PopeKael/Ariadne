#!/bin/sh
set -eu
DOCKER=/usr/local/bin/docker
NAME=ariadne-news-backend

# First attach only the three known news/sports/video preparation records.
"$DOCKER" exec "$NAME" python -m news_backend.backfill_images --apply --ids \
  article-b20912b2756931d3e387af6647fc \
  article-da9b367cf3d8f9c12d572a19dda1 \
  article-081666fa998bfd84aa7f08f3c710

# Prove those cards expose real local images before the bounded backfill.
python3 - <<'PY'
import json, urllib.request
ids = {'article-b20912b2756931d3e387af6647fc', 'article-da9b367cf3d8f9c12d572a19dda1', 'article-081666fa998bfd84aa7f08f3c710'}
with urllib.request.urlopen('http://127.0.0.1:8791/briefing', timeout=10) as response:
    cards = json.load(response)['articles']
found = set()
for card in cards:
    if card['article_id'] not in ids:
        continue
    assert card.get('prepared_version') == 1 and card.get('image_status') == 'cached', card['article_id']
    with urllib.request.urlopen(card['image_cache_url'], timeout=10) as response:
        mime, raw = response.headers.get_content_type(), response.read()
        assert mime.startswith('image/') and len(raw) > 1000, '%s: content-type=%s bytes=%s' % (card['article_id'], mime, len(raw))
    found.add(card['article_id'])
    print('%s | %s | cached | OK' % (card['title'], card['image_method']))
assert found == ids, 'Known articles missing from briefing; stop and inspect before broadening backfill'
PY

# Only current briefing cards missing images, excluding healthy caches.
# This does not scan or rebuild the full news database.
"$DOCKER" exec "$NAME" python -m news_backend.backfill_images --limit 100 --apply
printf 'BACKFILL_COMPLETE\n'
