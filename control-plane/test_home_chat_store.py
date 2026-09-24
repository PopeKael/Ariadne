import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from home_chat_store import ChatStore, isoformat, title_from_document  # noqa: E402


class HomeChatStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        self.current = datetime(2026, 8, 22, 7, 0, tzinfo=timezone.utc)
        self.store = ChatStore(self.vault, now_fn=lambda: self.current)

    def tearDown(self):
        self.temp.cleanup()

    def test_document_title_uses_metadata_and_is_not_replaced_by_later_turns(self):
        self.assertEqual(
            title_from_document({"filename": "article.md", "metadata": {"title": "Metadata title"}}),
            "Metadata title",
        )
        self.assertEqual(
            title_from_document({"filename": "quarterly_report-v2.md", "metadata": {}}),
            "quarterly report v2",
        )
        chat = self.store.create()
        turn_id, _ = self.store.begin_turn(
            chat["chat_id"], "Summarise this", "qwen3.5:9b", {},
            title="The supplied document title",
        )
        self.store.complete_turn(
            chat["chat_id"], turn_id, "The first answer.", model="qwen3.5:9b",
            used_vault=False, sources=[], retrieval={}, timing={}, identity_kernel={},
        )
        first = self.store.get(chat["chat_id"])
        self.assertEqual(first["title"], "The supplied document title")
        later_turn, _ = self.store.begin_turn(chat["chat_id"], "A later question", "qwen3.5:9b", {}, title="Later title")
        self.store.complete_turn(
            chat["chat_id"], later_turn, "The later answer.", model="qwen3.5:9b",
            used_vault=False, sources=[], retrieval={}, timing={}, identity_kernel={},
        )
        self.assertEqual(self.store.get(chat["chat_id"])["title"], "The supplied document title")

    def test_no_document_title_uses_first_exchange_after_completion(self):
        chat = self.store.create()
        turn_id, _ = self.store.begin_turn(chat["chat_id"], "What changed in the article?", "qwen3.5:9b", {})
        self.store.complete_turn(
            chat["chat_id"], turn_id, "The article explains the major policy change.", model="qwen3.5:9b",
            used_vault=False, sources=[], retrieval={}, timing={}, identity_kernel={},
        )
        self.assertIn("What changed in the article", self.store.get(chat["chat_id"])["title"])
        titled = self.store.title_from_first_exchange(chat["chat_id"])
        self.assertIn("What changed in the article", titled["title"])
        self.assertIn("The article explains", titled["title"])
        self.assertEqual(self.store.title_from_first_exchange(chat["chat_id"])["title"], titled["title"])

    def test_user_and_pending_assistant_are_written_before_generation(self):
        chat = self.store.create({"id": "ariadne", "version": "1.1.0"})
        turn_id, _ = self.store.begin_turn(chat["chat_id"], "First durable question", "qwen3.5:9b", {"version": "1.1.0"})
        path = self.vault / "00_System" / "Data" / "HomeSessions" / f"{chat['chat_id']}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual([item["role"] for item in payload["messages"]], ["user", "assistant"])
        self.assertEqual(payload["messages"][0]["content"], "First durable question")
        self.assertEqual(payload["messages"][1]["state"], "pending")
        self.assertEqual(self.store.model_history(chat["chat_id"]), [{"role": "user", "content": "First durable question"}])
        self.assertTrue(turn_id)

    def test_completed_turn_survives_new_store_instance_and_archives_once(self):
        chat = self.store.create({"id": "ariadne", "version": "1.1.0"})
        turn_id, _ = self.store.begin_turn(chat["chat_id"], "What survived?", "qwen3.5:9b", {"version": "1.1.0"})
        self.store.complete_turn(
            chat["chat_id"], turn_id, "The durable record survived.", model="qwen3.5:9b",
            used_vault=False, sources=[], retrieval={"match_count": 0}, timing={"total_duration_ms": 12},
            identity_kernel={"version": "1.1.0"},
        )
        restarted = ChatStore(self.vault, now_fn=lambda: self.current)
        self.assertEqual(restarted.model_history(chat["chat_id"])[-1]["content"], "The durable record survived.")
        record, archive_path = restarted.close_and_archive(chat["chat_id"])
        archive = self.vault / archive_path
        self.assertTrue(archive.is_file())
        text = archive.read_text(encoding="utf-8")
        self.assertIn("kind: ariadne-home-chat", text)
        self.assertIn("## Wazza", text)
        self.assertIn("## Ariadne", text)
        self.assertIn("The durable record survived.", text)
        self.assertEqual(record["archive_path"], archive_path)
        _, same_archive = restarted.close_and_archive(chat["chat_id"])
        self.assertEqual(archive_path, same_archive)
        self.assertEqual(len(list((self.vault / "Archive").rglob("*.md"))), 1)

    def test_interrupted_response_is_explicit_and_user_message_remains(self):
        chat = self.store.create()
        turn_id, _ = self.store.begin_turn(chat["chat_id"], "Interrupt me", "qwen3.5:9b", {"version": "1.1.0"})
        self.store.interrupt_turn(chat["chat_id"], turn_id, "local model stopped")
        record = self.store.get(chat["chat_id"])
        self.assertEqual(record["messages"][0]["state"], "complete")
        self.assertEqual(record["messages"][1]["state"], "interrupted")
        self.assertEqual(record["messages"][1]["response_state"], "interrupted")
        self.assertNotIn("", [record["messages"][0]["content"]])

    def test_response_feedback_is_persisted_against_assistant_message(self):
        chat = self.store.create()
        turn_id, started = self.store.begin_turn(chat["chat_id"], "Rate this answer", "qwen3.5:9b", {})
        assistant = next(item for item in started["messages"] if item["role"] == "assistant")
        self.store.complete_turn(
            chat["chat_id"], turn_id, "A durable answer.", model="qwen3.5:9b", used_vault=False,
            sources=[], retrieval={}, timing={}, identity_kernel={},
            active_source_signal_ids=["signal-article123"],
        )
        feedback = self.store.record_feedback(
            chat["chat_id"], assistant["message_id"], "needs_work",
            ["signal-article123"], "The explanation needs one more example.",
        )
        restarted = ChatStore(self.vault, now_fn=lambda: self.current)
        stored = restarted.get(chat["chat_id"])["messages"][1]["feedback"]
        self.assertEqual(feedback["message_id"], assistant["message_id"])
        self.assertEqual(stored["chat_id"], chat["chat_id"])
        self.assertEqual(stored["active_source_signal_ids"], ["signal-article123"])
        self.assertEqual(stored["rating"], "needs_work")
        self.assertEqual(stored["comment"], "The explanation needs one more example.")
        self.assertTrue(stored["timestamp"])

    def test_expiry_removes_only_json_and_preserves_archive(self):
        chat = self.store.create()
        turn_id, _ = self.store.begin_turn(chat["chat_id"], "Keep my history", "qwen3.5:9b", {"version": "1.1.0"})
        self.store.complete_turn(
            chat["chat_id"], turn_id, "History kept.", model="qwen3.5:9b", used_vault=False,
            sources=[], retrieval={}, timing={}, identity_kernel={"version": "1.1.0"},
        )
        self.current += timedelta(days=8)
        expired = self.store.cleanup_expired()
        self.assertEqual(len(expired), 1)
        self.assertIsNone(self.store.get(chat["chat_id"]))
        archive_path = expired[0]["archive_path"]
        self.assertTrue((self.vault / archive_path).is_file())

    def test_expiry_is_explicit_from_last_activity(self):
        chat = self.store.create()
        initial_expiry = parse_iso(chat["expires_at"])
        self.current += timedelta(days=1)
        self.store.begin_turn(chat["chat_id"], "Refresh retention", "qwen3.5:9b", {})
        record = self.store.get(chat["chat_id"])
        self.assertGreater(parse_iso(record["expires_at"]), initial_expiry)


    def test_recent_list_is_newest_first_and_resume_reopens_closed_chat(self):
        first = self.store.create()
        self.store.begin_turn(first["chat_id"], "First question", "qwen3.5:9b", {})
        self.current += timedelta(minutes=1)
        second = self.store.create()
        recent = self.store.list_recent()
        self.assertEqual([item["chat_id"] for item in recent], [first["chat_id"]])
        self.store.close_and_archive(first["chat_id"])
        resumed = self.store.resume(first["chat_id"])
        self.assertEqual(resumed["status"], "active")
        self.assertEqual(self.store.get(first["chat_id"])["status"], "active")

    def test_recent_list_hides_empty_records_without_touching_archived_or_inbox_copies(self):
        active = self.store.create()
        inbox = self.store.create()
        inbox_turn, _ = self.store.begin_turn(inbox["chat_id"], "Inbox question", "qwen3.5:9b", {})
        self.store.complete_turn(
            inbox["chat_id"], inbox_turn, "Inbox answer", model="qwen3.5:9b",
            used_vault=False, sources=[], retrieval={}, timing={}, identity_kernel={},
        )
        self.store.save_to_inbox(inbox["chat_id"])
        archived = self.store.create()
        self.store.close_and_archive(archived["chat_id"])

        visible = {item["chat_id"] for item in self.store.list_recent()}
        self.assertNotIn(active["chat_id"], visible)
        self.assertIn(inbox["chat_id"], visible)
        self.assertNotIn(archived["chat_id"], visible)
        inbox_record = self.store.get(inbox["chat_id"])
        archived_record = self.store.get(archived["chat_id"])
        self.assertTrue((self.vault / inbox_record["inbox_path"]).is_file())
        self.assertTrue((self.vault / archived_record["archive_path"]).is_file())

    def test_empty_cleanup_is_idempotent_and_protects_context_and_copies(self):
        removable = self.store.create()
        protected = self.store.create()
        inbox = self.store.create()
        inbox_turn, _ = self.store.begin_turn(inbox["chat_id"], "Inbox question", "qwen3.5:9b", {})
        self.store.complete_turn(
            inbox["chat_id"], inbox_turn, "Inbox answer", model="qwen3.5:9b",
            used_vault=False, sources=[], retrieval={}, timing={}, identity_kernel={},
        )
        self.store.save_to_inbox(inbox["chat_id"])
        archived = self.store.create()
        _, archive_path = self.store.close_and_archive(archived["chat_id"])

        removed = self.store.cleanup_empty_transient({protected["chat_id"]})
        self.assertEqual([item["chat_id"] for item in removed], [removable["chat_id"]])
        self.assertIsNone(self.store.get(removable["chat_id"]))
        self.assertIsNotNone(self.store.get(protected["chat_id"]))
        self.assertIsNotNone(self.store.get(inbox["chat_id"]))
        self.assertIsNotNone(self.store.get(archived["chat_id"]))
        self.assertTrue((self.vault / archive_path).is_file())
        self.assertEqual(self.store.cleanup_empty_transient({protected["chat_id"]}), [])

    def test_empty_cleanup_leaves_nonempty_record_bytes_unchanged(self):
        chat = self.store.create()
        turn_id, _ = self.store.begin_turn(chat["chat_id"], "Keep this transcript", "qwen3.5:9b", {})
        self.store.complete_turn(chat["chat_id"], turn_id, "Keep this answer", model="qwen3.5:9b", used_vault=False, sources=[], retrieval={}, timing={}, identity_kernel={})
        path = self.vault / "00_System" / "Data" / "HomeSessions" / f"{chat['chat_id']}.json"
        before = path.read_bytes()
        self.assertEqual(self.store.cleanup_empty_transient(), [])
        self.assertEqual(path.read_bytes(), before)

    def test_recent_list_reports_actual_durable_turn_count(self):
        chat = self.store.create()
        first_turn, _ = self.store.begin_turn(chat["chat_id"], "First", "qwen3.5:9b", {})
        self.store.complete_turn(chat["chat_id"], first_turn, "First answer", model="qwen3.5:9b", used_vault=False, sources=[], retrieval={}, timing={}, identity_kernel={})
        second_turn, _ = self.store.begin_turn(chat["chat_id"], "Second", "qwen3.5:9b", {})
        self.store.complete_turn(chat["chat_id"], second_turn, "Second answer", model="qwen3.5:9b", used_vault=False, sources=[], retrieval={}, timing={}, identity_kernel={})
        row = self.store.list_recent()[0]
        self.assertEqual(row["message_count"], 4)
        self.assertEqual(row["turn_count"], 2)

    def test_save_to_inbox_is_idempotent_and_export_preserves_transcript_order(self):
        chat = self.store.create()
        with self.assertRaisesRegex(ValueError, "cannot be saved"):
            self.store.save_to_inbox(chat["chat_id"])
        with self.assertRaisesRegex(ValueError, "cannot be exported"):
            self.store.export_markdown(chat["chat_id"])
        turn_id, _ = self.store.begin_turn(chat["chat_id"], "First saved question", "qwen3.5:9b", {})
        self.store.complete_turn(
            chat["chat_id"], turn_id, "First saved answer", model="qwen3.5:9b", used_vault=False,
            sources=[], retrieval={}, timing={}, identity_kernel={"version": "1.1.0"},
        )
        first_record, inbox_path = self.store.save_to_inbox(chat["chat_id"])
        second_record, same_path = self.store.save_to_inbox(chat["chat_id"])
        self.assertEqual(inbox_path, same_path)
        self.assertEqual(second_record["inbox_path"], inbox_path)
        self.assertTrue((self.vault / inbox_path).is_file())
        self.assertEqual(len(list((self.vault / "Inbox").glob("*.md"))), 1)
        markdown, filename = self.store.export_markdown(chat["chat_id"])
        self.assertTrue(filename.startswith("First saved question"))
        self.assertTrue(filename.endswith(f"_{chat['chat_id'][:8]}.md"))
        self.assertLess(markdown.index("First saved question"), markdown.index("First saved answer"))
        self.assertIn("saved_to_inbox: true", (self.vault / inbox_path).read_text(encoding="utf-8"))
        self.assertEqual(first_record["chat_id"], second_record["chat_id"])

    def test_purge_removes_only_temporary_json_and_preserves_archive_and_inbox(self):
        chat = self.store.create()
        turn_id, _ = self.store.begin_turn(chat["chat_id"], "Keep permanent copies", "qwen3.5:9b", {})
        self.store.complete_turn(
            chat["chat_id"], turn_id, "Permanent copies stay", model="qwen3.5:9b", used_vault=False,
            sources=[], retrieval={}, timing={}, identity_kernel={},
        )
        _, inbox_path = self.store.save_to_inbox(chat["chat_id"])
        _, archive_path = self.store.close_and_archive(chat["chat_id"])
        self.store.purge(chat["chat_id"])
        self.assertIsNone(self.store.get(chat["chat_id"]))
        self.assertTrue((self.vault / inbox_path).is_file())
        self.assertTrue((self.vault / archive_path).is_file())

def parse_iso(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


if __name__ == "__main__":
    unittest.main()
