"""Provider/task routing for ordinary Ariadne intelligence work.

This module deliberately contains metadata and routing only.  The actual
provider adapters remain at their existing boundaries (for example, the
canonical Vault Ollama adapter).  Secrets are resolved from environment
variables and are represented in UI payloads only as Configured/Missing.
"""
from __future__ import annotations

import json
import os
import hashlib
import re
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
DEFAULT_CONTEXT_TOKENS = 32_768


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


def resolve_ollama_model_id(model_id: str, installed_names: Any,
                            catalogue: list[dict[str, Any]] | None = None) -> str:
    """Resolve a canonical name, an installed alias, or an omitted ``:latest`` tag."""
    requested = str(model_id or "").strip()
    installed = [str(name).strip() for name in installed_names if str(name).strip()] if isinstance(installed_names, (list, tuple, set)) else []
    exact = next((name for name in installed if name.casefold() == requested.casefold()), None)
    if exact:
        return exact
    for model in catalogue or []:
        names = {str(model.get("canonical_name") or model.get("id") or "").casefold()}
        names.update(str(alias.get("name") or "").casefold() for alias in model.get("aliases", []) if isinstance(alias, dict))
        names.update(str(name).casefold() for name in model.get("installed_names", []))
        if requested.casefold() in names:
            runtime_name = str(model.get("runtime_model") or "").strip()
            if runtime_name in installed:
                return runtime_name
    normalized = normalize_ollama_model_id(requested).casefold()
    alias_match = next((name for name in installed if normalize_ollama_model_id(name).casefold() == normalized), None)
    if alias_match:
        return alias_match
    raise ValueError(f"Ollama model '{requested}' is not installed or is not a known model alias.")


def effective_context_tokens(context_max: Any, requested: int = DEFAULT_CONTEXT_TOKENS) -> int:
    """Use Ariadne's 32K default, lowering it only to a declared model limit."""
    requested = max(1_024, int(requested))
    try:
        maximum = int(context_max)
    except (TypeError, ValueError, OverflowError):
        maximum = 0
    return min(requested, maximum) if maximum > 0 else requested


def _model_role(name: str, capabilities: list[str]) -> str:
    value = name.casefold()
    features = {item.casefold() for item in capabilities}
    if "embedding" in features or "embeddings" in features or "embed" in value:
        return "Embedding"
    if re.search(r"(?:^|[-/:])(?:legacy|test)(?:[-/:]|$)", value):
        return "Legacy/Test"
    if "ariadne-qwen-code" in value or re.search(r"(?:^|[-/:])coder?(?:[-/:]|$)|coding", value):
        return "Coding"
    if "vision" in features or "vision" in value or "vl" in value:
        return "Vision"
    return "Chat"


def _model_signature(entry: dict[str, Any], show: dict[str, Any] | None) -> str:
    """Identify equal model weights/configurations while ignoring only num_ctx."""
    if not isinstance(show, dict):
        return str(entry.get("digest") or normalize_ollama_model_id(str(entry.get("name") or entry.get("model") or "")).casefold())
    modelfile = str(show.get("modelfile") or "")
    source = re.search(r"(?im)^\s*FROM\s+(.+?)\s*$", modelfile)
    if not source:
        return str(entry.get("digest") or normalize_ollama_model_id(str(entry.get("name") or entry.get("model") or "")).casefold())
    source_blob = source.group(1).strip().strip('"').casefold()
    parameters = []
    for line in str(show.get("parameters") or "").splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].casefold() != "num_ctx":
            parameters.append((parts[0].casefold(), parts[1].strip()))
    info = show.get("model_info") if isinstance(show.get("model_info"), dict) else {}
    stable_info = sorted((str(key), value) for key, value in info.items() if not str(key).endswith(".context_length"))
    material = json.dumps({
        "source": source_blob,
        "parameters": sorted(parameters),
        "template": str(show.get("template") or ""),
        "system": str(show.get("system") or ""),
        "model_info": stable_info,
    }, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def ollama_model_catalogue(entries: Any, show_metadata: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Group installed Ollama names into canonical models and purpose aliases."""
    show_metadata = show_metadata or {}
    groups: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        installed_id = str(entry.get("name") or entry.get("model") or "").strip()
        if not installed_id:
            continue
        details = entry.get("details") if isinstance(entry.get("details"), dict) else {}
        show = show_metadata.get(installed_id) if isinstance(show_metadata.get(installed_id), dict) else None
        declared = show.get("capabilities") if isinstance(show, dict) and isinstance(show.get("capabilities"), list) else []
        hint = f"{installed_id} {details.get('family') or ''}".casefold()
        features = {str(item).casefold() for item in declared}
        is_embedding = "embedding" in features or "embed" in hint
        is_chat = "completion" in features or (not declared and not is_embedding)
        is_vision = "vision" in features or "vision" in hint or "vl" in installed_id.casefold()
        role_features = []
        if is_embedding:
            role_features.append("embeddings")
        if is_chat:
            role_features.extend(["chat", "structured_output"])
        if is_vision:
            role_features.append("vision")
        if _model_role(installed_id, ["embedding"] if is_embedding else list(features)) == "Coding":
            role_features.append("coding")
        role = _model_role(installed_id, ["embedding"] if is_embedding else list(features))
        signature = _model_signature(entry, show)
        groups.setdefault(signature, []).append((entry, {
            "name": installed_id, "show": show, "role": role,
            "capabilities": role_features, "can_chat": is_chat,
            "context_max": _context_max(show),
        }))

    models: list[dict[str, Any]] = []
    for signature, members in groups.items():
        members.sort(key=lambda pair: _model_name_priority(pair[1]["name"]))
        primary_entry, primary = members[0]
        names = [meta["name"] for _, meta in members]
        untagged = {normalize_ollama_model_id(name) for name in names}
        # If installed tags differ only by their Ollama tag, expose the shared
        # base name while retaining every exact installed spelling as an alias.
        bases = {name.rpartition(":")[0] for name in names if ":" in name}
        canonical_name = sorted(bases, key=str.casefold)[0] if len(untagged) > 1 and len(bases) == 1 else normalize_ollama_model_id(primary["name"])
        roles_set = {meta["role"] for _, meta in members}
        if any(meta["can_chat"] for _, meta in members):
            roles_set.add("Chat")
        roles = sorted(roles_set, key=lambda value: (value != primary["role"], value))
        capabilities = sorted({cap for _, meta in members for cap in meta["capabilities"]})
        aliases = []
        for _, meta in members:
            name = meta["name"]
            if name.casefold() == canonical_name.casefold():
                continue
            purpose = "coding alias" if meta["role"] == "Coding" and primary["role"] != "Coding" else "Ollama tag alias"
            aliases.append({"name": name, "purpose": purpose, "role": meta["role"]})
        context_max = max((meta["context_max"] or 0 for _, meta in members), default=0) or None
        models.append({
            "id": canonical_name, "name": canonical_name, "canonical_id": signature,
            "canonical_name": canonical_name, "runtime_model": primary["name"],
            "installed_names": names, "aliases": aliases,
            "role": primary["role"], "roles": roles,
            "capabilities": capabilities, "can_chat": any(meta["can_chat"] for _, meta in members),
            "context_max": context_max,
            "effective_context_tokens": effective_context_tokens(context_max),
            "size": max((int(entry.get("size") or 0) for entry, _ in members), default=0),
            "digest": str(primary_entry.get("digest") or ""),
        })
    return sorted(models, key=lambda model: str(model["canonical_name"]).casefold())


def _context_max(show: dict[str, Any] | None) -> int | None:
    info = show.get("model_info") if isinstance(show, dict) and isinstance(show.get("model_info"), dict) else {}
    values = [int(value) for key, value in info.items() if str(key).endswith(".context_length") and str(value).isdigit()]
    return max(values) if values else None


def _model_name_priority(name: str) -> tuple[int, int, str]:
    value = name.casefold()
    return (1 if value.startswith("ariadne-") else 0, 1 if value.endswith(":latest") else 0, value)


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
        model_id = str(value.get("model_id") or "").strip()
        if provider_type == "ollama":
            endpoint = ipv4_ollama_endpoint(endpoint)
            model_id = normalize_ollama_model_id(model_id)
        return cls(
            str(value.get("provider_id") or "").strip(),
            provider_type,
            model_id,
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
    home_model = normalize_ollama_model_id(os.environ.get("ARIADNE_HOME_CHAT_MODEL", "qwen3.5:9b-q4_K_M"))
    planner_model = normalize_ollama_model_id(os.environ.get("ARIADNE_PLANNER_MODEL", home_model))
    embedding_model = normalize_ollama_model_id(os.environ.get("ARIADNE_EMBEDDING_MODEL", "nomic-embed-text"))
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


__all__ = [
    "DEFAULT_CONTEXT_TOKENS", "InferenceRegistry", "Provider", "TASKS",
    "default_providers", "effective_context_tokens", "normalize_ollama_model_id",
    "ollama_model_catalogue", "resolve_ollama_model_id",
]
