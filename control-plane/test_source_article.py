from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from source_article import extract_article_text, promote_signal, resolve_article_url  # noqa: E402


class _Headers:
    def get_content_charset(self):
        return "utf-8"


class _Response:
    def __init__(self, body: bytes, url: str):
        self.body = body
        self.url = url
        self.headers = _Headers()
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.body

    def geturl(self):
        return self.url


class SourceArticleTests(unittest.TestCase):
    def test_google_news_wrapper_resolves_to_publisher(self):
        google_page = b'<c-wiz><div data-n-a-id="article-id" data-n-a-ts="123" data-n-a-sg="signature"></div></c-wiz>'
        batch = b')]}\'\n\n[["wrb.fr","Fbv4je","[\\"garturlres\\",\\"https://publisher.example/story\\",1]"]]'
        with patch(
            "source_article.urlopen",
            side_effect=[_Response(google_page, "https://news.google.com/articles/article-id"), _Response(batch, "https://news.google.com/")],
        ) as fetch:
            resolved = resolve_article_url("https://news.google.com/rss/articles/article-id")
        self.assertEqual(resolved, "https://publisher.example/story")
        self.assertEqual(fetch.call_count, 2)

    def test_extract_article_text_ignores_navigation_and_boilerplate(self):
        html = b"""
        <html><head><title>Publisher title</title></head><body>
          <nav>Home Subscribe</nav>
          <main><article><h1>Article heading</h1>
          <p>The first readable paragraph contains the source fact.</p>
          <p>The second readable paragraph contains the useful detail.</p>
          </article></main>
          <footer>Cookie settings</footer>
        </body></html>
        """
        text, title = extract_article_text(html)
        self.assertEqual(title, "Publisher title")
        self.assertIn("The first readable paragraph", text)
        self.assertIn("The second readable paragraph", text)
        self.assertNotIn("Cookie settings", text)
        self.assertNotIn("Home Subscribe", text)

    def test_promote_reuses_signal_id_note_and_preserves_metadata(self):
        html = b"<html><body><article><p>Clean source article text.</p></article></body></html>"
        signal = {
            "signal_id": "signal-1234567890abcdef",
            "title": "A Thailand article",
            "source_name": "Google News RSS",
            "url": "https://news.google.com/rss/articles/example",
            "published_at": "2026-09-08T10:00:00Z",
            "category": "Thailand Focus",
            "image_url": "https://cdn.example/image.jpg",
            "watchlist_matches": [{"topic": "AI"}],
            "content": "Fallback content",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("source_article._fetch_article", return_value=("https://publisher.example/story", "Publisher title", "Clean source article text.")) as fetch:
                first = promote_signal(root, signal, captured_at="2026-09-08T11:00:00Z")
                second = promote_signal(root, signal, captured_at="2026-09-08T12:00:00Z")
            self.assertFalse(first["updated"])
            self.assertTrue(second["updated"])
            self.assertEqual(first["path"], second["path"])
            self.assertEqual(fetch.call_count, 2)
            notes = list((root / "Inbox").glob("*.md"))
            self.assertEqual(len(notes), 1)
            note = notes[0].read_text(encoding="utf-8")
            self.assertIn('type: source-article', note)
            self.assertIn('signal_id: "signal-1234567890abcdef"', note)
            self.assertIn('resolved_url: "https://publisher.example/story"', note)
            self.assertIn('category: "Thailand Focus"', note)
            self.assertIn('  - "AI"', note)
            self.assertIn('  - "Google News RSS"', note)
            self.assertIn('  - "resolved publisher URL"', note)
            self.assertIn("Clean source article text.", note)
            self.assertIn("Think with Ariadne", note)


if __name__ == "__main__":
    unittest.main()
