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
RESPONSE_FEEDBACK_TAGS = ("like", "insightful", "clear", "engaging", "dry", "repetitive", "overreaching")
RESPONSE_FEEDBACK_MEANINGS = {
    "like": "overall approval",
    "insightful": "added useful understanding",
    "clear": "easy to follow",
    "engaging": "held the user's interest",
    "dry": "felt flat",
    "repetitive": "repeated itself or known information",
    "overreaching": "went beyond the evidence or request",
}


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


def _clean_title(value: object, limit: int = 80) -> str:
    cleaned = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    return cleaned[:limit].rstrip(" .!?-:")


def _title_fragment(value: object, limit: int = 52) -> str:
    cleaned = _clean_title(value, 240)
    first_sentence = re.split(r"(?<=[.!?])\s+", cleaned, maxsplit=1)[0]
    return first_sentence[:limit].rstrip(" .!?-:")


def title_from_document(document: object) -> str | None:
    if not isinstance(document, dict):
        return None
    metadata = document.get("metadata") if isinstance(document.get("metadata"), dict) else {}
    metadata_title = _clean_title(metadata.get("title"), 80)
    if metadata_title:
        return metadata_title
    filename = str(document.get("filename") or "").replace("\\", "/")
    stem = Path(filename).stem
    stem = re.sub(r"__signal-[A-Za-z0-9_-]+$", "", stem)
    stem = stem.replace("_", " ").replace("-", " ")
    return _title_fragment(stem, 80) or None


def title_from_exchange(message: object, answer: object) -> str | None:
    question = _title_fragment(message)
    takeaway = _title_fragment(answer)
    if not question:
        return takeaway or None
    if not takeaway:
        return question
    available = max(1, 80 - len(question) - 3)
    return f"{question} — {takeaway[:available].rstrip(' .!?-:')}"


def _meaningful_messages(messages: object) -> list[dict[str, Any]]:
    if not isinstance(messages, list):
        return []
    return [
        item for item in messages
        if isinstance(item, dict)
        and item.get("role") in {"user", "assistant"}
        and isinstance(item.get("content"), str)
        and item["content"].strip()
    ]


def _conversation_turn_count(messages: object) -> int:
    """Count real user turns without inventing one for an empty record."""
    user_turn_ids: set[str] = set()
    anonymous_user_turns = 0
    for item in _meaningful_messages(messages):
        if item.get("role") != "user":
            continue
        turn_id = item.get("turn_id")
        if isinstance(turn_id, str) and turn_id.strip():
            user_turn_ids.add(turn_id)
        else:
            anonymous_user_turns += 1
    return len(user_turn_ids) + anonymous_user_turns


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
        self.response_preferences_path = self.vault_root / "00_System" / "Data" / "response-preference-evidence.json"
        self.now_fn = now_fn

    def _response_preferences_locked(self) -> dict[str, Any]:
        try:
            value = json.loads(self.response_preferences_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}

    def _write_response_preferences_locked(self, value: dict[str, Any]) -> None:
        _atomic_bytes(self.response_preferences_path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    def interaction_memories(self, *, include_removed: bool = False) -> list[dict[str, Any]]:
        """Read the reviewable behavioral-memory layer stored with response feedback."""
        with _process_lock(self.lock_path):
            payload = self._response_preferences_locked()
            layer = payload.get("interaction_memories") if isinstance(payload.get("interaction_memories"), dict) else {}
            items = layer.get("items") if isinstance(layer.get("items"), list) else []
            return [dict(item) for item in items if isinstance(item, dict)
                    and (include_removed or item.get("status") != "removed")]

    def _mutate_interaction_memories(self, mutate: Callable[[list[dict[str, Any]]], Any]) -> Any:
        with _process_lock(self.lock_path):
            payload = self._response_preferences_locked()
            layer = payload.get("interaction_memories") if isinstance(payload.get("interaction_memories"), dict) else {}
            items = layer.get("items") if isinstance(layer.get("items"), list) else []
            items = [dict(item) for item in items if isinstance(item, dict)]
            result = mutate(items)
            payload["interaction_memories"] = {"version": 1, "items": items}
            self._write_response_preferences_locked(payload)
            return result

    def create_interaction_memory_candidate(self, lesson: str, *, scope: str = "general",
                                            source: dict[str, Any] | None = None,
                                            supersedes: str | None = None) -> dict[str, Any]:
        lesson = str(lesson or "").strip()
        scope = str(scope or "general").strip()
        if not lesson or len(lesson) > 800:
            raise ValueError("A learning candidate must contain 1–800 characters.")
        if not scope or len(scope) > 160:
            raise ValueError("A learning scope must contain 1–160 characters.")
        provenance = dict(source) if isinstance(source, dict) else {}
        evidence = provenance.get("evidence")
        if isinstance(evidence, list):
            if not 3 <= len(evidence) <= 8 or any(not isinstance(item, dict) for item in evidence):
                raise ValueError("A feedback-pattern candidate must cite 3–8 completed responses.")
            checked_evidence = []
            for item in evidence:
                source_chat = str(item.get("chat_id") or "")
                source_message = str(item.get("message_id") or "")
                if not _CHAT_ID.fullmatch(source_chat) or not re.fullmatch(r"[0-9a-f]{32}", source_message):
                    raise ValueError("A feedback-pattern source has invalid response provenance.")
                source_record = self.get(source_chat)
                source_response = next((entry for entry in (source_record or {}).get("messages", [])
                                        if isinstance(entry, dict) and entry.get("role") == "assistant"
                                        and entry.get("message_id") == source_message), None)
                if not source_response or source_response.get("state") != "complete":
                    raise ValueError("A feedback-pattern candidate must cite completed Ariadne responses.")
                checked_evidence.append({
                    "chat_id": source_chat, "message_id": source_message,
                    "ratings": [tag for tag in item.get("ratings", []) if tag in RESPONSE_FEEDBACK_TAGS],
                    "active_source_signal_ids": list(item.get("active_source_signal_ids") or [])[:32],
                    "timestamp": str(item.get("timestamp") or "")[:48],
                })
            if len({(item["chat_id"], item["message_id"]) for item in checked_evidence}) < 3:
                raise ValueError("A feedback-pattern candidate needs three distinct responses.")
            source_chat, source_message = checked_evidence[0]["chat_id"], checked_evidence[0]["message_id"]
            provenance["evidence"] = checked_evidence
        else:
            source_chat = str(provenance.get("chat_id") or "")
            source_message = str(provenance.get("message_id") or "")
            if source_chat and not _CHAT_ID.fullmatch(source_chat):
                raise ValueError("The source chat ID is invalid.")
            if source_message and not re.fullmatch(r"[0-9a-f]{32}", source_message):
                raise ValueError("The source response ID is invalid.")
            if not source_chat or not source_message:
                raise ValueError("A source chat and response ID are required for provenance.")
            source_record = self.get(source_chat)
            source_response = next((item for item in (source_record or {}).get("messages", [])
                                    if isinstance(item, dict) and item.get("role") == "assistant"
                                    and item.get("message_id") == source_message), None)
            if not source_response or source_response.get("state") != "complete":
                raise ValueError("A learning candidate must point to a completed Ariadne response.")
        if not isinstance(evidence, list) and provenance.get("kind") == "explicit_feedback" \
                and not isinstance(source_response.get("feedback"), dict):
            raise ValueError("Explicit feedback provenance requires feedback recorded on the source response.")
        candidate = {
            "memory_id": uuid.uuid4().hex,
            "lesson": lesson,
            "scope": scope,
            "status": "candidate",
            "created_at": isoformat(self.now_fn()),
            "source": {"chat_id": source_chat, "message_id": source_message,
                       "kind": str(provenance.get("kind") or "explicit_feedback")[:48],
                       "feedback_timestamp": str(provenance.get("feedback_timestamp") or "")[:48],
                       **({"evidence": provenance["evidence"]} if isinstance(provenance.get("evidence"), list) else {})},
            "revision": 1,
            "supersedes": supersedes if isinstance(supersedes, str) else None,
            "superseded_by": None,
        }

        def add(items: list[dict[str, Any]]) -> dict[str, Any]:
            if supersedes:
                old = next((item for item in items if item.get("memory_id") == supersedes), None)
                if not old or old.get("status") != "approved":
                    raise ValueError("Only an approved memory can be corrected or superseded.")
                if any(item.get("status") == "candidate" and item.get("supersedes") == supersedes for item in items):
                    raise ValueError("A correction candidate already exists for this memory.")
            source_key = sorted((entry.get("chat_id"), entry.get("message_id"))
                                for entry in candidate["source"].get("evidence", []))
            for item in items:
                if item.get("status") not in {"candidate", "approved"}:
                    continue
                old_source = item.get("source") if isinstance(item.get("source"), dict) else {}
                old_key = sorted((entry.get("chat_id"), entry.get("message_id"))
                                 for entry in old_source.get("evidence", []) if isinstance(entry, dict))
                same_single_source = (not source_key and not old_key
                                      and old_source.get("chat_id") == source_chat
                                      and old_source.get("message_id") == source_message)
                if ((source_key and source_key == old_key) or same_single_source) \
                        and str(item.get("lesson") or "").strip().casefold() == lesson.casefold() \
                        and str(item.get("scope") or "general").strip().casefold() == scope.casefold():
                    return dict(item)
            items.append(candidate)
            return dict(candidate)
        return self._mutate_interaction_memories(add)

    def approve_interaction_memory(self, memory_id: str) -> dict[str, Any]:
        def approve(items: list[dict[str, Any]]) -> dict[str, Any]:
            item = next((value for value in items if value.get("memory_id") == memory_id), None)
            if not item or item.get("status") != "candidate":
                raise ValueError("Only a pending learning candidate can be approved.")
            supersedes = item.get("supersedes")
            if supersedes:
                previous = next((value for value in items if value.get("memory_id") == supersedes), None)
                if not previous or previous.get("status") != "approved":
                    raise ValueError("The memory being corrected is no longer approved.")
                previous["status"] = "superseded"
                previous["superseded_by"] = memory_id
                previous["reviewed_at"] = isoformat(self.now_fn())
            item["status"] = "approved"
            item["reviewed_at"] = isoformat(self.now_fn())
            return dict(item)
        return self._mutate_interaction_memories(approve)

    def remove_interaction_memory(self, memory_id: str) -> dict[str, Any]:
        def remove(items: list[dict[str, Any]]) -> dict[str, Any]:
            item = next((value for value in items if value.get("memory_id") == memory_id), None)
            if not item or item.get("status") in {"removed", "superseded"}:
                raise ValueError("That interaction memory is not removable in its current state.")
            item["status"] = "removed"
            item["removed_at"] = isoformat(self.now_fn())
            return dict(item)
        return self._mutate_interaction_memories(remove)

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
            "title": None,
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
                   identity_kernel: dict[str, Any], title: str | None = None,
                   semantic: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
        with _process_lock(self.lock_path):
            record = self._load_locked(chat_id)
            if not record or record.get("status") != "active":
                raise ValueError("The durable Home chat is not active.")
            now = self.now_fn()
            timestamp = isoformat(now)
            turn_id = uuid.uuid4().hex
            user_message_id = uuid.uuid4().hex
            assistant_message_id = uuid.uuid4().hex
            from conversation_orchestration import assemble_turn
            job = assemble_turn(semantic or {}, message, record, turn_id)
            record["conversation_state"] = job["state"]
            if not record.get("messages") and title:
                record["title"] = _clean_title(title, 80)
            record["last_activity_at"] = timestamp
            record["expires_at"] = isoformat(now + timedelta(days=RETENTION_DAYS))
            record["model"] = model
            record["identity_kernel"] = identity_kernel
            record["messages"].extend([
                {
                    "message_id": user_message_id,
                    "turn_id": turn_id,
                    "role": "user",
                    "content": message,
                    "created_at": timestamp,
                    "state": "complete",
                    "response_state": "submitted",
                },
                {
                    "message_id": assistant_message_id,
                    "turn_id": turn_id,
                    "role": "assistant",
                    "content": "",
                    "created_at": timestamp,
                    "state": "pending",
                    "response_state": "generating",
                    "model": model,
                    "identity_kernel": identity_kernel,
                    "turn_assembly": job,
                },
            ])
            self._write_locked(record)
            return turn_id, record

    def record_lab_run(self, chat_id: str, run: dict[str, Any], code: str | None) -> dict[str, Any]:
        """Import an authoritative saved Lab result once into the selected chat."""
        run_id = str(run.get("run_id") or "")
        if not re.fullmatch(r"[0-9a-f]{32}", run_id):
            raise ValueError("Invalid Lab run ID.")
        with _process_lock(self.lock_path):
            record = self._load_locked(chat_id)
            if not record or record.get("status") != "active":
                raise ValueError("The selected chat is not active.")
            if any(item.get("lab_run_id") == run_id for item in record.get("messages", [])):
                return record
            timestamp = isoformat(self.now_fn())
            turn_id = uuid.uuid4().hex
            model = str(run.get("model") or "")
            answer = ("The Lab: " + str(run.get("project_name") or "Browser build") + "\n\n"
                      + str(run.get("summary") or "")
                      + "\nBuild state: " + str(run.get('state') or 'Unknown')
                      + ("\nDiagnostics: " + str(run['error']) if run.get('error') else '')
                      + f"\n\nRun ID: {run_id}\nModel: {model}\nContext: {run.get('context_tokens')} tokens"
                      + "\nProject: " + str(run.get("project_path") or "No project saved"))
            if code:
                fence = "`" * max(3, max((len(match.group()) + 1 for match in re.finditer(r"`+", code)), default=3))
                answer += f"\n\n{fence}html\n{code}\n{fence}"
            record["messages"].extend([
                {"message_id": uuid.uuid4().hex, "turn_id": turn_id, "role": "user",
                 "content": str(run.get("user_prompt") or ""), "created_at": run.get("timestamp") or timestamp,
                 "state": "complete", "response_state": "submitted", "lab_run_id": run_id},
                {"message_id": uuid.uuid4().hex, "turn_id": turn_id, "role": "assistant",
                 "content": answer, "created_at": timestamp, "completed_at": run.get("finished_at") or timestamp,
                 "state": "complete", "response_state": "completed", "model": model,
                 "lab_run_id": run_id, "lab_success": bool(run.get("success")), "used_vault": False,
                 "sources": [], "retrieval": {}, "timing": {}, "identity_kernel": {}},
            ])
            if not record.get("title"):
                record["title"] = _clean_title("The Lab: " + str(run.get("project_name") or "Browser build"), 80)
            record["model"] = model
            record["last_activity_at"] = timestamp
            record["expires_at"] = isoformat(self.now_fn() + timedelta(days=RETENTION_DAYS))
            self._write_locked(record)
            return record

    def record_context_recipe(self, chat_id: str, turn_id: str, recipe: dict[str, Any]) -> None:
        """Persist the prepared receipt before transport; retain it on interruption."""
        with _process_lock(self.lock_path):
            record = self._load_locked(chat_id)
            if not record:
                raise ValueError("Durable Home chat disappeared before recipe recording.")
            message = self._find_assistant_locked(record, turn_id)
            message["context_recipe"] = recipe
            self._write_locked(record)

    def _title_first_exchange_locked(self, record: dict[str, Any]) -> None:
        if record.get("title"):
            return
        messages = record.get("messages") if isinstance(record.get("messages"), list) else []
        first_user = next(
            (
                item for item in messages
                if isinstance(item, dict)
                and item.get("role") == "user"
                and isinstance(item.get("content"), str)
                and item.get("content", "").strip()
            ),
            None,
        )
        first_answer = next(
            (
                item for item in messages
                if isinstance(item, dict)
                and item.get("role") == "assistant"
                and item.get("state") == "complete"
                and isinstance(item.get("content"), str)
                and item.get("content", "").strip()
            ),
            None,
        )
        if not first_user or not first_answer:
            return
        title = title_from_exchange(first_user.get("content"), first_answer.get("content"))
        if title:
            record["title"] = title

    def title_from_first_exchange(self, chat_id: str) -> dict[str, Any] | None:
        with _process_lock(self.lock_path):
            record = self._load_locked(chat_id)
            if not record:
                return record
            before = record.get("title")
            self._title_first_exchange_locked(record)
            if record.get("title") != before:
                self._write_locked(record)
            return record

    def _find_assistant_locked(self, record: dict[str, Any], turn_id: str) -> dict[str, Any]:
        for item in reversed(record["messages"]):
            if item.get("turn_id") == turn_id and item.get("role") == "assistant":
                return item
        raise ValueError("Durable assistant turn not found.")

    def complete_turn(self, chat_id: str, turn_id: str, answer: str, *, model: str,
                      used_vault: bool, sources: list[Any], retrieval: dict[str, Any],
                      timing: dict[str, Any], identity_kernel: dict[str, Any],
                      active_source_signal_ids: list[str] | None = None) -> dict[str, Any]:
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
                "active_source_signal_ids": [
                    str(item) for item in (active_source_signal_ids or [])
                    if isinstance(item, str) and item.strip()
                ],
            })
            self._title_first_exchange_locked(record)
            record["last_activity_at"] = timestamp
            record["expires_at"] = isoformat(now + timedelta(days=RETENTION_DAYS))
            self._write_locked(record)
            return record

    def record_feedback(self, chat_id: str, message_id: str, rating: str,
                        active_source_signal_ids: list[str] | None = None,
                        comment: str = "") -> dict[str, Any]:
        if rating not in {"good", "needs_work", "wrong"}:
            raise ValueError("Feedback rating must be good, needs_work, or wrong.")
        if not isinstance(message_id, str) or not message_id.strip():
            raise ValueError("A response message_id is required.")
        if not isinstance(comment, str):
            raise ValueError("Feedback comment must be text.")
        signal_ids = [
            str(item).strip() for item in (active_source_signal_ids or [])
            if isinstance(item, str) and item.strip()
        ][:32]
        with _process_lock(self.lock_path):
            record = self._load_locked(chat_id)
            if not record or record.get("status") != "active":
                raise ValueError("The durable Home chat is not active.")
            message = next(
                (
                    item for item in reversed(record.get("messages", []))
                    if isinstance(item, dict)
                    and item.get("role") == "assistant"
                    and (item.get("message_id") == message_id or item.get("turn_id") == message_id)
                ),
                None,
            )
            if not message:
                raise ValueError("That Ariadne response was not found in this chat.")
            if message.get("state") != "complete":
                raise ValueError("Feedback can only be recorded for a completed Ariadne response.")
            timestamp = isoformat(self.now_fn())
            feedback = {
                **(message.get("feedback") if isinstance(message.get("feedback"), dict) else {}),
                "chat_id": chat_id,
                "message_id": str(message.get("message_id") or message.get("turn_id") or message_id),
                "active_source_signal_ids": signal_ids,
                "rating": rating,
                "comment": comment.strip()[:2_000],
                "timestamp": timestamp,
            }
            message["feedback"] = feedback
            record["last_activity_at"] = timestamp
            record["expires_at"] = isoformat(self.now_fn() + timedelta(days=RETENTION_DAYS))
            self._write_locked(record)
        self._record_response_preference(feedback)
        return feedback

    def set_feedback_tags(self, chat_id: str, message_id: str, tags: list[str],
                          active_source_signal_ids: list[str] | None = None) -> dict[str, Any]:
        """Persist the complete independent selection set for one response."""
        if not isinstance(tags, list) or any(not isinstance(tag, str) or tag not in RESPONSE_FEEDBACK_TAGS for tag in tags):
            raise ValueError("Choose only the available response feedback tags.")
        if not isinstance(message_id, str) or not message_id.strip():
            raise ValueError("A response message_id is required.")
        signal_ids = list(dict.fromkeys(
            str(item).strip() for item in (active_source_signal_ids or [])
            if isinstance(item, str) and item.strip()
        ))[:32]
        with _process_lock(self.lock_path):
            record = self._load_locked(chat_id)
            if not record or record.get("status") != "active":
                raise ValueError("The durable Home chat is not active.")
            message = next((item for item in reversed(record.get("messages", []))
                           if isinstance(item, dict) and item.get("role") == "assistant"
                           and (item.get("message_id") == message_id or item.get("turn_id") == message_id)), None)
            if not message or message.get("state") != "complete":
                raise ValueError("Feedback can only be recorded for a completed Ariadne response.")
            previous = message.get("feedback") if isinstance(message.get("feedback"), dict) else {}
            current = list(dict.fromkeys(tags))
            timestamp = isoformat(self.now_fn())
            feedback = {
                **previous,
                "chat_id": chat_id,
                "message_id": str(message.get("message_id") or message.get("turn_id") or message_id),
                "active_source_signal_ids": signal_ids or list(previous.get("active_source_signal_ids")
                                                                or message.get("active_source_signal_ids") or []),
                "ratings": current,
                "timestamp": timestamp,
            }
            message["feedback"] = feedback
            record["last_activity_at"] = timestamp
            record["expires_at"] = isoformat(self.now_fn() + timedelta(days=RETENTION_DAYS))
            self._write_locked(record)
        self._record_response_tag_state(feedback)
        return feedback

    def save_feedback_comment(self, chat_id: str, message_id: str, comment: str) -> dict[str, Any]:
        if not isinstance(comment, str):
            raise ValueError("Feedback comment must be text.")
        with _process_lock(self.lock_path):
            record = self._load_locked(chat_id)
            if not record or record.get("status") != "active":
                raise ValueError("The durable Home chat is not active.")
            message = next((item for item in reversed(record.get("messages", []))
                           if isinstance(item, dict) and item.get("role") == "assistant"
                           and (item.get("message_id") == message_id or item.get("turn_id") == message_id)), None)
            if not message or message.get("state") != "complete":
                raise ValueError("Feedback can only be recorded for a completed Ariadne response.")
            timestamp = isoformat(self.now_fn())
            feedback = dict(message.get("feedback") or {})
            feedback.update({"chat_id": chat_id,
                             "message_id": str(message.get("message_id") or message.get("turn_id") or message_id),
                             "comment": comment.strip()[:2_000], "timestamp": timestamp})
            message["feedback"] = feedback
            record["last_activity_at"] = timestamp
            record["expires_at"] = isoformat(self.now_fn() + timedelta(days=RETENTION_DAYS))
            self._write_locked(record)
        self._record_response_comment(feedback)
        return feedback

    def _record_response_tag_state(self, feedback: dict[str, Any]) -> None:
        with _process_lock(self.lock_path):
            existing = self._response_preferences_locked()
            responses = existing.get("tag_feedback_by_response")
            responses = dict(responses) if isinstance(responses, dict) else {}
            response_key = f"{feedback['chat_id']}:{feedback['message_id']}"
            responses[response_key] = {
                "chat_id": feedback["chat_id"], "message_id": feedback["message_id"],
                "ratings": [tag for tag in feedback.get("ratings", []) if tag in RESPONSE_FEEDBACK_TAGS],
                "active_source_signal_ids": list(feedback.get("active_source_signal_ids") or []),
                "comment": str(feedback.get("comment") or "")[:500],
                "timestamp": feedback.get("timestamp"),
            }
            tag_counts = {tag: 0 for tag in RESPONSE_FEEDBACK_TAGS}
            for item in responses.values():
                if isinstance(item, dict):
                    for tag in {value for value in item.get("ratings", []) if isinstance(value, str)}:
                        if tag in tag_counts:
                            tag_counts[tag] += 1
            existing["tag_feedback_by_response"] = responses
            existing["tag_counts"] = tag_counts
            existing["tag_response_count"] = sum(bool(item.get("ratings")) for item in responses.values() if isinstance(item, dict))
            existing["tag_comment_count"] = sum(bool(str(item.get("comment") or "").strip())
                                                 for item in responses.values() if isinstance(item, dict))
            existing["updated_at"] = feedback.get("timestamp")
            self._write_response_preferences_locked(existing)

    def _record_response_comment(self, feedback: dict[str, Any]) -> None:
        with _process_lock(self.lock_path):
            existing = self._response_preferences_locked()
            comments = existing.get("tag_feedback_by_response")
            comments = dict(comments) if isinstance(comments, dict) else {}
            response_key = f"{feedback['chat_id']}:{feedback['message_id']}"
            item = comments.get(response_key) if isinstance(comments.get(response_key), dict) else {}
            item.update({"chat_id": feedback["chat_id"], "message_id": feedback["message_id"],
                         "ratings": [tag for tag in feedback.get("ratings", []) if tag in RESPONSE_FEEDBACK_TAGS],
                         "active_source_signal_ids": list(feedback.get("active_source_signal_ids") or []),
                         "comment": str(feedback.get("comment") or "")[:500], "timestamp": feedback.get("timestamp")})
            comments[response_key] = item
            existing["tag_feedback_by_response"] = comments
            existing["tag_comment_count"] = sum(bool(str(value.get("comment") or "").strip())
                                                  for value in comments.values() if isinstance(value, dict))
            existing["updated_at"] = feedback.get("timestamp")
            self._write_response_preferences_locked(existing)

    def response_feedback_evidence(self, limit: int = 30) -> dict[str, Any]:
        preferences = self.response_preferences()
        responses = preferences.get("tag_feedback_by_response")
        responses = responses if isinstance(responses, dict) else {}
        responses = [item for item in responses.values() if isinstance(item, dict) and item.get("ratings")]
        responses.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
        bounded = []
        for item in responses[:max(0, min(30, int(limit)))]:
            chat_id = str(item.get("chat_id") or "")
            message_id = str(item.get("message_id") or "")
            record = self.get(chat_id) if _CHAT_ID.fullmatch(chat_id) else None
            source = next((message for message in (record or {}).get("messages", [])
                           if isinstance(message, dict) and message.get("role") == "assistant"
                           and message.get("message_id") == message_id and message.get("state") == "complete"), None)
            if not source:
                continue
            bounded.append({
                "evidence_id": f"{chat_id}:{message_id}",
                "chat_id": chat_id,
                "message_id": message_id,
                "ratings": [tag for tag in item.get("ratings", []) if tag in RESPONSE_FEEDBACK_TAGS],
                "active_source_signal_ids": list(item.get("active_source_signal_ids") or [])[:32],
                "comment": str(item.get("comment") or "")[:500],
                "timestamp": item.get("timestamp"),
            })
        return {"counts": preferences.get("tag_counts", {}),
                "rating_guide": dict(RESPONSE_FEEDBACK_MEANINGS), "responses": bounded}

    def _record_response_preference(self, feedback: dict[str, Any]) -> None:
        """Persist answer-feedback evidence separately from chat transcripts."""
        with _process_lock(self.lock_path):
            existing = self._response_preferences_locked()
            ratings = existing.get("ratings") if isinstance(existing.get("ratings"), dict) else {}
            rating = str(feedback.get("rating") or "")
            ratings[rating] = int(ratings.get(rating, 0)) + 1
            comments = existing.get("comments") if isinstance(existing.get("comments"), list) else []
            comment = str(feedback.get("comment") or "").strip()
            if comment:
                comments.append({"rating": rating, "comment": comment[:2_000], "timestamp": feedback.get("timestamp")})
            existing.update({"version": 1, "ratings": ratings, "comment_count": len(comments),
                             "comments": comments[-100:], "updated_at": feedback.get("timestamp")})
            self._write_response_preferences_locked(existing)

    def response_preferences(self) -> dict[str, Any]:
        with _process_lock(self.lock_path):
            value = self._response_preferences_locked()
            return value if value else {"version": 1, "ratings": {}, "comment_count": 0, "comments": []}

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

    def _format_markdown(self, record: dict[str, Any], ended_at: datetime, *, saved_to_inbox: bool = False) -> str:
        """Render the canonical portable transcript without diagnostic payloads."""
        lines = [
            "---",
            "kind: ariadne-home-chat",
            f"chat_id: {record['chat_id']}",
            f"title: {_yaml_string(record.get('title'))}",
            f"started: {record.get('started_at') or ''}",
            f"ended: {isoformat(ended_at)}",
            f"model: {_yaml_string(record.get('model') or '')}",
            f"identity_kernel: {_yaml_string((record.get('identity_kernel') or {}).get('version') or 'unknown')}",
        ]
        if saved_to_inbox:
            lines.append("saved_to_inbox: true")
        lines.extend(["---", "", f"# {record.get('title') or 'Ariadne Home chat'}", ""])
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
        return "\n".join(lines).rstrip() + "\n"

    def list_recent(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        with _process_lock(self.lock_path):
            self.root.mkdir(parents=True, exist_ok=True)
            for path in sorted(self.root.glob("*.json"), key=lambda item: item.name):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                chat_id = record.get("chat_id") if isinstance(record, dict) else None
                if not isinstance(chat_id, str) or not _CHAT_ID.fullmatch(chat_id) or path.stem != chat_id:
                    continue
                messages = record.get("messages") if isinstance(record.get("messages"), list) else []
                meaningful_messages = _meaningful_messages(messages)
                # Archives and Inbox copies remain on disk, but a record with no
                # actual dialogue is not a Recent Chat. This also hides legacy
                # empty archive shells created when a fresh chat was closed.
                if not meaningful_messages:
                    continue
                rows.append({
                    "chat_id": chat_id,
                    "title": str(record.get("title") or "Ariadne Home chat"),
                    "status": str(record.get("status") or "active"),
                    "started_at": record.get("started_at"),
                    "last_activity_at": record.get("last_activity_at"),
                    "expires_at": record.get("expires_at"),
                    "message_count": len(messages),
                    "turn_count": _conversation_turn_count(messages),
                    "has_interrupted": any(isinstance(item, dict) and item.get("state") == "interrupted" for item in messages),
                    "archive_path": record.get("archive_path"),
                    "inbox_path": record.get("inbox_path"),
                })
        rows.sort(key=lambda item: str(item.get("last_activity_at") or ""), reverse=True)
        return rows

    def cleanup_empty_transient(self, protected_chat_ids: set[str] | None = None) -> list[dict[str, Any]]:
        """Remove only empty temporary JSON records, preserving copies and context."""
        removed: list[dict[str, Any]] = []
        protected = {
            item for item in (protected_chat_ids or set())
            if isinstance(item, str) and _CHAT_ID.fullmatch(item)
        }
        with _process_lock(self.lock_path):
            self.root.mkdir(parents=True, exist_ok=True)
            for path in sorted(self.root.glob("*.json"), key=lambda item: item.name):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                chat_id = record.get("chat_id") if isinstance(record, dict) else None
                if not isinstance(chat_id, str) or not _CHAT_ID.fullmatch(chat_id) or path.stem != chat_id:
                    continue
                if chat_id in protected or _meaningful_messages(record.get("messages")):
                    continue
                # Inbox and archive copies are deliberate permanent lifecycle
                # outputs; leave both the copy and its record alone.
                if record.get("inbox_path") or record.get("archive_path"):
                    continue
                try:
                    path.unlink()
                except OSError:
                    continue
                removed.append(record)
        return removed

    def resume(self, chat_id: str) -> dict[str, Any]:
        with _process_lock(self.lock_path):
            record = self._load_locked(chat_id)
            if not record:
                raise ValueError("Temporary Home chat not found.")
            if record.get("status") not in {"active", "closed"}:
                raise ValueError("This temporary Home chat is no longer available.")
            now = self.now_fn()
            record["status"] = "active"
            record["resumed_at"] = isoformat(now)
            record["last_activity_at"] = isoformat(now)
            record["expires_at"] = isoformat(now + timedelta(days=RETENTION_DAYS))
            self._write_locked(record)
            return record

    def _inbox_path(self, record: dict[str, Any]) -> Path:
        directory = (self.vault_root / "Inbox").resolve()
        existing = record.get("inbox_path")
        if isinstance(existing, str) and existing:
            existing_path = (self.vault_root / existing).resolve()
            if existing_path.parent == directory and existing_path.suffix == ".md":
                return existing_path
            raise ValueError("Stored Inbox path escaped the vault Inbox directory.")
        started = parse_time(record.get("started_at")) or self.now_fn()
        filename = f"{started.astimezone().strftime('%Y-%m-%d_%H%M')}_{_safe_filename(str(record.get('title') or 'chat'))}_{record['chat_id'][:8]}.md"
        path = (directory / filename).resolve()
        if path.parent != directory:
            raise ValueError("Inbox path escaped the vault Inbox directory.")
        return path

    def save_to_inbox(self, chat_id: str) -> tuple[dict[str, Any], str]:
        with _process_lock(self.lock_path):
            record = self._load_locked(chat_id)
            if not record:
                raise ValueError("Temporary Home chat not found.")
            if not _meaningful_messages(record.get("messages")):
                raise ValueError("Empty chats cannot be saved to Inbox.")
            now = self.now_fn()
            path = self._inbox_path(record)
            _atomic_bytes(path, self._format_markdown(record, now, saved_to_inbox=True).encode("utf-8"))
            record["inbox_path"] = path.relative_to(self.vault_root).as_posix()
            record["inbox_saved_at"] = isoformat(now)
            self._write_locked(record)
            return record, record["inbox_path"]

    def export_markdown(self, chat_id: str) -> tuple[str, str]:
        with _process_lock(self.lock_path):
            record = self._load_locked(chat_id)
            if not record:
                raise ValueError("Temporary Home chat not found.")
            if not _meaningful_messages(record.get("messages")):
                raise ValueError("Empty chats cannot be exported.")
            now = self.now_fn()
            filename = f"{_safe_filename(str(record.get('title') or 'chat'))}_{chat_id[:8]}.md"
            return self._format_markdown(record, now), filename

    def purge(self, chat_id: str) -> dict[str, Any]:
        with _process_lock(self.lock_path):
            path = self._path(chat_id)
            record = self._load_locked(chat_id)
            if not record:
                raise ValueError("Temporary Home chat not found.")
            path.unlink()
            return record

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

