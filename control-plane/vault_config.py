"""Single Ariadne configuration boundary for the authoritative KnowledgeVault."""

from __future__ import annotations

from pathlib import Path
from threading import Lock

from ariadne_config import DEFAULT_STORAGE, configuration_snapshot

DEFAULT_VAULT_ROOT = Path(DEFAULT_STORAGE["knowledge_vault"])


_configuration = configuration_snapshot()
VAULT_ROOT = Path(_configuration["storage"]["knowledge_vault"])
VAULT_ROOT_SOURCE = str(_configuration["sources"]["knowledge_vault"])


_COUNT_CACHE = {}
_COUNT_LOCK = Lock()


def _count_signature(root):
    signature = []
    for path in (root, root / "00_System" / "library.json", root / "00_System" / "Data" / "embedding-index.json"):
        try:
            info = path.stat()
            signature.append((info.st_mtime_ns, info.st_size))
        except OSError:
            signature.append(None)
    return tuple(signature)


def vault_counts(root: Path | None = None) -> dict[str, object]:
    """Reuse counts until their source files change; never cache embeddings."""
    root = root or VAULT_ROOT
    signature = _count_signature(root)
    with _COUNT_LOCK:
        cached = _COUNT_CACHE.get(root)
        if cached is not None and cached[0] == signature:
            return dict(cached[1])
        result = _read_vault_counts(root)
        _COUNT_CACHE[root] = (signature, result)
        return dict(result)


def _read_vault_counts(root: Path) -> dict[str, object]:
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
        result["embedding_updated_at"] = index.get("updated_at") if isinstance(index, dict) else None
        entries = index.get("entries", {}) if isinstance(index, dict) else {}
        failures = index.get("failures", {}) if isinstance(index, dict) else {}
        result["embedding_chunks"] = len(entries) if isinstance(entries, dict) else 0
        result["embedding_failures"] = len(failures) if isinstance(failures, dict) else 0
        result["embedding_documents"] = len({str(item.get("path")) for item in entries.values() if isinstance(item, dict)})
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return result
