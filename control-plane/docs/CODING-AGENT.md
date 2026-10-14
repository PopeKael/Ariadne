# Ariadne coding capability

Ariadne exposes the installed Qwen Code CLI as the `coding-agent` Chat capability and the `coding.execute` plugin capability. The installation remains at `F:\AI\Ariadne-Agent-Worker`; every project workspace must be a child of `F:\AriadneLab\agent-workspaces`.

Chat routes ordinary implementation requests to Qwen Code automatically. Other Ariadne workflows can call `POST /api/coding-agent/run` with:

```json
{
  "task": "Fix the failing parser and run its tests",
  "workspace": "F:\\AriadneLab\\agent-workspaces\\parser-fix",
  "model": "ariadne-qwen-code:9b-32k",
  "context_tokens": 32768,
  "constraints": ["Keep public APIs unchanged"]
}
```

The workspace, model, context window and constraints are validated and passed to the existing `Start-AriadneQwenCode.ps1` launcher. Qwen Code itself is not modified. The launcher creates an isolated per-run settings profile, disables Qwen Code's wall-clock run budget, and emits partial stream-JSON events so Ariadne can observe model/tool progress.

Before launch, Core holds its AI admission and requests a model lease from the Rust Host named-pipe custodian. The lease remains active for the entire Qwen Code process, so Host GPU/residency policy sees the coding model as in use and cannot unload it mid-task. Qwen Code runs without a wall-clock budget. Ariadne observes partial model/tool events, continuously watches the process for completion or crash, and sends Chat heartbeats. The Chat Cancel task button stops the Qwen child process tree before the lease is released. Silence alone never terminates a job; a quiet but live process retains its lease until it resumes, exits, or the user cancels. The launcher therefore observes and reuses the Host-owned loopback Ollama service; it does not need to start Ollama during a managed Ariadne task.

Results include `status`, `workspace`, `model`, `context_tokens`, `changed_files`, `tests`, elapsed time, Qwen model/tool call counts and transcript, errors, and a concise summary. Canonical run result records live under `F:\AriadneLab\agent-workspaces\.ariadne-results`; a mirror remains under `F:\AI\Ariadne-Agent-Worker\logs\qwen-code\ariadne-runs` for the benchmark harness and later diagnosis.

## Chat coding-job workspace

Chat opens the reusable coding-job view as soon as Qwen Code starts. The view reads and displays files from the assigned child workspace under `F:\AriadneLab\agent-workspaces`, streams Qwen Code output and shell/test tool results, and presents the final changed-file and test evidence. A sandboxed browser preview serves only workspace assets through `/coding-preview/<run_id>/...`; it disables network connections and uses an opaque-origin iframe. The preview bridge captures load/runtime errors and can exercise visible controls for interactive smoke evidence. Verification reports are stored with the run result. Successful jobs with an HTML entry point, no failed test reports, and a passing interactive check can be opened in the existing Rust Host-owned full-screen Edge Runner. The old 14B Lab generation loop and model switching remain retired.
