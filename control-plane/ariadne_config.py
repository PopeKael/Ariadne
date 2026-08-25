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
CANONICAL_AVATAR_STATES = (
    "idle",
    "listening",
    "thinking",
    "searching_vault",
    "reading",
    "cross_referencing",
    "loading_model",
    "working",
    "speaking",
    "waiting",
    "success",
    "warning",
    "confused",
    "recovering",
    "error",
    "offline",
)
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

AVATAR_IMAGE_SUFFIXES = (".png",)


def default_avatar_directory() -> Path:
    """Resolve the installation-relative default avatar pack directory."""
    project_root = Path(__file__).resolve().parent.parent
    return project_root / "control-plane" / "host" / "assets" / "avatar"


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


def effective_avatar(path: Path | None = None) -> tuple[dict[str, object], dict[str, str]]:
    saved = _read_saved(path).get("avatar", {})
    if not isinstance(saved, dict):
        saved = {}
    enabled = saved.get("enabled", True)
    if not isinstance(enabled, bool):
        enabled = True
    configured = _path_for(saved.get("asset_directory"))
    if configured is None:
        configured = default_avatar_directory().resolve()
        directory_source = "installation default"
    else:
        directory_source = "saved Ariadne configuration"
    state_assets = _normalized_state_assets(saved.get("state_assets"))
    return {
        "enabled": enabled,
        "asset_directory": str(configured),
        "state_assets": state_assets,
    }, {
        "enabled": "saved Ariadne configuration" if "enabled" in saved else "safe default",
        "asset_directory": directory_source,
        "state_assets": "saved Ariadne configuration" if state_assets else "avatar manifest defaults",
    }


def _path_for(value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        return None
    return candidate.resolve()


def _safe_avatar_asset(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = Path(value.strip())
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        return None
    if candidate.suffix.lower() not in AVATAR_IMAGE_SUFFIXES:
        return None
    return candidate.as_posix()


def _normalized_state_assets(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        key: asset
        for key, raw_asset in value.items()
        if isinstance(key, str)
        and key in CANONICAL_AVATAR_STATES
        and (asset := _safe_avatar_asset(raw_asset)) is not None
    }


def normalize_avatar_assets(value: object) -> dict[str, str]:
    """Return only canonical, safe relative avatar mapping entries."""
    return _normalized_state_assets(value)


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


def validate_avatar(avatar: dict[str, object]) -> dict[str, str]:
    errors: dict[str, str] = {}
    enabled = avatar.get("enabled")
    if not isinstance(enabled, bool):
        errors["enabled"] = "Choose Enabled or Disabled."
    asset_directory = _path_for(avatar.get("asset_directory"))
    if asset_directory is None:
        errors["asset_directory"] = "Enter an absolute local folder path."
    elif asset_directory.exists() and not asset_directory.is_dir():
        errors["asset_directory"] = "The avatar asset path must be a folder."
    state_assets = avatar.get("state_assets", {})
    if not isinstance(state_assets, dict):
        errors["state_assets"] = "Avatar State mappings must be an object."
    else:
        for key, value in state_assets.items():
            if key not in CANONICAL_AVATAR_STATES:
                errors[f"state_assets.{key}"] = "Unknown Avatar State mapping."
            elif _safe_avatar_asset(value) is None:
                errors[f"state_assets.{key}"] = "Mappings must use relative PNG paths inside the selected pack."
    return errors


def _atomic_write_configuration(payload: dict[str, Any], target: Path) -> dict[str, Any]:
    target.parent.mkdir(parents=True, exist_ok=True)
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


def save_configuration(
    storage: dict[str, object] | None = None,
    avatar: dict[str, object] | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    target = path or configuration_path()
    current = _read_saved(target)
    saved_storage = current.get("storage", {})
    if not isinstance(saved_storage, dict):
        saved_storage = {}
    current_storage = {
        key: saved_storage.get(key, DEFAULT_STORAGE[key])
        for key in DEFAULT_STORAGE
    }
    current_storage = {
        key: str(_path_for(value) or Path(DEFAULT_STORAGE[key]).resolve())
        for key, value in current_storage.items()
    }
    current_avatar, _ = effective_avatar(target)
    selected_storage = storage if storage is not None else current_storage
    selected_avatar = dict(avatar) if avatar is not None else dict(current_avatar)
    selected_avatar.setdefault("state_assets", current_avatar.get("state_assets", {}))
    storage_errors = validate_storage(selected_storage)
    avatar_errors = validate_avatar(selected_avatar)
    errors = {**storage_errors, **{f"avatar.{key}": value for key, value in avatar_errors.items()}}
    if errors:
        raise ValueError(json.dumps(errors, ensure_ascii=False))
    payload = dict(current)
    payload.update({
        "version": CONFIG_VERSION,
        "storage": {key: str(_path_for(selected_storage[key])) for key in DEFAULT_STORAGE},
        "avatar": {
            "enabled": bool(selected_avatar["enabled"]),
            "asset_directory": str(_path_for(selected_avatar["asset_directory"])),
            "state_assets": _normalized_state_assets(selected_avatar.get("state_assets")),
        },
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    return _atomic_write_configuration(payload, target)


def save_storage(storage: dict[str, object], path: Path | None = None) -> dict[str, Any]:
    return save_configuration(storage=storage, path=path)


def save_avatar(avatar: dict[str, object], path: Path | None = None) -> dict[str, Any]:
    return save_configuration(avatar=avatar, path=path)


def avatar_pack_status(directory: str | Path, state_assets: object = None) -> dict[str, Any]:
    """Inspect a selected avatar pack without changing configuration."""
    base = _path_for(str(directory))
    if base is None:
        return {
            "state": "invalid",
            "detail": "The avatar asset directory must be an absolute local folder path.",
            "directory": str(directory),
            "manifest": None,
            "states": [],
            "available_count": 0,
        }
    manifest_path = base / "avatar_states.json"
    empty_states = [
        {"key": key, "filename": None, "state": "missing", "detail": "Manifest mapping unavailable."}
        for key in CANONICAL_AVATAR_STATES
    ]
    if not base.is_dir():
        return {
            "state": "missing",
            "detail": "Avatar pack folder does not exist.",
            "directory": str(base),
            "manifest": None,
            "states": empty_states,
            "available_count": 0,
        }
    if not manifest_path.is_file():
        return {
            "state": "missing",
            "detail": "avatar_states.json is missing from this avatar pack.",
            "directory": str(base),
            "manifest": None,
            "states": empty_states,
            "available_count": 0,
        }
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {
            "state": "invalid",
            "detail": f"avatar_states.json could not be read: {exc}",
            "directory": str(base),
            "manifest": {"path": str(manifest_path), "version": None},
            "states": empty_states,
            "available_count": 0,
        }
    mappings = manifest.get("states") if isinstance(manifest, dict) else None
    version = manifest.get("version") if isinstance(manifest, dict) else None
    if not isinstance(mappings, dict) or version != 1:
        return {
            "state": "invalid",
            "detail": "avatar_states.json must contain version 1 and a states object.",
            "directory": str(base),
            "manifest": {"path": str(manifest_path), "version": version},
            "states": empty_states,
            "available_count": 0,
        }
    resolved_base = base.resolve()
    configured_assets = _normalized_state_assets(state_assets)
    state_rows: list[dict[str, object]] = []
    available_count = 0
    for key in CANONICAL_AVATAR_STATES:
        filename = configured_assets.get(key) or mappings.get(key)
        mapping_source = "configuration" if key in configured_assets else "manifest"
        if not isinstance(filename, str) or not filename.strip():
            state_rows.append({"key": key, "filename": None, "state": "missing", "source": mapping_source, "detail": "No Avatar State mapping."})
            continue
        candidate = Path(filename)
        if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
            state_rows.append({"key": key, "filename": filename, "state": "invalid", "source": mapping_source, "detail": "Avatar State path must stay inside the avatar pack."})
            continue
        asset_path = (base / candidate).resolve()
        try:
            asset_path.relative_to(resolved_base)
        except ValueError:
            state_rows.append({"key": key, "filename": filename, "state": "invalid", "source": mapping_source, "detail": "Avatar State path escapes the avatar pack."})
            continue
        if asset_path.is_file():
            available_count += 1
            state_rows.append({"key": key, "filename": filename, "state": "available", "source": mapping_source, "detail": "Asset file is available."})
        else:
            state_rows.append({"key": key, "filename": filename, "state": "missing", "source": mapping_source, "detail": "Mapped asset file is missing."})
    pack_state = "ready" if available_count == len(CANONICAL_AVATAR_STATES) else "partial"
    return {
        "state": pack_state,
        "detail": f"{available_count} of {len(CANONICAL_AVATAR_STATES)} Avatar States have available assets.",
        "directory": str(base),
        "manifest": {"path": str(manifest_path), "version": version},
        "states": state_rows,
        "available_count": available_count,
    }


def configuration_snapshot(path: Path | None = None) -> dict[str, Any]:
    values, sources = effective_storage(path)
    avatar, avatar_sources = effective_avatar(path)
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
        "avatar": avatar,
        "avatar_sources": avatar_sources,
    }
