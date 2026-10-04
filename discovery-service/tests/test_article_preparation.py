"""Exercise real HTTP boundaries: publisher -> preparation -> Signal -> Home cards."""
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from collections import Counter
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
for folder in ("discovery-service", "signal-service", "news-backend", "control-plane"):
    sys.path.insert(0, str(ROOT / folder))

from discovery_service.article_cache import ArticleCache
from discovery_service.article_images import image_candidates, prepare_image
from discovery_service.cache_app import ArticleCacheHTTPServer
from discovery_service.engine import DiscoveryEngine
from discovery_service.models import Article
from news_backend.service import NewsStore, _fetch_publisher
from signal_service.service import SignalService
from news_briefing_cache import NewsBriefingCache, _merge_cards


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        buffer = io.BytesIO()
        Image.new("RGB", (640, 360), "blue").save(buffer, "JPEG")
        self.image = buffer.getvalue()
        self.calls = Counter()
        self.pages = {}
        owner = self

        class Publisher(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                owner.calls[self.path] += 1
                if self.path == "/bad.jpg":
                    raw, mime = b"<html>Not an image</html>", "image/jpeg"
                elif self.path == "/lead.jpg":
                    raw, mime = owner.image, "image/jpeg"
                elif self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/news/story")
                    self.end_headers()
                    return
                else:
                    raw, mime = owner.pages.get(self.path, b""), "text/html"
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.publisher = ThreadingHTTPServer(("127.0.0.1", 0), Publisher)
        self.publisher_thread = threading.Thread(target=self.publisher.serve_forever, daemon=True)
        self.publisher_thread.start()
        self.base = f"http://localhost:{self.publisher.server_port}"

    def tearDown(self):
        self.publisher.shutdown()
        self.publisher.server_close()
        self.publisher_thread.join()
        self.temp.cleanup()

    def test_cached_image_mime_is_independent_of_host_registry(self):
        cache = ArticleCache(self.root / "unused", self.root / "index.sqlite3", self.root / "cache")
        try:
            images = cache.cache_root / "images"
            images.mkdir(exist_ok=True)
            for suffix, mime in (("jpg", "image/jpeg"), ("png", "image/png"), ("webp", "image/webp"),
                                 ("gif", "image/gif"), ("avif", "image/avif")):
                filename = "image-proof." + suffix
                (images / filename).write_bytes(self.image)
                with patch("mimetypes.guess_type", return_value=(None, None)):
                    self.assertEqual(cache.cached_image(filename), (self.image, mime))
            self.assertIsNone(cache.cached_image("../image-proof.jpg"))
        finally:
            cache.close()

    def test_general_extraction_order_and_no_site_graphics(self):
        variants = {
            "/news/2026/10/3/report": ('<meta property="og:image" content="/lead.jpg">', "og:image"),
            "/sports/2026/10/3/report": ('<meta property="og:image:url" content="/lead.jpg">', "og:image:url"),
            "/video/newsfeed/2026/10/3/report": ('<meta name="twitter:image" content="/lead.jpg">', "twitter:image"),
            "/jsonld": ('<script type="application/ld+json">{"@type":"NewsArticle","image":{"contentUrl":"/lead.jpg"}}</script>', "jsonld"),
            "/primary": ('<script type="application/ld+json">{"@type":"WebPage","primaryImageOfPage":{"url":"/lead.jpg"}}</script>', "jsonld"),
            "/link": ('<link rel="image_src" href="/lead.jpg">', "image_src"),
            "/hero": ('<article><figure><picture><source srcset="/lead.jpg 640w"><img src="/lead.jpg"></picture></figure></article>', "article_hero"),
            "/none": ('<header><img src="/logo.jpg"></header><aside><img src="/ad.jpg"></aside><article><p>No photograph.</p></article>', "none"),
        }
        for path, (page, expected) in variants.items():
            with self.subTest(path=path):
                result = prepare_image(page, self.base + path, "", self.root / "images")
                self.assertEqual(result["image_method"], expected)
                self.assertEqual(result["image_status"], "no_url_found" if expected == "none" else "cached")
        self.assertEqual(self.calls["/logo.jpg"], 0)
        self.assertEqual(self.calls["/ad.jpg"], 0)

    def test_redirect_resolves_relative_image_against_final_url(self):
        candidates = list(image_candidates('<meta property="og:image" content="../lead.jpg">', self.base + "/news/story"))
        self.assertEqual(candidates, [("og:image", self.base + "/lead.jpg")])

    def test_failed_image_retry_is_bounded_and_does_not_erase_article_text(self):
        self.pages["/retry"] = ('<meta property="og:image" content="/bad.jpg"><article><p>' + "Useful original article text. " * 8 + '</p></article>').encode()
        cache = ArticleCache(self.root / "unused", self.root / "index.sqlite3", self.root / "cache")
        article = {"article_id": "article-retry", "url": self.base + "/retry", "title": "Retry report"}
        try:
            first = cache.prepare(article)
            self.assertTrue(first["content_ready"])
            self.assertEqual(first["image_status"], "remote_image_failed")
            self.assertTrue(first["next_retry_at"])
            self.assertFalse(cache.prepare(article)["fetched"])
            self.assertEqual(self.calls["/retry"], 1)
            self.pages["/retry"] = self.pages["/retry"].replace(b"bad.jpg", b"lead.jpg")
            repaired = cache.prepare(article, retry_image=True)
            self.assertEqual(repaired["image_status"], "cached")
            self.assertIn("original article text", repaired["extracted_text"])
            self.assertFalse(cache.prepare(article, retry_image=True)["fetched"])
            self.assertEqual(self.calls["/retry"], 2)
        finally:
            cache.close()

    def test_full_pipeline_fetches_article_once_and_home_receives_local_image(self):
        for bad_feed in (False, True):
            with self.subTest(bad_feed=bad_feed):
                self.calls.clear()
                directory = self.root / str(bad_feed)
                page_path = "/sports/report" if bad_feed else "/news/report"
                self.pages[page_path] = ('<meta property="og:image" content="/lead.jpg"><article><p>' + "The report contains useful article detail and source context. " * 4 + '</p></article>').encode()
                article = Article.from_candidate({"url": self.base + page_path, "title": "Al Jazeera report", "source_name": "Al Jazeera", "summary": "Report summary.", "image_url": self.base + "/bad.jpg" if bad_feed else ""})
                cache = ArticleCache(directory / "unused.sqlite3", directory / "index.sqlite3", directory / "cache")
                cache_server = ArticleCacheHTTPServer(("127.0.0.1", 0), cache)
                cache_thread = threading.Thread(target=cache_server.serve_forever, daemon=True)
                cache_thread.start()
                cache_base = f"http://localhost:{cache_server.server_port}"
                news = NewsStore(directory / "news")
                signal = SignalService(directory / "signals.sqlite3", feeds=[])
                engine = DiscoveryEngine(str(directory / "discovery.sqlite3"), sources=[])
                try:
                    with patch.dict(os.environ, {"ARTICLE_CACHE_PUBLIC_URL": cache_base, "NEWS_BACKEND_ARTICLE_CACHE_URL": cache_base}):
                        news.upsert_discovered(article)
                        ok, error = _fetch_publisher(article, news)
                        self.assertTrue(ok, error)
                        prepared = cache.entry(article.article_id)
                        # Discovery reuses preparation; no second publisher HTML fetch.
                        cache.source_article = lambda _: {"article_id": article.article_id, "url": article.canonical_url, "title": article.title, "source": "Al Jazeera"}
                        engine.article_cache_url = cache_base
                        story = {"article_id": article.article_id, "story_id": "story-test", "title": article.title, "url": article.canonical_url, "summary": article.summary}
                        engine._materialize_articles([story])
                        self.assertEqual(story["prepared_article"]["image_status"], "cached")
                        # Intercept only the handoff; the actual ingestion and image HTTP transfer run.
                        def handoff(request, timeout):
                            candidate = json.loads(request.data)["items"][0]
                            with patch.object(signal, "_semantic_enrich", return_value={}):
                                outcome = signal.ingest_candidates([candidate])
                            class Response:
                                def __enter__(self): return self
                                def __exit__(self, *args): pass
                                def read(self, limit): return json.dumps({"ok": True, **outcome}).encode()
                            return Response()
                        engine.signal_service_url = self.base
                        # urllib.request module is shared; patch just engine's module reference.
                        import types
                        import discovery_service.engine as engine_module
                        real_urllib = engine_module.urllib
                        fake = types.SimpleNamespace(request=types.SimpleNamespace(Request=real_urllib.request.Request, urlopen=handoff))
                        with patch.object(engine_module, "urllib", fake), patch("signal_service.service.fetch_article_image", side_effect=AssertionError("Signal refetched publisher")):
                            pushed = engine._push([story])
                        self.assertTrue(pushed["ok"])
                        signal_card = signal.briefing()["signals"][0]
                        self.assertEqual(signal_card["image_enrichment"]["status"], "cached")
                        self.assertIn("/v1/images/", signal_card["image_cache_url"])
                        news.curate_top100()
                        home_card = news.curated_briefing()["articles"][0]
                        self.assertEqual(home_card["image_url"], prepared["image_cache_url"])
                        with urlopen(home_card["image_url"]) as response:
                            self.assertEqual(response.read(), self.image)
                        snapshot = NewsBriefingCache(cache_path=directory / "snapshot.json")
                        with patch.object(snapshot, "_request_briefing", return_value={"ok": True, **news.curated_briefing()}):
                            snapshot.sync_once()
                        self.assertEqual(snapshot.snapshot()["briefing"]["articles"][0]["image_url"], prepared["image_cache_url"])
                        stale = {"article_id": article.article_id, "image_cache_url": "http://old/wrong.jpg"}
                        self.assertEqual(_merge_cards([stale], [home_card])[0]["image_cache_url"], prepared["image_cache_url"])
                    self.assertEqual(self.calls[page_path], 1)
                    self.assertEqual(self.calls["/lead.jpg"], 1)
                    self.assertEqual(self.calls["/bad.jpg"], int(bad_feed))
                    if bad_feed:
                        self.assertEqual(prepared["image_attempts"][0]["status"], "failed")
                finally:
                    engine.close()
                    signal.close()
                    news.close()
                    cache_server.shutdown()
                    cache_server.server_close()
                    cache_thread.join()
                    cache.close()


if __name__ == "__main__":
    unittest.main()
