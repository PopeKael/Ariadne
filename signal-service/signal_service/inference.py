"""Provider-independent inference routing for the NAS Signal Service.

The registry stores only provider metadata and credential references.  Runtime
secrets are resolved from the environment of the service process and are never
returned by :func:`describe`.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


TASK_CAPABILITIES = {
    "home_chat": {"chat"},
    "planner": {"chat", "structured_output"},
    "embedding": {"embeddings"},
    "summarization_classification": {"classification"},
}


class ProviderUnavailable(RuntimeError):
    """Raised when a selected provider cannot currently perform a task."""


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
            provider_id=str(value.get("provider_id") or "").strip(),
            provider_type=str(value.get("provider_type") or "").strip().casefold(),
            model_id=str(value.get("model_id") or "").strip(),
            endpoint=str(value.get("endpoint") or "").strip().rstrip("/"),
            credential_reference=str(value.get("credential_reference") or "").strip(),
            capabilities=tuple(sorted({str(item).strip() for item in value.get("capabilities", []) if str(item).strip()})),
            location=str(value.get("location") or "nas").strip().casefold(),
            enabled=bool(value.get("enabled", True)),
            priority=int(value.get("priority", 100)),
            embedding_version=str(value.get("embedding_version") or "v1").strip(),
        )

    def as_dict(self, *, include_runtime: bool = False) -> dict[str, Any]:
        result = {
            "provider_id": self.provider_id,
            "provider_type": self.provider_type,
            "model_id": self.model_id,
            "endpoint": self.endpoint,
            "credential_reference": self.credential_reference,
            "capabilities": list(self.capabilities),
            "location": self.location,
            "enabled": self.enabled,
            "priority": self.priority,
            "embedding_version": self.embedding_version,
        }
        if include_runtime:
            result["credential_state"] = credential_state(self)
        return result


def credential_state(provider: Provider) -> str:
    if not provider.credential_reference:
        return "Configured"
    return "Configured" if os.environ.get(provider.credential_reference, "").strip() else "Missing"


def _default_providers() -> list[Provider]:
    google_model = os.environ.get("SIGNAL_SERVICE_GOOGLE_EMBEDDING_MODEL", "gemini-embedding-001")
    google_key = os.environ.get("SIGNAL_SERVICE_GOOGLE_CREDENTIAL_REFERENCE", "GOOGLE_API_KEY")
    ollama_url = os.environ.get("SIGNAL_SERVICE_OLLAMA_URL", "http://localhost:11434")
    ollama_model = os.environ.get("SIGNAL_SERVICE_OLLAMA_EMBEDDING_MODEL", "nomic-embed-text")
    return [
        Provider(
            "google-public", "google-generative-language", google_model,
            "https://generativelanguage.googleapis.com", google_key,
            ("embeddings", "classification", "structured_output"), "cloud", True, 10,
            "google-embedding-v1",
        ),
        Provider(
            "ollama-optional", "ollama", ollama_model, ollama_url, "",
            ("embeddings", "chat", "classification", "structured_output"), "desktop", True, 50,
            "ollama-embed-v1",
        ),
    ]


class InferenceRegistry:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.environ.get("SIGNAL_SERVICE_INFERENCE_CONFIG", "")) if (path or os.environ.get("SIGNAL_SERVICE_INFERENCE_CONFIG")) else None
        self.providers: list[Provider] = []
        self.routes: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        raw: dict[str, Any] = {}
        if self.path and self.path.is_file():
            try:
                candidate = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(candidate, dict):
                    raw = candidate
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                raw = {}
        values = raw.get("providers") if isinstance(raw.get("providers"), list) else []
        self.providers = [Provider.from_dict(item) for item in values if isinstance(item, dict) and str(item.get("provider_id") or "").strip()]
        if not self.providers:
            self.providers = _default_providers()
        configured_routes = raw.get("routes") if isinstance(raw.get("routes"), dict) else {}
        self.routes = {str(key): str(value) for key, value in configured_routes.items() if str(key) in TASK_CAPABILITIES and str(value).strip()}

    def save(self, providers: Iterable[dict[str, Any]] | None = None, routes: dict[str, str] | None = None) -> dict[str, Any]:
        if providers is not None:
            self.providers = [Provider.from_dict(item) for item in providers if isinstance(item, dict) and str(item.get("provider_id") or "").strip()]
        if routes is not None:
            self.routes = {str(key): str(value) for key, value in routes.items() if str(key) in TASK_CAPABILITIES and str(value).strip()}
        if self.path is None:
            return self.snapshot()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "providers": [provider.as_dict() for provider in self.providers], "routes": dict(self.routes)}
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return self.snapshot()

    def compatible(self, task: str) -> list[Provider]:
        required = TASK_CAPABILITIES.get(task, set())
        return sorted(
            [provider for provider in self.providers if provider.enabled and required.issubset(set(provider.capabilities))],
            key=lambda provider: (provider.priority, provider.provider_id),
        )

    def route(self, task: str) -> Provider | None:
        selected = self.routes.get(task)
        compatible = self.compatible(task)
        if selected:
            return next((provider for provider in compatible if provider.provider_id == selected), None)
        if not compatible:
            return None
        # An explicit route remains authoritative so its unavailable state is
        # visible. Without one, prefer a provider that is currently usable.
        configured = [provider for provider in compatible if self.state(provider) == "Configured"]
        return configured[0] if configured else compatible[0]

    def state(self, provider: Provider) -> str:
        if not provider.enabled:
            return "Unavailable"
        if credential_state(provider) == "Missing":
            return "Missing"
        if provider.provider_type == "ollama":
            try:
                request = urllib.request.Request(provider.endpoint + "/api/tags", headers={"Accept": "application/json"})
                with urllib.request.urlopen(request, timeout=1.5):
                    return "Configured"
            except (OSError, urllib.error.URLError, TimeoutError):
                return "Unavailable"
        return "Configured"

    def snapshot(self) -> dict[str, Any]:
        routes: dict[str, Any] = {}
        for task in TASK_CAPABILITIES:
            provider = self.route(task)
            routes[task] = {
                "provider_id": provider.provider_id if provider else None,
                "model_id": provider.model_id if provider else None,
                "location": provider.location if provider else None,
                "state": self.state(provider) if provider else "Unavailable",
                "compatible_provider_ids": [item.provider_id for item in self.compatible(task)],
            }
        return {
            "version": 1,
            "providers": [dict(provider.as_dict(include_runtime=True), state=self.state(provider)) for provider in self.providers],
            "routes": routes,
            "config_path": str(self.path) if self.path else None,
        }

    def embed_many(self, texts: list[str], provider: Provider | None = None) -> list[list[float]]:
        selected = provider or self.route("embedding")
        if selected is None:
            raise ProviderUnavailable("No compatible embedding provider is configured.")
        if self.state(selected) in {"Missing", "Unavailable"}:
            raise ProviderUnavailable(f"Embedding provider {selected.provider_id} is {self.state(selected).casefold()}.")
        if selected.provider_type == "ollama":
            body = {"model": selected.model_id, "input": texts, "truncate": True}
            request = urllib.request.Request(selected.endpoint + "/api/embed", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    payload = json.loads(response.read(5_000_000).decode("utf-8"))
            except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
                raise ProviderUnavailable(f"Ollama embedding failed: {exc}") from exc
            vectors = payload.get("embeddings") if isinstance(payload, dict) else None
            if not isinstance(vectors, list) or len(vectors) != len(texts):
                raise ProviderUnavailable("Ollama returned an incompatible embedding response.")
            return [[float(value) for value in vector] for vector in vectors]
        if selected.provider_type in {"google", "google-generative-language", "gemini"}:
            key = os.environ.get(selected.credential_reference, "").strip()
            if not key:
                raise ProviderUnavailable(f"Credential {selected.credential_reference} is missing.")
            requests = [{"model": f"models/{selected.model_id}", "content": {"parts": [{"text": text}]}} for text in texts]
            body = {"requests": requests}
            url = selected.endpoint + "/v1beta/models/" + urllib.parse.quote(selected.model_id, safe="") + ":batchEmbedContents?key=" + urllib.parse.quote(key, safe="")
            request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    payload = json.loads(response.read(5_000_000).decode("utf-8"))
            except (OSError, urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
                raise ProviderUnavailable(f"Google embedding failed: {exc}") from exc
            embeddings = payload.get("embeddings") if isinstance(payload, dict) else None
            vectors = [item.get("values") for item in embeddings] if isinstance(embeddings, list) else []
            if len(vectors) != len(texts) or any(not isinstance(vector, list) for vector in vectors):
                raise ProviderUnavailable("Google returned an incompatible embedding response.")
            return [[float(value) for value in vector] for vector in vectors]
        raise ProviderUnavailable(f"Provider type {selected.provider_type} has no embedding adapter.")


__all__ = ["InferenceRegistry", "Provider", "ProviderUnavailable", "TASK_CAPABILITIES", "credential_state"]
