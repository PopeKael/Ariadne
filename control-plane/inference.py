"""Provider/task routing for ordinary Ariadne intelligence work.

This module deliberately contains metadata and routing only.  The actual
provider adapters remain at their existing boundaries (for example, the
canonical Vault Ollama adapter).  Secrets are resolved from environment
variables and are represented in UI payloads only as Configured/Missing.
"""
from __future__ import annotations

import json
import os
import credential_store
from urllib.parse import unquote, urlsplit
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ariadne_config import configuration_path


TASKS = {
    "coding": {"coding", "structured_output"},
    "home_chat": {"chat"},
    "planner": {"chat", "structured_output"},
    "embedding": {"embeddings"},
    "summarization_classification": {"classification"},
}


def ipv4_ollama_endpoint(endpoint: str) -> str:
    """Keep legacy loopback model routes on the desktop IPv4 listener."""
    endpoint = endpoint.strip().rstrip("/")
    parsed = urlsplit(endpoint)
    if parsed.hostname not in {"localhost", "::1"}:
        return endpoint
    credentials = parsed.netloc.rpartition("@")[0] + "@" if "@" in parsed.netloc else ""
    authority = credentials + "127.0.0.1" + (f":{parsed.port}" if parsed.port else "")
    return parsed._replace(netloc=authority).geturl()


def normalize_ollama_model_id(model_id: str) -> str:
    """Treat an omitted Ollama tag and the explicit ``:latest`` tag alike."""
    value = str(model_id or "").strip()
    name, separator, tag = value.rpartition(":")
    return name if separator and tag.casefold() == "latest" and name else value


def ollama_model_catalogue(entries: Any) -> list[dict[str, Any]]:
    """Return unique installed model identities, canonicalizing ``:latest``."""
    models: dict[str, dict[str, Any]] = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        installed_id = str(entry.get("name") or entry.get("model") or "").strip()
        model_id = normalize_ollama_model_id(installed_id)
        if not model_id:
            continue
        details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
        hint = f"{installed_id} {details.get('family') or ''}".casefold()
        capabilities = ["embeddings"] if "embed" in hint else ["chat", "structured_output", "coding"]
        models.setdefault(model_id, {"id": model_id, "capabilities": capabilities})
    return [models[key] for key in sorted(models, key=str.casefold)]


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
    display_name: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Provider":
        provider_type = str(value.get("provider_type") or "").strip().casefold()
        endpoint = str(value.get("endpoint") or "").strip().rstrip("/")
        if provider_type == "ollama":
            endpoint = ipv4_ollama_endpoint(endpoint)
        return cls(
            str(value.get("provider_id") or "").strip(),
            provider_type,
            str(value.get("model_id") or "").strip(),
            endpoint,
            str(value.get("credential_reference") or "").strip(),
            tuple(sorted({str(item).strip() for item in value.get("capabilities", []) if str(item).strip()})),
            str(value.get("location") or "desktop").strip().casefold(),
            bool(value.get("enabled", True)), int(value.get("priority", 100)),
            str(value.get("embedding_version") or "v1"), str(value.get("display_name") or "").strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        value = {"provider_id": self.provider_id, "provider_type": self.provider_type, "model_id": self.model_id, "endpoint": self.endpoint, "credential_reference": self.credential_reference, "capabilities": list(self.capabilities), "location": self.location, "enabled": self.enabled, "priority": self.priority, "embedding_version": self.embedding_version}
        if self.display_name:
            value["display_name"] = self.display_name
        return value


def _saved(path: Path | None = None) -> dict[str, Any]:
    target = path or configuration_path()
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def default_providers() -> list[Provider]:
    ollama_url = ipv4_ollama_endpoint(os.environ.get("ARIADNE_OLLAMA_URL", "http://127.0.0.1:11434"))
    home_model = os.environ.get("ARIADNE_HOME_CHAT_MODEL", "qwen3.5:9b-q4_K_M")
    planner_model = os.environ.get("ARIADNE_PLANNER_MODEL", home_model)
    embedding_model = os.environ.get("ARIADNE_EMBEDDING_MODEL", "nomic-embed-text")
    return [
        Provider("ollama-coding", "ollama", os.environ.get("ARIADNE_CODING_MODEL", "qwen2.5-coder:14b"), ollama_url, "", ("coding", "structured_output"), "desktop", True, 40),
        Provider("ollama-desktop", "ollama", home_model, ollama_url, "", ("chat", "structured_output"), "desktop", True, 10),
        Provider("ollama-planner", "ollama", planner_model, ollama_url, "", ("chat", "structured_output"), "desktop", True, 20),
        Provider("ollama-embedding", "ollama", embedding_model, ollama_url, "", ("embeddings",), "desktop", True, 30, "ollama-embed-v1"),
        Provider("google-cloud", "google-generative-language", os.environ.get("ARIADNE_GOOGLE_MODEL") or "gemini-3.8-flash", "https://generativelanguage.googleapis.com/v1beta/openai", os.environ.get("ARIADNE_GOOGLE_CREDENTIAL_REFERENCE", "GOOGLE_API_KEY"), ("chat", "embeddings", "structured_output", "classification"), "cloud", True, 50, "google-embedding-v1"),
        Provider("groq-cloud", "groq-openai-compatible", os.environ.get("ARIADNE_GROQ_MODEL", "openai/gpt-oss-120b"), "https://api.groq.com/openai/v1", "GROQ_API_KEY", ("chat",), "cloud", True, 60),
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
        # Existing saved configurations predate the dedicated coding provider.
        if not any(item.provider_id == "ollama-coding" for item in self.providers):
            self.providers.append(next(item for item in default_providers() if item.provider_id == "ollama-coding"))
        if not any(item.provider_id == "groq-cloud" for item in self.providers):
            self.providers.append(next(item for item in default_providers() if item.provider_id == "groq-cloud"))
        if not any(item.provider_id == "google-cloud" for item in self.providers):
            self.providers.append(next(item for item in default_providers() if item.provider_id == "google-cloud"))
        # Backfill the optional Gemini profile for configurations that already
        # carried a placeholder Google entry without a usable model selection.
        gemini_default = next(item for item in default_providers() if item.provider_id == "google-cloud")
        self.providers = [
            Provider(
                item.provider_id, item.provider_type,
                gemini_default.model_id if item.provider_id == "google-cloud" and not item.model_id else item.model_id,
                gemini_default.endpoint if item.provider_id == "google-cloud" and item.endpoint in {"", "https://generativelanguage.googleapis.com"} else item.endpoint,
                item.credential_reference, item.capabilities,
                item.location, item.enabled, item.priority, item.embedding_version, item.display_name,
            )
            for item in self.providers
        ]
        saved_routes = raw.get("routes") if isinstance(raw, dict) and isinstance(raw.get("routes"), dict) else {}
        self.routes = {str(key): str(value) for key, value in saved_routes.items() if str(key) in TASKS and str(value).strip()}

    def compatible(self, task: str) -> list[Provider]:
        required = TASKS.get(task, set())
        return sorted([item for item in self.providers if item.enabled and required.issubset(set(item.capabilities)) and (task != "coding" or (item.provider_type == "ollama" and item.location == "desktop"))], key=lambda item: (item.priority, item.provider_id))

    def route(self, task: str) -> Provider | None:
        override = {"home_chat": os.environ.get("ARIADNE_HOME_PROVIDER"), "planner": os.environ.get("ARIADNE_PLANNER_PROVIDER"), "embedding": os.environ.get("ARIADNE_EMBEDDING_PROVIDER"), "summarization_classification": os.environ.get("ARIADNE_SUMMARIZATION_PROVIDER")}.get(task)
        selected = (override or self.routes.get(task) or "").strip()
        available = self.compatible(task)
        if selected:
            provider_id, separator, model_id = selected.partition("::")
            provider = next((item for item in available if item.provider_id == provider_id), None)
            return replace(provider, model_id=unquote(model_id)) if provider and separator and model_id else provider
        return next((item for item in available if self.credential_state(item) != "Missing" and item.model_id), None)

    @staticmethod
    def credential_state(provider: Provider) -> str:
        return "Configured" if not provider.credential_reference or credential_store.get_credential(provider.credential_reference) else "Missing"

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
            override = {"home_chat": os.environ.get("ARIADNE_HOME_PROVIDER"), "planner": os.environ.get("ARIADNE_PLANNER_PROVIDER"), "embedding": os.environ.get("ARIADNE_EMBEDDING_PROVIDER"), "summarization_classification": os.environ.get("ARIADNE_SUMMARIZATION_PROVIDER")}.get(task)
            route_selection = (override or self.routes.get(task) or "").strip() or None
            provider_state = next((item["state"] for item in providers if item["provider_id"] == provider.provider_id), "Unavailable") if provider else "Unavailable"
            route = {"provider_id": provider.provider_id if provider else None, "provider_type": provider.provider_type if provider else None, "model_id": provider.model_id if provider else None, "location": provider.location if provider else None, "route_selection": route_selection, "state": "Unconfigured" if provider is None else health.get(task, provider_state), "compatible_provider_ids": [item.provider_id for item in self.compatible(task)]}
            routes[task] = route
        return {"version": 1, "providers": providers, "routes": routes, "config_path": str(self.path)}


__all__ = ["InferenceRegistry", "Provider", "TASKS", "default_providers", "normalize_ollama_model_id", "ollama_model_catalogue"]
