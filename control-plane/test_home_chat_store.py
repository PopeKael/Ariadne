import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from home_chat_store import ChatStore, isoformat  # noqa: E402


class HomeChatStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.vault = Path(self.temp.name)
        self.current = datetime(2026, 8, 22, 7, 0, tzinfo=timezone.utc)
        self.store = ChatStore(self.vault, now_fn=lambda: self.current)

    def tearDown(self):
        self.temp.cleanup()

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

    def test_recent_list_preserves_empty_archived_or_inbox_records(self):
        active = self.store.create()
        inbox = self.store.create()
        self.store.save_to_inbox(inbox["chat_id"])
        archived = self.store.create()
        self.store.close_and_archive(archived["chat_id"])

        visible = {item["chat_id"] for item in self.store.list_recent()}
        self.assertNotIn(active["chat_id"], visible)
        self.assertIn(inbox["chat_id"], visible)
        self.assertIn(archived["chat_id"], visible)

    def test_save_to_inbox_is_idempotent_and_export_preserves_transcript_order(self):
        chat = self.store.create()
        _, initial_inbox_path = self.store.save_to_inbox(chat["chat_id"])
        turn_id, _ = self.store.begin_turn(chat["chat_id"], "First saved question", "qwen3.5:9b", {})
        self.store.complete_turn(
            chat["chat_id"], turn_id, "First saved answer", model="qwen3.5:9b", used_vault=False,
            sources=[], retrieval={}, timing={}, identity_kernel={"version": "1.1.0"},
        )
        first_record, inbox_path = self.store.save_to_inbox(chat["chat_id"])
        second_record, same_path = self.store.save_to_inbox(chat["chat_id"])
        self.assertEqual(initial_inbox_path, inbox_path)
        self.assertEqual(inbox_path, same_path)
        self.assertEqual(second_record["inbox_path"], inbox_path)
        self.assertTrue((self.vault / inbox_path).is_file())
        self.assertEqual(len(list((self.vault / "Inbox").glob("*.md"))), 1)
        markdown, filename = self.store.export_markdown(chat["chat_id"])
        self.assertTrue(filename.startswith("First saved question_"))
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
