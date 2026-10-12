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

The Python core still owns admission of inference work and the existing GPU
arbiter for model residency and renderer transitions. That arbiter decides when
the Qwen core model is loaded or released; the Rust host supervises the Ollama
server process and catalogue. These are separate responsibilities with one owner
for each. Standalone external workloads do not yet use a shared lease protocol.

`ARIADNE_ALLOW_UNSUPERVISED_CORE=1` keeps the explicit development path that
starts Ollama from Python. It is not used by the normal resident-host startup.
