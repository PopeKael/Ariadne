"""Manifest-driven discovery for Ariadne's optional capabilities.

 The registry is deliberately small and declarative.  Discovery validates
 metadata only; the separate execution seam is responsible for trusted,
 on-demand adapters.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


MANIFEST_VERSION = "0.1"
MANIFEST_FILENAME = "plugin.json"
PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,95}$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
HEALTH_STATES = {"healthy", "attention", "unavailable"}
PLUGIN_TYPES = {"capability", "service", "background"}
REQUIRED_FIELDS = {
    "manifest_version",
    "plugin_id",
    "name",
    "version",
    "description",
    "author",
    "plugin_type",
    "capabilities",
    "entry_point",
    "ui",
    "settings",
    "permissions",
    "dependencies",
    "hardware_requirements",
    "resource_requirements",
    "startup",
    "enabled",
    "health",
    "activity",
}
OPTIONAL_FIELDS = {"actions", "action_metadata"}


class ManifestValidationError(ValueError):
    """Raised when a plugin manifest cannot be used by Ariadne Core."""


def _require_text(value: object, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ManifestValidationError(f"{field} must be a non-empty string")
    return value.strip()


def _require_string_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ManifestValidationError(f"{field} must be a list of non-empty strings")
    return [item.strip() for item in value]


def validate_manifest(payload: object, *, source: str = "manifest") -> dict[str, Any]:
    """Validate and return a detached, normalized v0.1 manifest."""
    if not isinstance(payload, dict):
        raise ManifestValidationError(f"{source} must contain a JSON object")
    missing = sorted(REQUIRED_FIELDS - payload.keys())
    if missing:
        raise ManifestValidationError(f"{source} is missing required fields: {', '.join(missing)}")
    unknown = sorted(set(payload) - REQUIRED_FIELDS - OPTIONAL_FIELDS)
    if unknown:
        raise ManifestValidationError(f"{source} contains unsupported core fields: {', '.join(unknown)}")
    if payload["manifest_version"] != MANIFEST_VERSION:
        raise ManifestValidationError(f"manifest_version must be {MANIFEST_VERSION}")
    plugin_id = _require_text(payload["plugin_id"], "plugin_id").casefold()
    if not PLUGIN_ID_RE.fullmatch(plugin_id):
        raise ManifestValidationError("plugin_id must use lowercase letters, digits, '.', '_' or '-'")
    version = _require_text(payload["version"], "version")
    if not SEMVER_RE.fullmatch(version):
        raise ManifestValidationError("version must be a semantic version such as 0.1.0")
    plugin_type = _require_text(payload["plugin_type"], "plugin_type")
    if plugin_type not in PLUGIN_TYPES:
        raise ManifestValidationError(f"plugin_type must be one of: {', '.join(sorted(PLUGIN_TYPES))}")
    capabilities = _require_string_list(payload["capabilities"], "capabilities")
    if any(not PLUGIN_ID_RE.fullmatch(capability) for capability in capabilities):
        raise ManifestValidationError("capabilities must use lowercase identifier names")
    entry_point = _require_text(payload["entry_point"], "entry_point", allow_empty=True)
    actions = _require_string_list(payload.get("actions", []), "actions")
    if any(not PLUGIN_ID_RE.fullmatch(action) for action in actions):
        raise ManifestValidationError("actions must use lowercase identifier names")
    action_metadata = payload.get("action_metadata", {})
    if not isinstance(action_metadata, dict):
        raise ManifestValidationError("action_metadata must be an object")
    for action_name, metadata in action_metadata.items():
        if action_name not in actions:
            raise ManifestValidationError(f"action_metadata contains an undeclared action: {action_name}")
        if not isinstance(metadata, dict):
            raise ManifestValidationError(f"action_metadata.{action_name} must be an object")
        unknown_metadata = set(metadata) - {"schedulable", "mutating"}
        if unknown_metadata:
            raise ManifestValidationError(f"action_metadata.{action_name} contains unsupported fields: {', '.join(sorted(unknown_metadata))}")
        for field in ("schedulable", "mutating"):
            if field in metadata and not isinstance(metadata[field], bool):
                raise ManifestValidationError(f"action_metadata.{action_name}.{field} must be a boolean")
    for field in ("ui", "settings", "hardware_requirements", "resource_requirements", "health"):
        if not isinstance(payload[field], dict):
            raise ManifestValidationError(f"{field} must be an object")
    activity = dict(payload["activity"])
    if not isinstance(activity.get("supported"), bool):
        raise ManifestValidationError("activity.supported must be a boolean")
    if activity.get("transport", "ariadne-core") != "ariadne-core":
        raise ManifestValidationError("activity.transport must be ariadne-core")
    ui = dict(payload["ui"])
    settings = dict(payload["settings"])
    for field, value in (("ui.available", ui.get("available", False)), ("settings.available", settings.get("available", False))):
        if not isinstance(value, bool):
            raise ManifestValidationError(f"{field} must be a boolean")
    for field, value in (("ui.route", ui.get("route")), ("settings.route", settings.get("route"))):
        if value is not None and (not isinstance(value, str) or not value.startswith("/")):
            raise ManifestValidationError(f"{field} must be a local Ariadne route or null")
    health = dict(payload["health"])
    health_state = health.get("state", "healthy")
    if health_state not in HEALTH_STATES:
        raise ManifestValidationError(f"health.state must be one of: {', '.join(sorted(HEALTH_STATES))}")
    health["state"] = health_state
    health["detail"] = str(health.get("detail", "Manifest loaded."))
    startup = _require_text(payload["startup"], "startup")
    if startup not in {"on_demand", "background", "application"}:
        raise ManifestValidationError("startup must be on_demand, background or application")
    if not isinstance(payload["enabled"], bool):
        raise ManifestValidationError("enabled must be a boolean")
    return {
        "manifest_version": MANIFEST_VERSION,
        "plugin_id": plugin_id,
        "name": _require_text(payload["name"], "name"),
        "version": version,
        "description": _require_text(payload["description"], "description"),
        "author": _require_text(payload["author"], "author"),
        "plugin_type": plugin_type,
        "capabilities": capabilities,
        "actions": actions,
        "action_metadata": {str(name): dict(value) for name, value in action_metadata.items()},
        "entry_point": entry_point,
        "ui": ui,
        "settings": settings,
        "permissions": _require_string_list(payload["permissions"], "permissions"),
        "dependencies": _require_string_list(payload["dependencies"], "dependencies"),
        "hardware_requirements": dict(payload["hardware_requirements"]),
        "resource_requirements": dict(payload["resource_requirements"]),
        "startup": startup,
        "enabled": payload["enabled"],
        "health": health,
        "activity": activity,
    }


def bundled_plugin_root() -> Path:
    return Path(__file__).resolve().parent / "plugins"


def user_plugin_root() -> Path:
    configured = os.environ.get("ARIADNE_USER_PLUGIN_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "Ariadne" / "plugins"
    return Path.home() / "AppData" / "Local" / "Ariadne" / "plugins"


@dataclass(frozen=True)
class PluginRecord:
    manifest: dict[str, Any] | None
    source: str
    manifest_path: str
    status: str
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        manifest = dict(self.manifest or {})
        ui = manifest.get("ui") if isinstance(manifest.get("ui"), dict) else {}
        settings = manifest.get("settings") if isinstance(manifest.get("settings"), dict) else {}
        health = manifest.get("health") if isinstance(manifest.get("health"), dict) else {}
        capabilities = manifest.get("capabilities") if isinstance(manifest.get("capabilities"), list) else []
        return {
            **manifest,
            "source": self.source,
            "manifest_path": self.manifest_path,
            "status": self.status,
            "error": self.error,
            "health": {"state": health.get("state", "unavailable" if self.status == "invalid" else "attention"), "detail": health.get("detail", self.error or "")},
            "has_ui": bool(ui.get("available", False)) and bool(ui.get("route")),
            "has_settings": bool(settings.get("available", False)) and bool(settings.get("route")),
            "background_only": bool(manifest.get("startup") == "background" and not ui.get("available", False)),
            "capability_count": len(capabilities),
        }


class PluginRegistry:
    """Central registry for bundled and user-installed plugin metadata."""

    def __init__(self, bundled_root: Path | None = None, user_root: Path | None = None) -> None:
        self.bundled_root = Path(bundled_root) if bundled_root else bundled_plugin_root()
        self.user_root = Path(user_root) if user_root else user_plugin_root()
        self.records: list[PluginRecord] = []
        self.discovery_errors: list[str] = []

    def _manifest_paths(self, root: Path) -> Iterable[Path]:
        if not root.is_dir():
            return []
        paths: list[Path] = []
        direct = root / MANIFEST_FILENAME
        if direct.is_file():
            paths.append(direct)
        try:
            paths.extend(path / MANIFEST_FILENAME for path in root.iterdir() if path.is_dir() and (path / MANIFEST_FILENAME).is_file())
        except OSError as exc:
            self.discovery_errors.append(f"Could not inspect {root}: {exc}")
        return sorted(paths, key=lambda path: str(path).casefold())

    def _discover_root(self, root: Path, source: str) -> list[PluginRecord]:
        found: list[PluginRecord] = []
        for path in self._manifest_paths(root):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                manifest = validate_manifest(payload, source=str(path))
                found.append(PluginRecord(manifest, source, str(path), "healthy"))
            except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
                found.append(PluginRecord(None, source, str(path), "invalid", str(exc)))
        return found

    def discover(self) -> list[PluginRecord]:
        self.discovery_errors = []
        records = self._discover_root(self.bundled_root, "bundled")
        records.extend(self._discover_root(self.user_root, "user"))
        seen: dict[str, PluginRecord] = {}
        normalized: list[PluginRecord] = []
        for record in records:
            plugin_id = (record.manifest or {}).get("plugin_id")
            if plugin_id and plugin_id in seen:
                normalized.append(PluginRecord(None, record.source, record.manifest_path, "invalid", f"Duplicate plugin_id: {plugin_id}"))
                continue
            if plugin_id:
                seen[plugin_id] = record
            normalized.append(record)
        self.records = normalized
        return list(self.records)

    def list_plugins(self) -> list[dict[str, Any]]:
        if not self.records:
            self.discover()
        return [record.as_dict() for record in self.records]

    def providers_for(self, capability_id: str, *, enabled_only: bool = True) -> list[PluginRecord]:
        capability_id = str(capability_id).strip().casefold()
        if not self.records:
            self.discover()
        return [record for record in self.records if record.manifest and capability_id in record.manifest["capabilities"] and (not enabled_only or record.manifest["enabled"])]

    def payload(self) -> dict[str, Any]:
        plugins = self.list_plugins()
        capabilities: dict[str, list[str]] = {}
        for plugin in plugins:
            if plugin.get("status") != "healthy":
                continue
            for capability in plugin.get("capabilities", []):
                capabilities.setdefault(capability, []).append(plugin["plugin_id"])
        return {
            "ok": True,
            "manifest_version": MANIFEST_VERSION,
            "plugins": plugins,
            "plugin_count": len(plugins),
            "healthy_count": sum(item.get("status") == "healthy" for item in plugins),
            "capabilities": capabilities,
            "locations": {"bundled": str(self.bundled_root), "user": str(self.user_root)},
            "discovery_errors": list(self.discovery_errors),
        }


PLUGIN_REGISTRY = PluginRegistry()
