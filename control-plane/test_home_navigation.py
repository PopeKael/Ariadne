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

    def test_opening_chat_in_new_tab_keeps_home_session_alive(self):
        start = self.source.index("async function openFreshChat")
        end = self.source.index("function el(", start)
        function = self.source[start:end]

        self.assertIn("if (!newTabWindow) closeSession();", function)
        self.assertLess(function.index("if (!newTabWindow) closeSession();"), function.index("newTabWindow.location.replace(destination)"))

    def test_home_and_canonical_chat_default_to_all_sources_and_send_current_selection(self):
        for page in ("home.html", "chat.html"):
            markup = Path(__file__).with_name(page).read_text(encoding="utf-8")
            self.assertIn('<option value="all">All Sources</option>', markup)
            self.assertIn('<option value="local">Local only</option>', markup)
        shell = Path(__file__).with_name("page-shell.js").read_text(encoding="utf-8")
        self.assertIn('["Chat","/chat",["/chat"]]', shell)
        self.assertIn('vault_mode: document.querySelector("#knowledge-mode").value || "all"', self.source)
        self.assertIn('tool_ids: Array.from(state.selectedToolIds)', self.source)
        self.assertIn('mode === "auto" ? "all" : mode', self.source)
        self.assertIn('button.textContent = "Local only"', self.source)
        self.assertIn('sourceMode.value = sourceMode.value === "local" ? "all" : "local"', self.source)
        self.assertIn('if (sourceMode.value === "local") state.selectedToolIds.delete("external-research")', self.source)
        self.assertIn('title="All Sources is the default. Turn on Local only to restrict this chat to Vault and attachments."', Path(__file__).with_name("chat.html").read_text(encoding="utf-8"))
        self.assertIn('await openFreshChat({signalId, prompt: TLDR_PROMPT', self.source)
        self.assertIn('await openFreshChat({prompt: message})', self.source)
        self.assertIn('window.location.assign(chatUrl({chatId}));', self.source)
        start = self.source.index("async function startNewChat")
        end = self.source.index("async function saveCurrentChat", start)
        fresh_chat = self.source[start:end]
        self.assertIn('if (sourceMode) sourceMode.value = "all";', fresh_chat)
        self.assertIn("state.selectedToolIds.clear();", fresh_chat)
        self.assertIn("syncChatLaunchStateToUrl();", fresh_chat)

    def test_chat_presentation_uses_one_activity_snapshot_and_unicode_citations(self):
        source = self.source
        self.assertIn('pushChatActivity(event.activity)', source)
        self.assertIn('applyChatActivity(payload.activity, {record: true})', source)
        self.assertIn('#chat-log .message.assistant.activity-placeholder .message-body', source)
        self.assertIn('pendingBody.textContent = label', source)
        self.assertNotIn('Response pending…', source)
        self.assertIn('function superscriptNumber(value)', source)
        self.assertIn('return superscriptNumber(citationNumbers.get(key) || number)', source)
        self.assertIn('text.replace(/\\[(\\d+)\\](?!\\()/g', source)
        self.assertIn('metadata.sources.entries()', source)
        self.assertNotIn('metadata.sources.slice(0, 8)', source)
        select_start = source.index("async function selectRecentChat")
        select_end = source.index("async function startNewChat", select_start)
        self.assertIn("await refreshChatActivity();", source[select_start:select_end])
        session_start = source.index("async function startSession")
        session_end = source.index("async function ask", session_start)
        self.assertIn("await refreshChatActivity();", source[session_start:session_end])
        start = source.index("async function readAnswer")
        end = source.index("function finiteMetric", start)
        self.assertIn("messageBody.textContent", source[start:end])

        stylesheet = Path(__file__).with_name("chat.css").read_text(encoding="utf-8")
        self.assertIn("body.chat-page { height:auto; min-height:100%; overflow-x:clip; overflow-y:visible; }", stylesheet)
        self.assertIn(".chat-log {", stylesheet)
        self.assertIn("overflow:visible; scroll-behavior:smooth", stylesheet)
        self.assertIn(".recent-chat-list { min-height:0; flex:1; overflow:auto", stylesheet)


if __name__ == "__main__":
    unittest.main()
