"""Single Ariadne configuration boundary for the authoritative KnowledgeVault."""

from __future__ import annotations

import os
from pathlib import Path


# The application repository is not a KnowledgeVault.  This default is
# deliberately explicit so a missing environment variable cannot silently
# turn the repository checkout into a second, stale corpus.
DEFAULT_VAULT_ROOT = Path(r"D:\Downloads\KnowledgeVault")


def configured_vault_root() -> tuple[Path, str]:
    override = os.environ.get("ARIADNE_VAULT_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve(), "environment override"
    return DEFAULT_VAULT_ROOT.resolve(), "configured default"


VAULT_ROOT, VAULT_ROOT_SOURCE = configured_vault_root()


def vault_counts(root: Path = VAULT_ROOT) -> dict[str, object]:
    """Return inspectable catalogue and embedding counts for startup/health."""
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
