"""Provider/task routing for ordinary Ariadne intelligence work.

This module deliberately contains metadata and routing only.  The actual
provider adapters remain at their existing boundaries (for example, the
canonical Vault Ollama adapter).  Secrets are resolved from environment
variables and are represented in UI payloads only as Configured/Missing.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ariadne_config import configuration_path


TASKS = {
    "home_chat": {"chat"},
    "planner": {"chat", "structured_output"},
    "embedding": {"embeddings"},
    "summarization_classification": {"classification"},
}


@dataclass(frozen=True)
class Provider:
    provider_id: str
    provider_type: str
    model_id: str
    endpoint: str
    credential_reference: str
    capabilities: tuple[str, ...]
    location: str
    enabled: bool = True
    priority: int = 100
    embedding_version: str = "v1"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Provider":
        return cls(
            str(value.get("provider_id") or "").strip(),
            str(value.get("provider_type") or "").strip().casefold(),
            str(value.get("model_id") or "").strip(),
            str(value.get("endpoint") or "").strip().rstrip("/"),
            str(value.get("credential_reference") or "").strip(),
            tuple(sorted({str(item).strip() for item in value.get("capabilities", []) if str(item).strip()})),
            str(value.get("location") or "desktop").strip().casefold(),
            bool(value.get("enabled", True)), int(value.get("priority", 100)),
            str(value.get("embedding_version") or "v1"),
        )

    def as_dict(self) -> dict[str, Any]:
        return {"provider_id": self.provider_id, "provider_type": self.provider_type, "model_id": self.model_id, "endpoint": self.endpoint, "credential_reference": self.credential_reference, "capabilities": list(self.capabilities), "location": self.location, "enabled": self.enabled, "priority": self.priority, "embedding_version": self.embedding_version}


def _saved(path: Path | None = None) -> dict[str, Any]:
    target = path or configuration_path()
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def default_providers() -> list[Provider]:
    ollama_url = os.environ.get("ARIADNE_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    home_model = os.environ.get("ARIADNE_HOME_CHAT_MODEL", "qwen3.5:9b-q4_K_M")
    planner_model = os.environ.get("ARIADNE_PLANNER_MODEL", home_model)
    embedding_model = os.environ.get("ARIADNE_EMBEDDING_MODEL", "nomic-embed-text")
    return [
        Provider("ollama-desktop", "ollama", home_model, ollama_url, "", ("chat", "structured_output"), "desktop", True, 10),
        Provider("ollama-planner", "ollama", planner_model, ollama_url, "", ("chat", "structured_output"), "desktop", True, 20),
        Provider("ollama-embedding", "ollama", embedding_model, ollama_url, "", ("embeddings",), "desktop", True, 30, "ollama-embed-v1"),
        Provider("google-cloud", "google-generative-language", os.environ.get("ARIADNE_GOOGLE_MODEL", ""), "https://generativelanguage.googleapis.com", os.environ.get("ARIADNE_GOOGLE_CREDENTIAL_REFERENCE", "GOOGLE_API_KEY"), ("chat", "embeddings", "structured_output", "classification"), "cloud", True, 50, "google-embedding-v1"),
    ]


class InferenceRegistry:
    def __init__(self, path: Path | None = None):
        self.path = path or configuration_path()
        self.providers: list[Provider] = []
        self.routes: dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        raw = _saved(self.path).get("inference", {})
        values = raw.get("providers") if isinstance(raw, dict) and isinstance(raw.get("providers"), list) else []
        self.providers = [Provider.from_dict(item) for item in values if isinstance(item, dict) and str(item.get("provider_id") or "").strip()] or default_providers()
        saved_routes = raw.get("routes") if isinstance(raw, dict) and isinstance(raw.get("routes"), dict) else {}
        self.routes = {str(key): str(value) for key, value in saved_routes.items() if str(key) in TASKS and str(value).strip()}

    def compatible(self, task: str) -> list[Provider]:
        required = TASKS.get(task, set())
        return sorted([item for item in self.providers if item.enabled and required.issubset(set(item.capabilities))], key=lambda item: (item.priority, item.provider_id))

    def route(self, task: str) -> Provider | None:
        override = {"home_chat": os.environ.get("ARIADNE_HOME_PROVIDER"), "planner": os.environ.get("ARIADNE_PLANNER_PROVIDER"), "embedding": os.environ.get("ARIADNE_EMBEDDING_PROVIDER"), "summarization_classification": os.environ.get("ARIADNE_SUMMARIZATION_PROVIDER")}.get(task)
        selected = (override or self.routes.get(task) or "").strip()
        available = self.compatible(task)
        if selected:
            return next((item for item in available if item.provider_id == selected), None)
        return next((item for item in available if self.credential_state(item) != "Missing" and item.model_id), None)

    @staticmethod
    def credential_state(provider: Provider) -> str:
        return "Configured" if not provider.credential_reference or os.environ.get(provider.credential_reference, "").strip() else "Missing"

    def snapshot(self, health: dict[str, Any] | None = None) -> dict[str, Any]:
        health = health or {}
        providers = []
        for provider in self.providers:
            item = provider.as_dict()
            item["credential_state"] = self.credential_state(provider)
            item["state"] = (
                "Missing" if item["credential_state"] == "Missing" or not provider.model_id
                else "Unavailable" if not provider.enabled
                else "Configured"
            )
            providers.append(item)
        routes: dict[str, Any] = {}
        for task in TASKS:
            provider = self.route(task)
            provider_state = next((item["state"] for item in providers if item["provider_id"] == provider.provider_id), "Unavailable") if provider else "Unavailable"
            route = {"provider_id": provider.provider_id if provider else None, "provider_type": provider.provider_type if provider else None, "model_id": provider.model_id if provider else None, "location": provider.location if provider else None, "state": "Unconfigured" if provider is None else health.get(task, provider_state), "compatible_provider_ids": [item.provider_id for item in self.compatible(task)]}
            routes[task] = route
        return {"version": 1, "providers": providers, "routes": routes, "config_path": str(self.path)}


__all__ = ["InferenceRegistry", "Provider", "TASKS", "default_providers"]
