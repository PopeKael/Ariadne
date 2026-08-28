"""Trusted adapter for Ariadne's deterministic filing assistant.

The adapter validates and migrates configuration, then invokes the one
existing PowerShell organiser. It never reads file contents or starts Vault
ingestion.
"""
from __future__ import annotations

import json
import re
from pathlib import Path


PLUGIN_ID = "cleanup"
PLUGIN_CAPABILITY = "filesystem.organise"
COLLISION_POLICIES = {"skip"}
EXTENSION_RE = re.compile(r"^\.[A-Za-z0-9][A-Za-z0-9._+-]*$")
DEFAULT_IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg", ".heic", ".avif"]
DEFAULT_VIDEO_EXTENSIONS = [".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".wmv", ".flv", ".mpeg", ".mpg"]


def default_configuration(storage: dict[str, object]) -> dict[str, object]:
    source = Path(str(storage["intake_root"])).expanduser()
    return {
        "enabled": True,
        "sources": [{"path": str(source), "enabled": True}],
        "filing_classes": [
            {"name": "Markdown", "extensions": [".md"], "destination": str(Path(str(storage["knowledge_vault"])) / "Inbox"), "enabled": True},
            {"name": "Email", "extensions": [".eml"], "destination": str(storage["documents"]), "enabled": True},
            # Patterns retain the legacy Screenshot filename rule. When a
            # class has patterns, the patterns are its deterministic matcher;
            # its extensions document the file family without routing every
            # image away from the screenshot destination.
            {"name": "Screenshot", "extensions": DEFAULT_IMAGE_EXTENSIONS.copy(), "patterns": ["screenshot"], "destination": str(source / "screenshots"), "enabled": True},
            {"name": "Image", "extensions": DEFAULT_IMAGE_EXTENSIONS.copy(), "destination": str(storage["images"]), "enabled": True},
            {"name": "Video", "extensions": DEFAULT_VIDEO_EXTENSIONS.copy(), "destination": str(storage["videos"]), "enabled": True},
        ],
        "recurse": False,
        "exclusions": [],
        "confirmation_required": True,
        "collision_policy": "skip",
        "unmatched_policy": "leave_in_place",
    }


def _absolute_folder(value: object, field: str, errors: dict[str, str], *, must_exist: bool = False) -> str | None:
    if not isinstance(value, str) or not value.strip():
        errors[field] = "Enter an absolute folder path."
        return None
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        errors[field] = "The path must be absolute."
        return None
    resolved = candidate.resolve()
    if must_exist and not resolved.is_dir():
        errors[field] = "The source folder must be an existing folder."
    return str(resolved)


def _migrate_shape(value: dict[str, object], storage: dict[str, object]) -> dict[str, object]:
    """Convert Cleanup v1 fields without changing their meaning."""
    migrated = dict(value)
    if "sources" not in migrated and "source_folder" in migrated:
        migrated["sources"] = [{"path": migrated.get("source_folder"), "enabled": True}]
    if "filing_classes" not in migrated and "rules" in migrated:
        classes = []
        for rule in migrated.get("rules", []) if isinstance(migrated.get("rules"), list) else []:
            if isinstance(rule, dict):
                classes.append({
                    "name": rule.get("category"), "extensions": rule.get("extensions", []),
                    "patterns": rule.get("patterns", []), "destination": rule.get("destination"), "enabled": True,
                })
        migrated["filing_classes"] = classes
    defaults = default_configuration(storage)
    for key, default in defaults.items():
        migrated.setdefault(key, default)
    return migrated


def normalize_configuration(value: object, storage: dict[str, object]) -> dict[str, object]:
    """Validate, migrate, and normalize user filing-assistant settings."""
    if value is None:
        candidate = default_configuration(storage)
    elif isinstance(value, dict):
        candidate = _migrate_shape(value, storage)
    else:
        raise ValueError("Cleanup configuration must be an object.")
    errors: dict[str, str] = {}
    if not isinstance(candidate.get("enabled"), bool):
        errors["enabled"] = "Enabled must be true or false."
    if not isinstance(candidate.get("recurse"), bool):
        errors["recurse"] = "Include subdirectories must be true or false."
    if not isinstance(candidate.get("confirmation_required"), bool):
        errors["confirmation_required"] = "Confirmation required must be true or false."
    if candidate.get("collision_policy") not in COLLISION_POLICIES:
        errors["collision_policy"] = "The only supported collision policy is skip."
    if candidate.get("unmatched_policy", "leave_in_place") != "leave_in_place":
        errors["unmatched_policy"] = "Unmatched files must remain in their original location."

    raw_sources = candidate.get("sources")
    sources: list[dict[str, object]] = []
    if not isinstance(raw_sources, list) or not raw_sources:
        errors["sources"] = "Add at least one filing source."
        raw_sources = []
    for index, raw_source in enumerate(raw_sources):
        prefix = f"sources.{index}"
        if not isinstance(raw_source, dict):
            errors[prefix] = "Each filing source must be an object."
            continue
        enabled = raw_source.get("enabled")
        if not isinstance(enabled, bool):
            errors[f"{prefix}.enabled"] = "Source enabled must be true or false."
            enabled = False
        path = _absolute_folder(raw_source.get("path"), f"{prefix}.path", errors, must_exist=enabled)
        sources.append({"path": path or "", "enabled": enabled})

    raw_classes = candidate.get("filing_classes")
    classes: list[dict[str, object]] = []
    if not isinstance(raw_classes, list) or not raw_classes:
        errors["filing_classes"] = "Add at least one filing class."
        raw_classes = []
    extension_destinations: dict[str, str] = {}
    for index, raw_class in enumerate(raw_classes):
        prefix = f"filing_classes.{index}"
        if not isinstance(raw_class, dict):
            errors[prefix] = "Each filing class must be an object."
            continue
        name = raw_class.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            errors[f"{prefix}.name"] = "Enter a filing class name of 1–80 characters."
            name = f"Class {index + 1}"
        enabled = raw_class.get("enabled")
        if not isinstance(enabled, bool):
            errors[f"{prefix}.enabled"] = "Filing class enabled must be true or false."
            enabled = False
        extensions = raw_class.get("extensions")
        raw_patterns = raw_class.get("patterns", [])
        pattern_declared = isinstance(raw_patterns, list) and bool(raw_patterns)
        if not isinstance(extensions, list) or (not extensions and not pattern_declared) or any(not isinstance(item, str) or not EXTENSION_RE.fullmatch(item.strip()) for item in extensions):
            errors[f"{prefix}.extensions"] = "Add one or more extensions such as .md or .3mf."
            extensions = []
        normalized_extensions = []
        for item in extensions:
            extension = str(item).strip().casefold()
            if extension not in normalized_extensions:
                normalized_extensions.append(extension)
        patterns = raw_patterns
        if not isinstance(patterns, list) or any(not isinstance(item, str) or not item.strip() or "/" in item or "\\" in item for item in patterns):
            errors[f"{prefix}.patterns"] = "Filename patterns must be text without path separators."
            patterns = []
        destination = _absolute_folder(raw_class.get("destination"), f"{prefix}.destination", errors)
        destination = destination or ""
        # Classes with patterns are pattern-only, so the legacy Screenshot
        # class can share image extensions without changing its routing.
        if enabled and not patterns:
            for extension in normalized_extensions:
                previous = extension_destinations.get(extension)
                if previous and previous.casefold() != destination.casefold():
                    errors[f"{prefix}.extensions"] = f"Extension {extension} conflicts with another enabled filing class."
                else:
                    extension_destinations[extension] = destination
        classes.append({
            "name": str(name).strip(), "extensions": normalized_extensions,
            "patterns": [str(item).strip().casefold() for item in patterns],
            "destination": destination, "enabled": enabled,
        })
    exclusions = candidate.get("exclusions", [])
    if not isinstance(exclusions, list) or any(not isinstance(item, str) or not item.strip() for item in exclusions):
        errors["exclusions"] = "Exclusions must be a list of relative paths."
        exclusions = []
    else:
        for item in exclusions:
            if Path(item).is_absolute() or ".." in Path(item).parts:
                errors["exclusions"] = "Exclusions must stay inside each source folder."
                break
    if errors:
        raise ValueError(str(errors))
    return {
        "enabled": candidate["enabled"], "sources": sources, "filing_classes": classes,
        "recurse": candidate["recurse"], "exclusions": [str(item).strip() for item in exclusions],
        "confirmation_required": candidate["confirmation_required"], "collision_policy": "skip",
        "unmatched_policy": "leave_in_place",
    }


def effective_configuration(saved_plugins: object, storage: dict[str, object]) -> tuple[dict[str, object], str | None]:
    saved = saved_plugins.get(PLUGIN_ID) if isinstance(saved_plugins, dict) else None
    try:
        return normalize_configuration(saved, storage), None
    except ValueError as exc:
        return default_configuration(storage), str(exc)


def build_command(action: str, config: dict[str, object], context: dict[str, object]) -> list[str]:
    if action not in {"preview", "apply"}:
        raise ValueError(f"Cleanup does not support action: {action}")
    shell = str(context.get("powershell_path") or "")
    script_path = Path(str(context["organiser_path"])).resolve()
    config_path = Path(str(context["config_path"])).resolve()
    result_path = Path(str(context["result_path"])).resolve() if context.get("result_path") else None
    if not shell or not script_path.is_file():
        raise RuntimeError("The existing Downloads organiser or PowerShell is unavailable.")
    enabled_sources = [Path(str(item["path"])).resolve() for item in config["sources"] if item.get("enabled")]
    if not enabled_sources:
        raise ValueError("Enable at least one existing Cleanup source folder before running.")
    missing = [str(path) for path in enabled_sources if not path.is_dir()]
    if missing:
        raise ValueError(f"Cleanup source folder does not exist: {', '.join(missing)}")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    command = [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script_path), "-ConfigPath", str(config_path)]
    if result_path:
        result_path.parent.mkdir(parents=True, exist_ok=True)
        command.extend(["-ResultPath", str(result_path)])
    if action == "preview":
        command.append("-WhatIf")
    return command


__all__ = ["PLUGIN_CAPABILITY", "PLUGIN_ID", "build_command", "default_configuration", "effective_configuration", "normalize_configuration"]
