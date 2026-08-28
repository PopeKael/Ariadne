"""Core-owned presentation mapping for semantic plugin activity.

Plugins report contract events only.  This module is the small Core seam that
may translate those events into existing avatar states; it contains no plugin
or asset-specific presentation rules.
"""
from __future__ import annotations

from collections.abc import Callable

from avatar_events import emit_state
from plugin_activity import PluginActivityEvent, PluginActivityReporter


STAGE_TO_CORE_STATE = {
    "preparing": "working",
    "starting": "working",
    "loading": "working",
    "reading": "reading",
    "retrieving": "reading",
    "retrieval": "reading",
    "parsing": "reading",
    "extracting": "reading",
    "analysing": "reading",
    "analyzing": "reading",
    "searching": "searching_vault",
    "cross-referencing": "cross_referencing",
    "cross referencing": "cross_referencing",
    "cross reference": "cross_referencing",
    "comparing": "cross_referencing",
    "correlating": "cross_referencing",
    "synthesizing": "cross_referencing",
    "synthesis": "cross_referencing",
}


def normalize_stage(stage: str | None) -> str:
    return " ".join(str(stage or "").casefold().replace("_", " ").split())


def core_state_for_activity(event: PluginActivityEvent) -> str | None:
    """Return an existing Core presentation state for one semantic event."""
    # Capability completion is truthful activity history, not completion of the
    # user's whole request. The surrounding Core request lifecycle owns the
    # final answer presentation.
    if event.state == "completed":
        return None
    if event.state == "warning":
        return "warning"
    if event.state == "failed":
        return "error"
    if event.state == "cancelled":
        return "idle"
    if event.state in {"started", "running", "progress", "stage"}:
        stage = normalize_stage(event.stage)
        return STAGE_TO_CORE_STATE.get(stage, "working")
    return None


class CoreActivityPresenter:
    """Decorate a plugin reporter with best-effort Core presentation."""

    def __init__(
        self,
        reporter: PluginActivityReporter,
        *,
        emit_state_fn: Callable[[str], bool] = emit_state,
        completion_state: str | None = None,
    ) -> None:
        self.reporter = reporter
        self.emit_state_fn = emit_state_fn
        self.completion_state = completion_state
        self._last_state: str | None = None

    def _present(self, event: PluginActivityEvent) -> None:
        state = self.completion_state if event.state == "completed" else core_state_for_activity(event)
        if not state or state == self._last_state:
            return
        self._last_state = state
        try:
            self.emit_state_fn(state)
        except Exception:
            # Presentation is optional and must never affect plugin work.
            return

    def report(self, state: str, status_text: str, *, progress: float | int | None = None,
               stage: str | None = None) -> PluginActivityEvent:
        event = self.reporter.report(state, status_text, progress=progress, stage=stage)
        self._present(event)
        return event

    def started(self, status_text: str = "Capability started.", *, stage: str | None = None) -> PluginActivityEvent:
        return self.report("started", status_text, progress=0, stage=stage)

    def progress(self, percentage: float | int, status_text: str, *, stage: str | None = None) -> PluginActivityEvent:
        return self.report("progress", status_text, progress=percentage, stage=stage)

    def running(self, status_text: str = "Capability is running.", *, stage: str | None = None) -> PluginActivityEvent:
        return self.report("running", status_text, stage=stage)

    def stage(self, stage: str, status_text: str) -> PluginActivityEvent:
        return self.report("stage", status_text, stage=stage)

    def completed(self, status_text: str = "Capability completed.") -> PluginActivityEvent:
        return self.report("completed", status_text, progress=100)

    def warning(self, status_text: str, *, stage: str | None = None) -> PluginActivityEvent:
        return self.report("warning", status_text, stage=stage)

    def failed(self, status_text: str, *, stage: str | None = None) -> PluginActivityEvent:
        return self.report("failed", status_text, stage=stage)

    def cancelled(self, status_text: str = "Capability cancelled.") -> PluginActivityEvent:
        return self.report("cancelled", status_text)


__all__ = ["CoreActivityPresenter", "STAGE_TO_CORE_STATE", "core_state_for_activity", "normalize_stage"]
