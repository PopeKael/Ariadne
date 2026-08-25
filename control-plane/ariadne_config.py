"""Local, human-readable Ariadne configuration and precedence rules.

The control plane keeps machine-specific settings outside the repository.  The
same small boundary is used by Home, Vault status, and the configuration page;
the existing environment variables remain the highest-precedence override.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONFIG_VERSION = 1
DEFAULT_STORAGE = {
    "knowledge_vault": r"D:\Downloads\KnowledgeVault",
    "documents": r"D:\Downloads\Docs",
    "images": r"D:\Downloads\Images",
    "videos": r"D:\Downloads\Videos",
    "screenshots": r"D:\Downloads\Screenshots",
    "intake_root": r"D:\Downloads",
}
STORAGE_ENVIRONMENT = {
    "knowledge_vault": "ARIADNE_VAULT_ROOT",
    "documents": "ARIADNE_DOCUMENTS_ROOT",
    "images": "ARIADNE_IMAGES_ROOT",
    "videos": "ARIADNE_VIDEOS_ROOT",
    "screenshots": "ARIADNE_SCREENSHOTS_ROOT",
    "intake_root": "ARIADNE_INTAKE_ROOT",
}
STORAGE_LABELS = {
    "knowledge_vault": "Knowledge Vault",
    "documents": "Documents",
    "images": "Images",
    "videos": "Videos",
    "screenshots": "Screenshots",
    "intake_root": "Raw Documents / Intake Root",
}


def configuration_path() -> Path:
    explicit = os.environ.get("ARIADNE_CONFIG_PATH", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "Ariadne" / "configuration.json"
    return Path.home() / ".local" / "share" / "Ariadne" / "configuration.json"


def _read_saved(path: Path | None = None) -> dict[str, Any]:
    target = path or configuration_path()
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def effective_storage(path: Path | None = None) -> tuple[dict[str, str], dict[str, str]]:
    saved = _read_saved(path).get("storage", {})
    if not isinstance(saved, dict):
        saved = {}
    values: dict[str, str] = {}
    sources: dict[str, str] = {}
    for key, default in DEFAULT_STORAGE.items():
        env_name = STORAGE_ENVIRONMENT[key]
        override = os.environ.get(env_name, "").strip()
        if override:
            values[key] = str(Path(override).expanduser().resolve())
            sources[key] = "environment override"
        else:
            configured = saved.get(key)
            if isinstance(configured, str) and configured.strip():
                values[key] = str(Path(configured).expanduser().resolve())
                sources[key] = "saved Ariadne configuration"
            else:
                values[key] = str(Path(default).expanduser().resolve())
                sources[key] = "installation default"
    return values, sources


def _path_for(value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        return None
    return candidate.resolve()


def validate_storage(storage: dict[str, object]) -> dict[str, str]:
    errors: dict[str, str] = {}
    for key in DEFAULT_STORAGE:
        candidate = _path_for(storage.get(key))
        if candidate is None:
            errors[key] = "Enter an absolute folder path."
            continue
        if key == "knowledge_vault":
            if not candidate.is_dir():
                errors[key] = "The Knowledge Vault must be an existing folder."
            elif not (candidate / "00_System").is_dir():
                errors[key] = "The Knowledge Vault must contain a 00_System folder."
    return errors


def save_storage(storage: dict[str, object], path: Path | None = None) -> dict[str, Any]:
    errors = validate_storage(storage)
    if errors:
        raise ValueError(json.dumps(errors, ensure_ascii=False))
    target = path or configuration_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": CONFIG_VERSION,
        "storage": {key: str(_path_for(storage[key])) for key in DEFAULT_STORAGE},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    descriptor, temporary_name = tempfile.mkstemp(prefix="ariadne-config-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
    return payload


def configuration_snapshot(path: Path | None = None) -> dict[str, Any]:
    values, sources = effective_storage(path)
    return {
        "path": str(path or configuration_path()),
        "version": CONFIG_VERSION,
        "precedence": [
            "explicit environment override",
            "saved Ariadne configuration",
            "installation default",
        ],
        "storage": values,
        "sources": sources,
    }
