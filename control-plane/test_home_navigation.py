from __future__ import annotations

import unittest
from pathlib import Path


class HomeNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Path(__file__).with_name("home.js").read_text(encoding="utf-8")

    def test_tldr_opens_chat_in_a_new_tab_before_async_work(self):
        source = self.source
        start = source.index("async function openSignalTldr")
        end = source.index("async function promoteSignalToVault", start)
        function = source[start:end]

        self.assertIn('window.open("about:blank", "_blank")', function)
        self.assertIn('showArticleLaunchState(newTabWindow, "Preparing TLDR · Ariadne")', function)
        self.assertIn("newTabWindow});", function)
        self.assertLess(function.index('window.open("about:blank", "_blank")'), function.index("await startSession()"))
        self.assertIn("const destination = chatUrl({chatId, prompt, signalId, tldrStartedAt, articleId, articleAction, articleStartedAt, vaultMode, toolIds});", source)
        self.assertIn("else window.location.assign(destination);", source)
        self.assertIn("<h1>Preparing article…</h1>", source)
        self.assertIn("<title>${title}</title>", source)
        self.assertIn("/ariadne-network-backdrop.png", source)
        self.assertIn("This window will continue automatically.", source)

    def test_think_action_uses_the_same_new_tab_chat_path(self):
        source = self.source
        start = source.index("async function promoteSignalToVault")
        end = source.index("function watchSignalArticle", start)
        function = source[start:end]

        self.assertIn('window.open("about:blank", "_blank")', function)
        self.assertIn('showArticleLaunchState(newTabWindow, "Preparing article · Ariadne")', function)
        self.assertIn("newTabWindow});", function)
        self.assertIn("newTabWindow.location.replace(destination)", source)


if __name__ == "__main__":
    unittest.main()
