"""Generic, trusted on-demand plugin execution boundary."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from plugin_registry import PluginRecord


class PluginExecutionError(RuntimeError):
    pass


def _entry_point(record: PluginRecord) -> Any:
    if record.source != "bundled" or not record.manifest:
        raise PluginExecutionError("Only trusted bundled plugin adapters may run in Ariadne v1.")
    entry_point = str(record.manifest.get("entry_point") or "")
    if ":" not in entry_point:
        raise PluginExecutionError("Plugin entry_point must use module:function form.")
    module_name, function_name = entry_point.split(":", 1)
    if not module_name or not function_name.isidentifier() or any(not part.isidentifier() for part in module_name.split(".")):
        raise PluginExecutionError("Plugin entry_point is invalid.")
    plugin_root = Path(record.manifest_path).resolve().parent
    module_path = (plugin_root / (module_name.replace(".", "/") + ".py")).resolve()
    try:
        module_path.relative_to(plugin_root)
    except ValueError as exc:
        raise PluginExecutionError("Plugin entry_point escapes its plugin folder.") from exc
    if not module_path.is_file():
        raise PluginExecutionError(f"Plugin adapter module is missing: {module_path}")
    name = "ariadne_plugin_" + record.manifest["plugin_id"].replace("-", "_").replace(".", "_")
    spec = importlib.util.spec_from_file_location(name, module_path)
    if spec is None or spec.loader is None:
        raise PluginExecutionError(f"Could not load plugin adapter: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    adapter = getattr(module, function_name, None)
    if not callable(adapter):
        raise PluginExecutionError(f"Plugin adapter function is unavailable: {entry_point}")
    return adapter


def build_plugin_command(record: PluginRecord, action: str, config: dict[str, object], context: dict[str, object]) -> list[str]:
    if not record.manifest:
        raise PluginExecutionError("Plugin manifest is unavailable.")
    if action not in record.manifest.get("actions", []):
        raise PluginExecutionError(f"Plugin does not support action: {action}")
    command = _entry_point(record)(action, config, context)
    if not isinstance(command, list) or not command or any(not isinstance(item, str) or not item for item in command):
        raise PluginExecutionError("Plugin adapter returned an invalid process command.")
    return command


__all__ = ["PluginExecutionError", "build_plugin_command"]
