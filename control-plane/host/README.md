# Ariadne Rust host runtime ownership

The resident Rust host owns the Windows Ollama server process when Ariadne runs
under the normal host. The Python core asks the host to reconcile it over the
local `ariadne-runtime` named pipe; the request includes the configured endpoint,
model store, and required model catalogue. Python does not launch or stop that
process on the normal hosted path.

The host verifies the required model manifests before launch, starts Ollama with
`OLLAMA_MODELS` set to the selected store, and keeps the child process handle in
a kill-on-host-close Windows Job Object. Its watchdog checks the child PID,
listener PID, API catalogue, and API version. If an Ollama update replaces the
listener, the host waits while the installer is active, then reconciles the
runtime. It also probes `/api/chat` with a deliberately missing model name, so
the route is checked without loading a model. Failed recovery attempts back off
instead of restarting the service continuously. Runtime ownership events and
failures go to `%LOCALAPPDATA%\Ariadne\host.log`.

The Rust host is the authority for local model admission and residency. Before starting or
restarting Ollama it samples WDDM GPU allocations, inspects Ollama's resident
models when its API is available, unloads idle models, and clears recognized
orphaned Ollama/llama runner processes. Large allocations owned by other
processes are left untouched and block model admission with the owning PID and
image reported in the host log.

Every Ariadne Ollama inference request obtains a short-lived host lease over the
ariadne-runtime named pipe. Python's model-activity wrapper is also a host lease,
so planner, Home, Model Lab, MCP and streaming requests receive the same gate.
The host checks /api/ps, the local model catalogue, active leases and native
DXGI/PDH memory readings before granting admission. It unloads idle conflicting
resident models before admitting another, blocks a second model while the first
is leased, and rejects admission when telemetry or headroom is insufficient.
Python unload requests are IPC commands: Rust checks active leases, performs the
unload and verifies Ollama's resident list. The Rust host's 15-minute idle
policy and 2 GiB pressure threshold govern routine release. Leases are released
after each request; stale leases expire after 15 minutes. Python holds no model
residency policy and cannot terminate Ollama on Windows unless the explicit
development override `ARIADNE_ALLOW_UNSUPERVISED_CORE=1` is set.

`ARIADNE_ALLOW_UNSUPERVISED_CORE=1` keeps the explicit development path that
starts Ollama from Python. It is not used by the normal resident-host startup.

Each Python-core startup calls `ensure_ollama`. When the host already owns a
healthy Ollama server, startup inventories GPU allocations, unloads idle
resident models, verifies the pool is clear, and reuses that server before
Python preloads Ariadne's selected Home model. If no healthy managed server is
available, the host checks for recognized orphaned runners before starting
Ollama. An active host lease blocks cleanup; failed GPU telemetry or an
unverifiable post-cleanup state blocks startup instead of guessing.
