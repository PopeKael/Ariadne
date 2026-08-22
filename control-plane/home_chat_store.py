"""Durable, short-lived Home chat records and provider-independent archives."""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator


CHAT_SCHEMA_VERSION = 1
RETENTION_DAYS = 7
_CHAT_ID = re.compile(r"^[0-9a-f]{32}$")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def title_from_message(message: str) -> str:
    cleaned = " ".join(message.replace("\r", " ").replace("\n", " ").split())
    return cleaned[:80].rstrip() or "Ariadne Home chat"


def _safe_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9 _-]+", "", value).strip()
    value = re.sub(r"\s+", " ", value)
    return (value[:72].rstrip(" .-") or "chat")


def _yaml_string(value: object) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False)


def _atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(8):
            try:
                os.replace(temporary_name, path)
                return
            except OSError as exc:
                if not isinstance(exc, PermissionError) and getattr(exc, "winerror", None) not in {5, 32, 33}:
                    raise
                if attempt == 7:
                    raise RuntimeError(f"Could not atomically replace {path}") from exc
                time.sleep(0.05 * (2 ** attempt))
    finally:
        if os.path.exists(temporary_name):
            try:
                os.unlink(temporary_name)
            except OSError:
                pass


@contextmanager
def _process_lock(path: Path) -> Iterator[None]:
    """Hold a small cross-process lock for the short read/modify/write window."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class ChatStore:
    """Own HomeSessions JSON and Archive/Chats Markdown for one vault root."""

    def __init__(self, vault_root: Path, now_fn: Callable[[], datetime] = utc_now) -> None:
        self.vault_root = Path(vault_root).resolve()
        self.root = (self.vault_root / "00_System" / "Data" / "HomeSessions").resolve()
        self.archive_root = (self.vault_root / "Archive" / "Chats").resolve()
        self.lock_path = self.root / ".chats.lock"
        self.now_fn = now_fn

    def _path(self, chat_id: str) -> Path:
        if not isinstance(chat_id, str) or not _CHAT_ID.fullmatch(chat_id):
            raise ValueError("Invalid durable chat_id.")
        path = (self.root / f"{chat_id}.json").resolve()
        if path.parent != self.root:
            raise ValueError("Chat path escaped HomeSessions.")
        return path

    def _load_locked(self, chat_id: str) -> dict[str, Any] | None:
        path = self._path(chat_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict) or value.get("chat_id") != chat_id:
            return None
        if not isinstance(value.get("messages"), list):
            return None
        return value

    def _write_locked(self, record: dict[str, Any]) -> None:
        _atomic_bytes(
            self._path(str(record["chat_id"])),
            (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )

    def _new_record_locked(self, identity_kernel: dict[str, Any] | None = None) -> dict[str, Any]:
        now = self.now_fn()
        chat_id = uuid.uuid4().hex
        timestamp = isoformat(now)
        return {
            "schema_version": CHAT_SCHEMA_VERSION,
            "chat_id": chat_id,
            "title": "Ariadne Home chat",
            "status": "active",
            "started_at": timestamp,
            "last_activity_at": timestamp,
            "expires_at": isoformat(now + timedelta(days=RETENTION_DAYS)),
            "model": None,
            "identity_kernel": identity_kernel or {},
            "messages": [],
            "archive_path": None,
        }

    def create(self, identity_kernel: dict[str, Any] | None = None) -> dict[str, Any]:
        with _process_lock(self.lock_path):
            record = self._new_record_locked(identity_kernel)
            self._write_locked(record)
            return record

    def get(self, chat_id: str) -> dict[str, Any] | None:
        with _process_lock(self.lock_path):
            return self._load_locked(chat_id)

    def get_or_create(self, requested_chat_id: object = None,
                      identity_kernel: dict[str, Any] | None = None) -> tuple[dict[str, Any], bool]:
        with _process_lock(self.lock_path):
            if isinstance(requested_chat_id, str) and _CHAT_ID.fullmatch(requested_chat_id):
                record = self._load_locked(requested_chat_id)
                if record and record.get("status") == "active":
                    if identity_kernel:
                        record["identity_kernel"] = identity_kernel
                        self._write_locked(record)
                    return record, True
            record = self._new_record_locked(identity_kernel)
            self._write_locked(record)
            return record, False

    def model_history(self, chat_id: str, limit: int = 8) -> list[dict[str, str]]:
        with _process_lock(self.lock_path):
            record = self._load_locked(chat_id)
            if not record:
                return []
            history = [
                {"role": item["role"], "content": item["content"][:4_000]}
                for item in record["messages"]
                if isinstance(item, dict)
                and item.get("role") in {"user", "assistant"}
                and item.get("state") == "complete"
                and isinstance(item.get("content"), str)
                and item["content"].strip()
            ]
            return history[-max(1, limit):]

    def begin_turn(self, chat_id: str, message: str, model: str,
                   identity_kernel: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        with _process_lock(self.lock_path):
            record = self._load_locked(chat_id)
            if not record or record.get("status") != "active":
                raise ValueError("The durable Home chat is not active.")
            now = self.now_fn()
            timestamp = isoformat(now)
            turn_id = uuid.uuid4().hex
            if not record.get("messages"):
                record["title"] = title_from_message(message)
            record["last_activity_at"] = timestamp
            record["expires_at"] = isoformat(now + timedelta(days=RETENTION_DAYS))
            record["model"] = model
            record["identity_kernel"] = identity_kernel
            record["messages"].extend([
                {
                    "turn_id": turn_id,
                    "role": "user",
                    "content": message,
                    "created_at": timestamp,
                    "state": "complete",
                    "response_state": "submitted",
                },
                {
                    "turn_id": turn_id,
                    "role": "assistant",
                    "content": "",
                    "created_at": timestamp,
                    "state": "pending",
                    "response_state": "generating",
                    "model": model,
                    "identity_kernel": identity_kernel,
                },
            ])
            self._write_locked(record)
            return turn_id, record

    def _find_assistant_locked(self, record: dict[str, Any], turn_id: str) -> dict[str, Any]:
        for item in reversed(record["messages"]):
            if item.get("turn_id") == turn_id and item.get("role") == "assistant":
                return item
        raise ValueError("Durable assistant turn not found.")

    def complete_turn(self, chat_id: str, turn_id: str, answer: str, *, model: str,
                      used_vault: bool, sources: list[Any], retrieval: dict[str, Any],
                      timing: dict[str, Any], identity_kernel: dict[str, Any]) -> dict[str, Any]:
        with _process_lock(self.lock_path):
            record = self._load_locked(chat_id)
            if not record:
                raise ValueError("Durable Home chat disappeared before response completion.")
            message = self._find_assistant_locked(record, turn_id)
            now = self.now_fn()
            timestamp = isoformat(now)
            message.update({
                "content": answer,
                "completed_at": timestamp,
                "state": "complete",
                "response_state": "completed",
                "model": model,
                "used_vault": used_vault,
                "sources": sources,
                "retrieval": retrieval,
                "timing": timing,
                "identity_kernel": identity_kernel,
            })
            record["last_activity_at"] = timestamp
            record["expires_at"] = isoformat(now + timedelta(days=RETENTION_DAYS))
            self._write_locked(record)
            return record

    def interrupt_turn(self, chat_id: str, turn_id: str, error: str) -> dict[str, Any] | None:
        with _process_lock(self.lock_path):
            record = self._load_locked(chat_id)
            if not record:
                return None
            message = self._find_assistant_locked(record, turn_id)
            now = self.now_fn()
            timestamp = isoformat(now)
            message.update({
                "completed_at": timestamp,
                "state": "interrupted",
                "response_state": "interrupted",
                "error": error[:800],
            })
            record["last_activity_at"] = timestamp
            record["expires_at"] = isoformat(now + timedelta(days=RETENTION_DAYS))
            self._write_locked(record)
            return record

    def _archive_locked(self, record: dict[str, Any], ended_at: datetime) -> str:
        started = parse_time(record.get("started_at")) or ended_at
        directory = self.archive_root / f"{started.year:04d}" / f"{started.month:02d}"
        filename = f"{started.astimezone().strftime('%Y-%m-%d_%H%M')}_{_safe_filename(str(record.get('title') or 'chat'))}_{record['chat_id'][:8]}.md"
        path = (directory / filename).resolve()
        if self.archive_root not in path.parents:
            raise ValueError("Chat archive path escaped Archive/Chats.")
        lines = [
            "---",
            "kind: ariadne-home-chat",
            f"chat_id: {record['chat_id']}",
            f"title: {_yaml_string(record.get('title'))}",
            f"started: {record.get('started_at') or ''}",
            f"ended: {isoformat(ended_at)}",
            f"model: {_yaml_string(record.get('model') or '')}",
            f"identity_kernel: {_yaml_string((record.get('identity_kernel') or {}).get('version') or 'unknown')}",
            "---",
            "",
            f"# {record.get('title') or 'Ariadne Home chat'}",
            "",
        ]
        for item in record.get("messages", []):
            if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
                continue
            label = "Wazza" if item.get("role") == "user" else "Ariadne"
            lines.extend([f"## {label}", ""])
            state = item.get("state")
            content = str(item.get("content") or "").strip()
            if content:
                lines.extend([content, ""])
            elif state == "pending":
                lines.extend(["_Response pending when this chat was archived._", ""])
            elif state == "interrupted":
                lines.extend(["_Response interrupted; no complete response was recorded._", ""])
        _atomic_bytes(path, ("\n".join(lines).rstrip() + "\n").encode("utf-8"))
        return path.relative_to(self.vault_root).as_posix()

    def close_and_archive(self, chat_id: str) -> tuple[dict[str, Any], str]:
        with _process_lock(self.lock_path):
            record = self._load_locked(chat_id)
            if not record:
                raise ValueError("Durable Home chat not found.")
            now = self.now_fn()
            record["status"] = "closed"
            record["closed_at"] = isoformat(now)
            record["last_activity_at"] = isoformat(now)
            record["expires_at"] = isoformat(now + timedelta(days=RETENTION_DAYS))
            archive_path = self._archive_locked(record, now)
            record["archive_path"] = archive_path
            self._write_locked(record)
            return record, archive_path

    def cleanup_expired(self) -> list[dict[str, Any]]:
        expired: list[dict[str, Any]] = []
        with _process_lock(self.lock_path):
            self.root.mkdir(parents=True, exist_ok=True)
            for path in sorted(self.root.glob("*.json"), key=lambda item: item.name):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(record, dict) or not isinstance(record.get("chat_id"), str):
                    continue
                if not _CHAT_ID.fullmatch(record["chat_id"]) or path.stem != record["chat_id"]:
                    continue
                expiry = parse_time(record.get("expires_at"))
                if expiry is None or expiry > self.now_fn():
                    continue
                ended = self.now_fn()
                archive_path = self._archive_locked(record, ended)
                record["archive_path"] = archive_path
                record["status"] = "expired"
                record["expired_at"] = isoformat(ended)
                try:
                    path.unlink()
                except OSError:
                    continue
                expired.append(record)
        return expired

