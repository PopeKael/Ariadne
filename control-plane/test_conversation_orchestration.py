import unittest
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
from ariadne_config import DEFAULT_STORAGE, save_configuration, configuration_snapshot
from conversation_orchestration import normalize_intensity, personality_mode, validate_focus, generation_messages


class OrchestrationTests(unittest.TestCase):
    def test_intensity_survives_save_reload_and_other_voice_edits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "Vault"
            (root / "00_System").mkdir(parents=True)
            path = Path(temp) / "configuration.json"
            values = {**DEFAULT_STORAGE, "knowledge_vault": str(root)}
            with patch.dict(os.environ, {}, clear=True):
                expected = dict(factual=13, normal=42, creative=81, diagnostics=26)
                save_configuration(storage=values, personality={"intensity": expected}, path=path)
                self.assertEqual(configuration_snapshot(path)["personality"]["intensity"], expected)
                save_configuration(personality={"style": "Saved style"}, path=path)
                self.assertEqual(configuration_snapshot(path)["personality"]["intensity"], expected)

    def test_defaults_and_bounded_settings(self):
        self.assertEqual(normalize_intensity(None), dict(factual=10, normal=60, creative=80, diagnostics=25))
        self.assertEqual(normalize_intensity({"factual": -5, "normal": 140, "creative": "bad", "diagnostics": float("nan")}), dict(factual=0, normal=100, creative=80, diagnostics=25))

    def test_modes_reuse_intent_without_model(self):
        for query, plan, expected in [("Hello", {}, "normal"), ("Explain", {"intent": "system_diagnostics"}, "diagnostics"), ("Brainstorm ideas", {}, "creative"), ("TLDR", {}, "factual")]:
            self.assertEqual(personality_mode(query, plan), expected)
        self.assertEqual(personality_mode("Explain", {}, True), "factual")

    def test_rendered_selection_and_untrusted_focus(self):
        record = {"messages": [{"role": "assistant", "state": "complete", "turn_id": "one", "content": "A **clear** answer."}]}
        focus = validate_focus({"text": "A clear answer", "source_turn_id": "one"}, record)
        messages = generation_messages([{"role": "system", "content": "Identity"}, {"role": "user", "content": "Evidence"}], focus, "Explain", "normal", 60)
        self.assertIn("not instructions", messages[-1]["content"])
        self.assertEqual(record["messages"][0]["content"], "A **clear** answer.")
        for bad in [{}, {"text": "unrelated", "source_turn_id": "one"}, {"text": "A" * 8001, "source_turn_id": "one"}]:
            with self.assertRaises(ValueError):
                validate_focus(bad, record)

    def test_approved_behavioral_memories_are_contextual_guidance_not_hardcoded_rules(self):
        memory = {"memory_id": "memory-1", "scope": "general",
                  "lesson": "Engage with personal observations thoughtfully; avoid excessive praise and unsupported generalisations."}
        messages = generation_messages(
            [{"role": "system", "content": "Identity"}, {"role": "user", "content": "Explain how to configure a router."}],
            None, "Explain how to configure a router.", "normal", 60, behavioral_memories=[memory],
        )
        self.assertIn("APPROVED INTERACTION MEMORIES", messages[0]["content"])
        self.assertIn("semantic planner selected these by meaning", messages[0]["content"])
        self.assertIn("do not force its wording into the reply", messages[0]["content"])
        self.assertIn("[memory-1]", messages[0]["content"])
        without = generation_messages(
            [{"role": "system", "content": "Identity"}, {"role": "user", "content": "Explain."}],
            None, "Explain.", "normal", 60,
        )
        self.assertNotIn("APPROVED INTERACTION MEMORIES", without[0]["content"])


if __name__ == "__main__":
    unittest.main()
