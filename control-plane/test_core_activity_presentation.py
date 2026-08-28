from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core_activity_presentation import CoreActivityPresenter, core_state_for_activity  # noqa: E402
from plugin_activity import PluginActivityEvent, PluginActivityStream  # noqa: E402


class CoreActivityPresentationTests(unittest.TestCase):
    def test_semantic_stages_map_to_existing_core_states(self):
        event = PluginActivityEvent("a", "p", "c", "stage", "Reading", stage="reading")
        self.assertEqual(core_state_for_activity(event), "reading")
        event = PluginActivityEvent("a", "p", "c", "stage", "Comparing", stage="cross-referencing")
        self.assertEqual(core_state_for_activity(event), "cross_referencing")
        event = PluginActivityEvent("a", "p", "c", "progress", "Retrieving", stage="retrieval")
        self.assertEqual(core_state_for_activity(event), "reading")

    def test_presenter_deduplicates_states_and_keeps_activity_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            stream = PluginActivityStream(Path(temporary) / "activity.jsonl")
            states: list[str] = []
            presenter = CoreActivityPresenter(
                stream.reporter(activity_id="a", plugin_id="p", capability_id="c"),
                emit_state_fn=lambda state: states.append(state) or True,
            )
            try:
                presenter.started("Preparing", stage="preparing")
                presenter.stage("reading", "Reading")
                presenter.progress(50, "Still reading", stage="reading")
                presenter.completed("Done")
            finally:
                stream.close()
        self.assertEqual(states, ["working", "reading"])

    def test_capability_completion_is_recorded_without_success_presentation(self):
        with tempfile.TemporaryDirectory() as temporary:
            stream = PluginActivityStream(Path(temporary) / "activity.jsonl")
            states: list[str] = []
            presenter = CoreActivityPresenter(
                stream.reporter(activity_id="a", plugin_id="p", capability_id="c"),
                emit_state_fn=lambda state: states.append(state) or True,
            )
            try:
                event = presenter.completed("Capability finished")
            finally:
                stream.close()
        self.assertEqual(event.state, "completed")
        self.assertEqual(states, [])

    def test_standalone_plugin_completion_can_use_existing_success_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            stream = PluginActivityStream(Path(temporary) / "activity.jsonl")
            states: list[str] = []
            presenter = CoreActivityPresenter(
                stream.reporter(activity_id="a", plugin_id="cleanup", capability_id="filesystem.organise"),
                emit_state_fn=lambda state: states.append(state) or True,
                completion_state="success",
            )
            try:
                presenter.started("Working", stage="preparing")
                event = presenter.completed("Done")
            finally:
                stream.close()
        self.assertEqual(event.state, "completed")
        self.assertEqual(states, ["working", "success"])

    def test_presentation_failure_does_not_escape_activity_reporter(self):
        with tempfile.TemporaryDirectory() as temporary:
            stream = PluginActivityStream(Path(temporary) / "activity.jsonl")
            presenter = CoreActivityPresenter(
                stream.reporter(activity_id="a", plugin_id="p", capability_id="c"),
                emit_state_fn=lambda state: (_ for _ in ()).throw(RuntimeError("host unavailable")),
            )
            try:
                event = presenter.started("Preparing", stage="preparing")
            finally:
                stream.close()
        self.assertEqual(event.stage, "preparing")


if __name__ == "__main__":
    unittest.main()
