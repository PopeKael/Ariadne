"""Small cross-process lock for rebuild-v1 ingestion operations."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


@contextmanager
def ingestion_lock(root: Path) -> Iterator[Path]:
    """Prevent concurrent daily/remediation runs without deleting a stale lock."""
    path = root / "Logs" / "rebuild-v1-ingest.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"pid": os.getpid(), "created_at": datetime.now(timezone.utc).isoformat()}) + "\n"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as exc:
        detail = path.read_text(encoding="utf-8", errors="replace").strip()
        raise RuntimeError(f"Another rebuild-v1 ingestion run holds {path}: {detail}. Inspect it before removing any stale lock.") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        yield path
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
