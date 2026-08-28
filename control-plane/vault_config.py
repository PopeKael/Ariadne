"""Single Ariadne configuration boundary for the authoritative KnowledgeVault."""

from __future__ import annotations

from pathlib import Path

from ariadne_config import DEFAULT_STORAGE, configuration_snapshot

DEFAULT_VAULT_ROOT = Path(DEFAULT_STORAGE["knowledge_vault"])


_configuration = configuration_snapshot()
VAULT_ROOT = Path(_configuration["storage"]["knowledge_vault"])
VAULT_ROOT_SOURCE = str(_configuration["sources"]["knowledge_vault"])


def vault_counts(root: Path | None = None) -> dict[str, object]:
    """Return inspectable catalogue and embedding counts for startup/health."""
    root = root or VAULT_ROOT
    system = root / "00_System"
    result: dict[str, object] = {
        "root": str(root),
        "source": VAULT_ROOT_SOURCE if root == VAULT_ROOT else "explicit path",
        "available": root.is_dir(),
        "catalogue_records": 0,
        "embedding_documents": 0,
        "embedding_chunks": 0,
        "embedding_failures": 0,
    }
    try:
        catalogue = __import__("json").loads((system / "library.json").read_text(encoding="utf-8-sig"))
        result["catalogue_records"] = len(catalogue) if isinstance(catalogue, list) else 0
    except (OSError, ValueError, TypeError):
        pass
    try:
        index = __import__("json").loads((system / "Data" / "embedding-index.json").read_text(encoding="utf-8"))
        entries = index.get("entries", {}) if isinstance(index, dict) else {}
        failures = index.get("failures", {}) if isinstance(index, dict) else {}
        result["embedding_chunks"] = len(entries) if isinstance(entries, dict) else 0
        result["embedding_failures"] = len(failures) if isinstance(failures, dict) else 0
        result["embedding_documents"] = len({str(item.get("path")) for item in entries.values() if isinstance(item, dict)})
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return result
