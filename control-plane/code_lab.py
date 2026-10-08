"""Single-file browser generation bench; no tools or process execution."""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from html.parser import HTMLParser
from ariadne_config import _read_saved, effective_storage

INSTRUCTIONS = (
    "Create the application requested by the user. Return only a JSON project object "
    "with project_name, summary, entrypoint and files. V1 requires entrypoint index.html "
    "and exactly one file with path index.html and content containing all HTML, CSS and "
    "JavaScript. Use no external dependencies, network resources, remote fonts or images. "
    "The application must run in an ordinary modern browser."
)
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["project_name", "summary", "entrypoint", "files"],
    "properties": {
        "project_name": {"type": "string", "minLength": 1, "maxLength": 120},
        "summary": {"type": "string", "maxLength": 2000},
        "entrypoint": {"const": "index.html"},
        "files": {"type": "array", "minItems": 1, "maxItems": 1, "items": {
            "type": "object", "additionalProperties": False, "required": ["path", "content"],
            "properties": {"path": {"const": "index.html"}, "content": {"type": "string", "minLength": 1}},
        }},
    },
}
PREVIEW_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
               "img-src data:; media-src data:; font-src 'none'; connect-src 'none'; "
               "frame-src 'none'; worker-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; webrtc 'block'")
_LOCK = threading.RLock()


class _Resources(HTMLParser):
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"iframe", "frame", "object", "embed", "base"}:
            raise ValueError(f"V1 does not permit {tag} elements.")
        if tag == "meta" and attrs.get("http-equiv", "").casefold() == "refresh":
            raise ValueError("V1 does not permit meta refresh navigation.")
        for key in ("src", "href", "poster", "action", "data", "srcset", "ping"):
            value = attrs.get(key, "").strip()
            if value and not value.startswith(("#", "data:")):
                raise ValueError(f"V1 does not permit external resources: {tag} {key}.")
        if tag == "script" and attrs.get("src"):
            raise ValueError("V1 JavaScript must be inline.")


def settings():
    saved = _read_saved().get("code_lab", {})
    if not isinstance(saved, dict):
        raise ValueError("Code Lab configuration must be an object.")
    raw = os.environ.get("ARIADNE_LAB_ROOT") or saved.get("root") or r"F:\AriadneLab"
    root = Path(raw).expanduser()
    if not root.is_absolute():
        raise ValueError("ARIADNE_LAB_ROOT must be an absolute path.")
    root = root.resolve()
    storage, _ = effective_storage()
    protected = [Path(__file__).resolve().parent.parent, Path(storage["knowledge_vault"]).resolve()]
    for source in protected:
        if root == source or root.is_relative_to(source) or source.is_relative_to(root):
            raise ValueError("Code Lab root must be separate from Ariadne source and the Knowledge Vault.")
    context = int(os.environ.get("ARIADNE_CODING_CONTEXT") or saved.get("context_tokens") or 32768)
    if not 32768 <= context <= 262144:
        raise ValueError("The Lab requires at least 32K (32768) context tokens, with a maximum of 262144.")
    return root, context


def run_directory(root, run_id):
    if not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise ValueError("Invalid Code Lab run ID.")
    target = (root / run_id).resolve()
    if not target.is_relative_to(root.resolve()) or target == root.resolve():
        raise ValueError("Run directory escapes Code Lab root.")
    return target


def atomic_write(path, content):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(content, encoding="utf-8", newline="")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_project(value):
    if not isinstance(value, dict) or set(value) != set(SCHEMA["required"]):
        raise ValueError("Invalid structured project object.")
    if not isinstance(value["project_name"], str) or not 1 <= len(value["project_name"].strip()) <= 120:
        raise ValueError("Invalid project name.")
    if not isinstance(value["summary"], str) or len(value["summary"]) > 2000:
        raise ValueError("Invalid project summary.")
    files = value["files"]
    if value["entrypoint"] != "index.html" or not isinstance(files, list) or len(files) != 1:
        raise ValueError("V1 requires exactly one index.html.")
    item = files[0]
    if not isinstance(item, dict) or set(item) != {"path", "content"} or item["path"] != "index.html":
        raise ValueError("V1 permits only index.html; file paths are not accepted.")
    content = item["content"]
    if not isinstance(content, str) or not content.strip() or len(content.encode("utf-8")) > 2_000_000:
        raise ValueError("index.html must contain nonempty HTML under 2 MB.")
    if not re.search(r"<(?:!doctype\s+html|html|body|canvas|div)\b", content, re.I):
        raise ValueError("index.html is not an HTML application.")
    _Resources().feed(content)
    if re.search(r"@import\b", content, re.I):
        raise ValueError("V1 does not permit CSS imports.")
    return value


def provider_metrics(response):
    result = {key: response.get(key) for key in (
        "total_duration", "load_duration", "prompt_eval_count", "prompt_eval_duration",
        "eval_count", "eval_duration", "done_reason", "done")}
    for count, duration, name in (("prompt_eval_count", "prompt_eval_duration", "prefill_tokens_per_second"),
                                  ("eval_count", "eval_duration", "eval_tokens_per_second")):
        n, d = response.get(count), response.get(duration)
        result[name] = round(n * 1_000_000_000 / d, 2) if isinstance(n, (int, float)) and isinstance(d, (int, float)) and d > 0 else None
    return result


class CodeLab:
    def __init__(self, registry, catalog, generate, admission, activity, hardware, avatar, capabilities=None):
        self.registry, self.catalog, self.generate = registry, catalog, generate
        self.admission, self.activity, self.hardware, self.avatar = admission, activity, hardware, avatar
        self.capabilities = capabilities

    def status(self):
        root, context = settings()
        provider = self.registry.route("coding")
        model = os.environ.get("ARIADNE_CODING_MODEL") or (provider.model_id if provider else None)
        catalog = self.catalog(provider.endpoint) if provider else {"available": False}
        item = next((item for item in catalog.get("models", []) if item.get("name") == model), {})
        installed = bool(item)
        details = item.get("details") or {}
        native_context = details.get("context_length")
        return {"model": model, "root": str(root), "context_tokens": context,
                "model_details": details, "size_bytes": item.get("size"), "context_max": native_context,
                "state": "READY" if installed else "FAILED", "installed": installed,
                "message": "Ready." if installed else f"Coding model {model} is missing or Ollama is unavailable. Install it explicitly in the owned model store; no download was attempted."}

    def run(self, body, on_event=None):
        root, context = settings()
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 8000:
            return {"ok": False, "message": "Enter a coding prompt of 1–8000 characters."}, 400
        provider = self.registry.route("coding")
        model = os.environ.get("ARIADNE_CODING_MODEL") or (provider.model_id if provider else None)
        run_id = uuid.uuid4().hex
        record = {"run_id": run_id, "timestamp": datetime.now(timezone.utc).isoformat(),
                  "model": model, "context_tokens": context, "user_prompt": prompt,
                  "instructions": INSTRUCTIONS, "schema": SCHEMA, "state": "LOADING MODEL",
                  "success": False, "project_path": None, "preview": {"result": "NOT RUN", "errors": []},
                  "telemetry": provider_metrics({}), "hardware": None}
        started = time.monotonic()
        directory = run_directory(root, run_id)

        def state(name, avatar):
            record["state"] = name
            self.avatar(avatar)
            if on_event:
                on_event({"type": "state", "state": name, "run_id": run_id})

        try:
            if not provider:
                raise ValueError("No local Ollama coding provider is configured.")
            catalog = self.catalog(provider.endpoint)
            if not catalog.get("available"):
                raise ValueError("The coding Ollama provider is unavailable.")
            if model not in [item.get("name") for item in catalog.get("models", [])]:
                raise ValueError(f"Coding model {model} is missing. Install it explicitly in the owned model store; no download was attempted.")
            if self.capabilities:
                validate_context(context, self.capabilities(model))
            payload = {"model": model, "system": INSTRUCTIONS, "prompt": prompt, "format": SCHEMA,
                       "stream": True, "keep_alive": -1,
                       "options": {"num_ctx": context, "num_predict": min(8192, context // 2), "temperature": 0, "seed": 42}}
            record["request"] = payload
            response, chunks = {}, []
            state("LOADING MODEL", "loading_model")
            with self.admission(), self.activity(model):
                for chunk in self.generate(provider.endpoint, model, payload):
                    response.update(chunk)
                    if chunk.get("response"):
                        if not chunks:
                            state("GENERATING", "working")
                        chunks.append(str(chunk["response"]))
                record["hardware"] = self.hardware()
            record["telemetry"] = provider_metrics(response)
            if not response.get("done") or response.get("done_reason") in {"length", "context", "context_length"}:
                raise ValueError("Coding generation did not complete: output/context limit or incomplete provider response.")
            project = validate_project(json.loads("".join(chunks)))
            state("SAVING", "working")
            directory = run_directory(root, run_id)
            directory.mkdir(parents=True, exist_ok=False)
            directory = run_directory(root, run_id)
            atomic_write(directory / "index.html", project["files"][0]["content"])
            record.update(success=True, project_path=str(directory), project_name=project["project_name"], summary=project["summary"])
            state("READY TO RUN", "success")
            code = project["files"][0]["content"]
            status = 200
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            record.update(success=False, project_path=None, error=str(exc)[:1000])
            state("FAILED", "error")
            code, status = None, 409
        record["wall_duration_ms"] = round((time.monotonic() - started) * 1000, 1)
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        # Failures also get a reproducible record, never a successful project.
        try:
            directory = run_directory(root, run_id)
            directory.mkdir(parents=True, exist_ok=True)
            directory = run_directory(root, run_id)
            atomic_write(directory / "run.json", json.dumps(record, indent=2, ensure_ascii=False))
        except OSError as exc:
            record.update(success=False, state="FAILED", project_path=None, error=f"Cannot persist Code Lab run: {exc}")
            code, status = None, 409
        return {"ok": record["success"], "run": record, "code": code, "message": record.get("error", record.get("summary", ""))}, status


def stream_events(service, body, heartbeat_seconds=5):
    """Keep the transport alive while inference runs and saves independently."""
    events = queue.Queue()
    def work():
        try:
            result, _ = service.run(body, events.put)
        except Exception as exc:
            result = {"ok": False, "message": str(exc)}
        events.put({"type": "complete", **result})
    threading.Thread(target=work, name="lab-generation", daemon=True).start()
    while True:
        try:
            event = events.get(timeout=heartbeat_seconds)
        except queue.Empty:
            event = {"type": "heartbeat"}
        yield event
        if event.get("type") == "complete":
            return


def saved_result(run_id=None):
    root, _ = settings()
    if not run_id:
        candidates = [p for p in root.glob("*/run.json") if re.fullmatch(r"[0-9a-f]{32}", p.parent.name)]
        if not candidates:
            raise FileNotFoundError("No saved Lab run is available yet.")
        run_id = max(candidates, key=lambda p: p.stat().st_mtime_ns).parent.name
    directory, record = read_run(run_id)
    code = (directory / "index.html").read_text(encoding="utf-8") if record.get("success") else None
    return {"ok": record.get("success", False), "run": record, "code": code,
            "message": record.get("error", record.get("summary", ""))}


def read_run(run_id):
    root, _ = settings()
    directory = run_directory(root, run_id)
    return directory, json.loads((directory / "run.json").read_text(encoding="utf-8"))


def record_preview(body):
    with _LOCK:
        directory, record = read_run(str(body.get("run_id") or ""))
        if not record.get("success"):
            raise ValueError("Failed generations cannot be previewed.")
        result = body.get("result")
        if result not in {"RUNNING", "LOADED", "FAILED", "ERROR", "RESET"}:
            raise ValueError("Invalid preview result.")
        errors = body.get("errors", [])
        if not isinstance(errors, list) or len(errors) > 100 or any(not isinstance(item, str) or len(item) > 2000 for item in errors):
            raise ValueError("Invalid runtime errors.")
        preview = {"result": result, "errors": errors, "timestamp": datetime.now(timezone.utc).isoformat()}
        record.setdefault("preview_history", []).append(preview)
        record["preview"] = preview
        record["state"] = "RUNNING" if result == "RUNNING" else "COMPLETED" if result == "LOADED" and not errors else "READY TO RUN" if result == "RESET" else "FAILED"
        atomic_write(directory / "run.json", json.dumps(record, indent=2, ensure_ascii=False))
        return {"ok": True, "run": record}


def preview_html(run_id, token):
    directory, record = read_run(run_id)
    if not record.get("success") or not re.fullmatch(r"[0-9a-f]{32}", token):
        raise ValueError("Preview requires a successful run and a preview token.")
    source = (directory / "index.html").read_text(encoding="utf-8")
    bootstrap = """<script>(()=>{const token=TOKEN; const send=(kind,message='')=>parent.postMessage({type:'code-lab-preview',token,kind,message},'*');
for(const name of ['RTCPeerConnection','webkitRTCPeerConnection']){try{Object.defineProperty(window,name,{value:undefined,writable:false,configurable:false})}catch{}}
addEventListener('error',e=>send('error',`${e.message} (${e.filename}:${e.lineno}:${e.colno})`));
addEventListener('unhandledrejection',e=>send('rejection',String(e.reason)));
addEventListener('DOMContentLoaded',()=>send('loaded'));
addEventListener('click',e=>{if(e.target.closest?.('a[href],area[href]'))e.preventDefault()},true);
addEventListener('submit',e=>e.preventDefault(),true);
})();</script>""".replace("TOKEN", json.dumps(token))
    # Insert trusted CSP and bootstrap before any model markup is parsed.
    return '<!doctype html><meta http-equiv="Content-Security-Policy" content="' + PREVIEW_CSP + '">' + bootstrap + source


def validate_context(context, capabilities):
    maximum = capabilities.get("context_max")
    if not capabilities.get("available") or not isinstance(maximum, int):
        raise ValueError("Ollama could not confirm this model's context capacity.")
    if context > maximum:
        raise ValueError(f"Configured context is {context} tokens, but this model supports {maximum}. The Lab requires at least 32768.")
    if not capabilities.get("text", True):
        raise ValueError("The selected model does not support text generation.")


def prepare_working_model(config, *, catalog, capabilities, transition, preload, unload, remember, avatar, unload_candidates):
    """Temporary residency switch using the existing core's model/GPU guard.

    It never persists or changes conversational inference routing.
    """
    model, context = config["model"], config["context_tokens"]
    removed = []
    succeeded = False
    try:
        if not config.get("installed"):
            raise ValueError(config.get("message") or "The requested model is not installed.")
        validate_context(context, capabilities(model))
        with transition(model) as outcome:
            try:
                before = catalog()
                if not before.get("available"):
                    raise RuntimeError("Ollama is unavailable.")
                avatar("loading_model")
                for name in sorted(set(before.get("loaded", [])) & set(unload_candidates) - {model}):
                    if not unload(name, reason="lab_mode_switch"):
                        raise RuntimeError(f"Could not release {name}; model loading was stopped.")
                    removed.append(name)
                loaded = preload(model, options={"num_ctx": context}, keep_alive=-1, reason="lab_mode_switch")
                if not loaded.get("ok"):
                    raise RuntimeError(loaded.get("detail") or "Model loading failed.")
                verified = catalog()
                resident = next((item for item in verified.get("loaded_details", []) if item.get("name") == model), None)
                if not resident or int(resident.get("context_length") or 0) < context:
                    raise RuntimeError(f"Ollama did not confirm {model} resident with {context} context tokens.")
                remember(model)
                outcome["success"] = succeeded = True
                avatar("success")
                return {**config, "ok": True, "state": "READY", "resident": resident,
                        "load_duration": loaded.get("response", {}).get("load_duration"),
                        "message": f"{model} is loaded and ready."}, 200
            except (OSError, RuntimeError, ValueError, TypeError) as exc:
                rollback_errors = []
                if removed:
                    unload(model, reason="lab_mode_switch_rollback")
                for previous in removed:
                    previous_context = next((item.get("context_length") for item in before.get("loaded_details", []) if item.get("name") == previous), None)
                    kwargs = {"options": {"num_ctx": previous_context}} if isinstance(previous_context, int) and previous_context > 0 else {}
                    restored = preload(previous, keep_alive=-1, reason="lab_mode_switch_rollback", **kwargs)
                    if not restored.get("ok"):
                        rollback_errors.append(previous)
                if rollback_errors:
                    raise RuntimeError(f"{exc} Previous model restore also failed: {', '.join(rollback_errors)}") from exc
                raise
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        avatar("error")
        return {"ok": False, "state": "FAILED", "message": str(exc)}, 409
    finally:
        if succeeded:
            avatar("idle")
