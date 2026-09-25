import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from discovery_service.article_cache import ArticleCache


class _Response:
    class Headers:
        def get_content_charset(self):
            return "utf-8"

    headers = Headers()

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.body


class ArticleCacheTests(unittest.TestCase):
    def test_extractor_prefers_article_body_and_drops_template_blocks(self):
        html = b"""<html><body><div class='sidebar'><p>Unrelated sidebar content with enough words to pass length checks.</p></div><div class='article-body'><h1>Useful story</h1><p>The article paragraph contains the real report and enough detail for the extractor.</p><div class='related-articles'><p>Unrelated recommended story content should be excluded.</p></div><p>The next paragraph stays inside the article and should remain in Markdown.</p></div><footer><p>Footer text is never part of the useful story body.</p></footer></body></html>"""
        from discovery_service.article_cache import _extract_markdown

        markdown = _extract_markdown(html, "https://publisher.test/story")
        self.assertIn("# Useful story", markdown)
        self.assertIn("The article paragraph contains the real report", markdown)
        self.assertIn("The next paragraph stays inside the article", markdown)
        self.assertNotIn("Unrelated sidebar", markdown)
        self.assertNotIn("recommended story", markdown)
        self.assertNotIn("Footer text", markdown)

    def test_populate_writes_markdown_and_retrieve_does_not_open_network(self):
        html = b"""<html><head><title>Noise</title></head><body><nav>Menu</nav><main><h1>Useful article</h1><p>This is the first useful paragraph with enough detail for extraction.</p><p>This is the second useful paragraph and it is also deliberately substantial.</p></main><footer>Noise</footer></body></html>"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "discovery.sqlite3"
            connection = sqlite3.connect(source)
            connection.executescript("""
                CREATE TABLE articles(article_id TEXT PRIMARY KEY, canonical_url TEXT, title TEXT, summary TEXT, published_at TEXT, image_url TEXT);
                CREATE TABLE article_sources(article_id TEXT, source_name TEXT, last_seen_at TEXT);
            """)
            connection.execute("INSERT INTO articles VALUES(?,?,?,?,?,?)", ("article-test", "https://publisher.test/story", "Useful article", "Summary", "2026-09-25T00:00:00+00:00", ""))
            connection.execute("INSERT INTO article_sources VALUES(?,?,?)", ("article-test", "Publisher", "2026-09-25T00:00:00+00:00"))
            connection.commit()
            connection.close()
            cache = ArticleCache(source, root / "index.sqlite3", root / "cache")
            try:
                with patch("discovery_service.article_cache.urllib.request.urlopen", return_value=_Response(html)) as fetch:
                    result = cache.populate("article-test")
                self.assertTrue(result["ok"])
                self.assertTrue(result["content_ready"])
                self.assertTrue(Path(result["markdown_path"]).is_file())
                self.assertIn("# Useful article", Path(result["markdown_path"]).read_text(encoding="utf-8"))
                fetch.assert_called_once()
                with patch("discovery_service.article_cache.urllib.request.urlopen", side_effect=AssertionError("retrieval attempted network")):
                    entry, markdown = cache.retrieve("article-test")
                self.assertEqual(entry["article_id"], "article-test")
                self.assertIn("first useful paragraph", markdown)
            finally:
                cache.close()

    def test_repeated_populate_is_a_cache_hit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "discovery.sqlite3"
            connection = sqlite3.connect(source)
            connection.executescript("CREATE TABLE articles(article_id TEXT PRIMARY KEY, canonical_url TEXT, title TEXT, summary TEXT, published_at TEXT, image_url TEXT); CREATE TABLE article_sources(article_id TEXT, source_name TEXT, last_seen_at TEXT);")
            connection.execute("INSERT INTO articles VALUES(?,?,?,?,?,?)", ("article-test", "https://publisher.test/story", "Useful article", "Summary", "2026-09-25T00:00:00+00:00", ""))
            connection.execute("INSERT INTO article_sources VALUES(?,?,?)", ("article-test", "Publisher", "2026-09-25T00:00:00+00:00"))
            connection.commit()
            connection.close()
            cache = ArticleCache(source, root / "index.sqlite3", root / "cache")
            try:
                with patch("discovery_service.article_cache.urllib.request.urlopen", return_value=_Response(b"<main><p>" + b"A" * 100 + b"</p></main>")) as fetch:
                    cache.populate("article-test")
                    second = cache.populate("article-test")
                self.assertTrue(second["cached"])
                self.assertFalse(second["fetched"])
                fetch.assert_called_once()
            finally:
                cache.close()


if __name__ == "__main__":
    unittest.main()
