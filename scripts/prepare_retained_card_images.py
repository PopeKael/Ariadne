"""Warm shared preparation for only missing images on the actual Home cards."""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'control-plane'))
from news_briefing_cache import NewsBriefingCache

with urllib.request.urlopen('http://localhost:8765/api/news/briefing-snapshot', timeout=15) as response:
    snapshot = json.load(response)
cache = NewsBriefingCache(cache_path=ROOT / 'runtime' / 'retained-image-preparation-proof.json')
cache._briefing = snapshot['briefing']
for _ in range(13):
    result = cache.sync_prepared_images()
    print(json.dumps(result), flush=True)
    if not result['attempted'] or not result['updated']:
        break
cards = cache.snapshot()['briefing']['articles']
print(json.dumps({'cards': len(cards), 'with_images': sum(bool(c.get('image_cache_url') or c.get('image_url')) for c in cards),
                  'missing': [{'article_id': c['article_id'], 'title': c['title'], 'status': c.get('image_status')} for c in cards if not (c.get('image_cache_url') or c.get('image_url'))]}), flush=True)
