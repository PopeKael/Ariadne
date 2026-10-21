//! Rust-host ownership of Ariadne's local Ollama server process.
//!
//! Python requests reconciliation over a user-session named pipe. The Rust
//! host retains the child handle and periodically checks that the same server
//! still owns the configured endpoint.

use serde::{Deserialize, Serialize};
use std::env;
use std::ffi::c_void;
use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};
#[cfg(windows)]
use std::os::windows::process::CommandExt;

const DEFAULT_ENDPOINT: &str = "http://127.0.0.1:11434";
const DEFAULT_STORE: &str = r"F:\AI\Models\Ollama";
const PIPE_NAME: &str = r"\\.\pipe\ariadne-runtime";
const RUNTIME_PIPE_WORKERS: usize = 8;
const GPU_SAFETY_RESERVE_BYTES: u64 = 1024 * 1024 * 1024;
const UNKNOWN_GPU_OWNER_BLOCK_BYTES: u64 = GPU_SAFETY_RESERVE_BYTES;

#[derive(Debug, Deserialize)]
struct RuntimeRequest {
    v: u32,
    #[serde(rename = "type")]
    kind: String,
    endpoint: Option<String>,
    model_store: Option<String>,
    required_models: Option<Vec<String>>,
    model: Option<String>,
    context_length: Option<u32>,
    allow_calibration: Option<bool>,
    inference_kind: Option<String>,
    lease_id: Option<String>,
    force: Option<bool>,
    workload: Option<String>,
    min_free_bytes: Option<u64>,
    owner_pid: Option<u32>,
}

#[derive(Debug, Serialize)]
pub(crate) struct RuntimeResponse {
    pub(crate) v: u32,
    pub(crate) ok: bool,
    pub(crate) state: String,
    pub(crate) detail: String,
    pub(crate) pid: Option<u32>,
    pub(crate) version: Option<String>,
    pub(crate) models: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    lease_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    residency: Option<serde_json::Value>,
}

impl RuntimeResponse {
    fn error(detail: impl Into<String>) -> Self {
        Self { v: 1, ok: false, state: "error".into(), detail: detail.into(), pid: None, version: None, models: Vec::new(), lease_id: None, residency: None }
    }
}

struct ManagedRuntime {
    child: Child,
    job: isize,
    endpoint: String,
    model_store: String,
    required_models: Vec<String>,
    executable: PathBuf,
    version: Option<String>,
    num_parallel: u32,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
struct ResidencyProfile {
    digest: String,
    ollama_version: String,
    context_length: u32,
    num_parallel: u32,
    model_bytes: u64,
    gpu_bytes: u64,
    wddm_impact_bytes: u64,
}

struct InferenceLease {
    model: String,
    context_length: u32,
    last_renewed_at: Instant,
}

struct GpuLease {
    workload: String,
    model: Option<String>,
    owner_pid: Option<u32>,
    // Keep a kernel handle to the exact client process. Polling by PID alone
    // can misidentify a later process after Windows reuses the PID.
    owner_process: Option<ClientProcessHandle>,
}

struct ClientProcessHandle(isize);

impl Drop for ClientProcessHandle {
    fn drop(&mut self) {
        if self.0 != 0 {
            use windows::Win32::Foundation::{CloseHandle, HANDLE};
            unsafe { let _ = CloseHandle(HANDLE(self.0 as *mut c_void)); }
            self.0 = 0;
        }
    }
}

impl Drop for ManagedRuntime {
    fn drop(&mut self) {
        unsafe { let _ = windows::Win32::Foundation::CloseHandle(windows::Win32::Foundation::HANDLE(self.job as *mut c_void)); }
    }
}

#[derive(Default)]
struct State {
    runtime: Option<ManagedRuntime>,
    desired: Option<(String, String, Vec<String>)>,
    last_reconcile: Option<Instant>,
    reconcile_failures: u32,
    inference_leases: std::collections::HashMap<String, InferenceLease>,
    gpu_leases: std::collections::HashMap<String, GpuLease>,
    model_last_used: std::collections::HashMap<String, Instant>,
    externally_delegated: bool,
}

#[derive(Clone, Default)]
pub struct OllamaSupervisor {
    state: Arc<Mutex<State>>,
    stop: Arc<AtomicBool>,
}

impl OllamaSupervisor {
    pub fn start() -> Self {
        let supervisor = Self { state: Arc::new(Mutex::new(State::default())), stop: Arc::new(AtomicBool::new(false)) };
        let state = supervisor.state.clone();
        let stop = supervisor.stop.clone();
        thread::spawn(move || loop {
            thread::sleep(Duration::from_secs(5));
            if stop.load(Ordering::Acquire) { break; }
            let Ok(mut state) = state.lock() else { continue };
            prune_expired_leases(&mut state);
            prune_dead_gpu_leases(&mut state);
            if state.externally_delegated { continue; }
            let Some((endpoint, store, models)) = state.desired.clone() else { continue };
            let healthy = state.runtime.as_mut().is_some_and(|runtime| {
                runtime.child.try_wait().ok().flatten().is_none()
                    && runtime.endpoint == endpoint
                    && runtime.model_store == store
                    && runtime.required_models == models
                    && runtime_health(runtime).is_ok()
            });
            if !healthy {
                if installer_running().unwrap_or(true) {
                    continue;
                }
                let retry_seconds = 2u64.saturating_pow(state.reconcile_failures.min(6)).min(60);
                if state.last_reconcile.is_some_and(|last| last.elapsed() < Duration::from_secs(retry_seconds)) {
                    continue;
                }
                state.last_reconcile = Some(Instant::now());
                log_line("Ollama runtime identity or readiness changed; reconciling under Rust host ownership");
                match ensure_locked(&mut state, endpoint, store, models) {
                    Ok(_) => state.reconcile_failures = 0,
                    Err(error) => {
                        state.reconcile_failures = state.reconcile_failures.saturating_add(1);
                        log_line(&format!("Ollama runtime reconciliation deferred/failed: {error}"));
                    }
                }
            }
        });
        supervisor
    }

    fn ensure(&self, endpoint: String, model_store: String, required_models: Vec<String>) -> RuntimeResponse {
        let Ok(mut state) = self.state.lock() else { return RuntimeResponse::error("Ollama supervisor state is unavailable.") };
        if state.externally_delegated { return RuntimeResponse::error("Ariadne runtime is delegated to Dungeon; Ollama reconciliation is paused."); }
        state.last_reconcile = Some(Instant::now());
        match ensure_locked(&mut state, endpoint, model_store, required_models) {
            Ok(response) => {
                state.reconcile_failures = 0;
                response
            }
            Err(error) => {
                if !error.contains("installer is active") {
                    state.reconcile_failures = state.reconcile_failures.saturating_add(1);
                }
                log_line(&format!("Ollama ensure request failed: {error}"));
                RuntimeResponse::error(error)
            }
        }
    }

    fn admit_model(&self, endpoint: String, model: String, context_length: u32, allow_calibration: bool, inference_kind: &str) -> RuntimeResponse {
        let Ok(mut state) = self.state.lock() else { return RuntimeResponse::error("Ollama supervisor state is unavailable.") };
        if state.externally_delegated { return RuntimeResponse::error("Ariadne runtime is delegated to Dungeon; model admission is paused."); }
        prune_expired_leases(&mut state);
        prune_dead_gpu_leases(&mut state);
        if model_blocked_by_gpu_leases(&model, state.gpu_leases.values()) {
            return RuntimeResponse::error("Rust host has an exclusive GPU workload lease; model inference is blocked.");
        }
        let (pid, version, models, num_parallel) = {
            let Some(runtime) = state.runtime.as_mut() else { return RuntimeResponse::error("Rust host has not established Ollama ownership; inference is blocked.") };
            if endpoint_address(&runtime.endpoint).ok() != endpoint_address(&endpoint).ok()
                || runtime.child.try_wait().ok().flatten().is_some() {
                return RuntimeResponse::error("Ollama runtime identity is not healthy; inference is blocked pending host reconciliation.");
            }
            (runtime.child.id(), runtime.version.clone(), runtime.required_models.clone(), runtime.num_parallel)
        };
        let active_models: std::collections::HashMap<String, String> = state.inference_leases.iter()
            .map(|(id, lease)| (id.clone(), lease.model.clone())).collect();
        match preflight_model(&endpoint, &model, context_length, num_parallel, version.as_deref().unwrap_or(""), allow_calibration, inference_kind, &active_models) {
            Ok((lease_id, inventory, detail)) => {
                log_line(&detail);
                state.inference_leases.insert(lease_id.clone(), InferenceLease { model, context_length, last_renewed_at: Instant::now() });
                RuntimeResponse {
                    v: 1, ok: true, state: "admitted".into(), detail, pid: Some(pid),
                    version, models,
                    lease_id: Some(lease_id), residency: Some(inventory),
                }
            }
            Err(error) => {
                log_line(&format!("Rust-host GPU/Ollama preflight denied inference for '{model}': {error}"));
                RuntimeResponse::error(error)
            }
        }
    }

    fn release_model(&self, lease_id: &str) -> RuntimeResponse {
        let Ok(mut state) = self.state.lock() else { return RuntimeResponse::error("Ollama supervisor state is unavailable.") };
        if let Some(lease) = state.inference_leases.remove(lease_id) {
            state.model_last_used.insert(lease.model.clone(), Instant::now());
            log_line(&format!("Released Rust-host inference lease '{lease_id}' for '{}'.", lease.model));
            RuntimeResponse { v: 1, ok: true, state: "released".into(), detail: "Inference lease released.".into(), pid: None, version: None, models: Vec::new(), lease_id: Some(lease_id.into()), residency: None }
        } else {
            RuntimeResponse::error("Unknown or already released inference lease.")
        }
    }

    fn gpu_status(&self) -> RuntimeResponse {
        match super::gpu_residency::sample() {
            Ok(snapshot) => RuntimeResponse {
                v: 1, ok: true, state: "online".into(),
                detail: "GPU memory sampled by the Rust host using WDDM.".into(),
                pid: None, version: None, models: Vec::new(), lease_id: None,
                residency: serde_json::to_value(snapshot).ok(),
            },
            Err(error) => RuntimeResponse::error(format!("Rust host could not sample GPU memory: {error}")),
        }
    }

    fn ollama_residency(&self, endpoint: &str) -> RuntimeResponse {
        let Ok(mut state) = self.state.lock() else { return RuntimeResponse::error("Ollama supervisor state is unavailable.") };
        let Some(runtime) = state.runtime.as_mut() else { return RuntimeResponse::error("Rust host does not own a ready Ollama runtime.") };
        if endpoint_address(&runtime.endpoint).ok() != endpoint_address(endpoint).ok()
            || runtime.child.try_wait().ok().flatten().is_some() {
            return RuntimeResponse::error("Rust-host Ollama identity is not healthy; residency is unavailable.");
        }
        match ollama_models(endpoint) {
            Ok(rows) => {
                let names = rows.iter().filter_map(|row| row.get("name").or_else(|| row.get("model"))
                    .and_then(|name| name.as_str()).map(str::to_owned)).collect();
                RuntimeResponse { v: 1, ok: true, state: "online".into(), detail: "Ollama residency read by Rust host.".into(), pid: Some(runtime.child.id()), version: runtime.version.clone(), models: names, lease_id: None, residency: Some(serde_json::json!({"loaded": rows})) }
            }
            Err(error) => RuntimeResponse::error(error),
        }
    }

    fn acquire_gpu_lease(&self, workload: String, model: Option<String>, min_free_bytes: u64,
                         owner_pid: Option<u32>) -> RuntimeResponse {
        if !matches!(workload.as_str(), "ai" | "vault" | "renderer" | "dungeon" | "transition" | "music" | "image") {
            return RuntimeResponse::error(format!("Unknown Rust-host GPU workload '{workload}'."));
        }
        let Some(owner_pid) = owner_pid else {
            return RuntimeResponse::error("Rust Host refused a GPU lease without an explicit client PID.");
        };
        let owner_process = match open_client_process(owner_pid) {
            Ok(process) => process,
            Err(error) => return RuntimeResponse::error(error),
        };
        let Ok(mut state) = self.state.lock() else { return RuntimeResponse::error("Ollama supervisor state is unavailable.") };
        prune_expired_leases(&mut state);
        prune_dead_gpu_leases(&mut state);
        if state.externally_delegated && workload == "ai" {
            return RuntimeResponse::error("GPU admission is suspended while Ariadne's GPU is delegated to Dungeon.");
        }
        let active_workloads: Vec<String> = state.gpu_leases.values().map(|lease| lease.workload.clone()).collect();
        if gpu_workload_conflicts(&workload, state.externally_delegated, &active_workloads, state.inference_leases.len()) {
            if is_exclusive_gpu_workload(&workload) {
                return RuntimeResponse::error("Rust host has active GPU or model leases; exclusive workload admission was denied.");
            }
            return RuntimeResponse::error("Rust host has an exclusive GPU workload lease; AI admission was denied.");
        }
        let mut released_models = Vec::new();
        let mut previous_resident_models = Vec::new();
        if releases_idle_ollama_for_workload(&workload) {
            let Some(runtime) = state.runtime.as_mut() else {
                return RuntimeResponse::error("Rust host does not own an Ollama runtime; exclusive GPU workload admission is blocked.");
            };
            if endpoint_address(&runtime.endpoint).ok() != endpoint_address(DEFAULT_ENDPOINT).ok()
                || runtime.child.try_wait().ok().flatten().is_some() {
                return RuntimeResponse::error("Ollama runtime identity is not healthy; exclusive GPU workload admission is blocked.");
            }
            let ollama_version = runtime.version.clone().unwrap_or_default();
            let num_parallel = runtime.num_parallel;
            let resident = match ollama_models(DEFAULT_ENDPOINT) {
                Ok(rows) => rows,
                Err(error) => return RuntimeResponse::error(format!("Rust host could not inspect Ollama before exclusive GPU workload admission: {error}")),
            };
            previous_resident_models = resident.clone();
            for row in &resident {
                let Some(name) = row.get("name").and_then(|value| value.as_str()).map(str::to_owned) else { continue };
                if let Err(error) = unload_model(DEFAULT_ENDPOINT, &name) {
                    let recovery = restore_residency_models(DEFAULT_ENDPOINT, &previous_resident_models, num_parallel, &ollama_version, &mut state.model_last_used);
                    return RuntimeResponse::error(format!("Rust host could not release idle model '{name}' before '{workload}' admission: {error}. {recovery}"));
                }
                let remaining = match ollama_models(DEFAULT_ENDPOINT) {
                    Ok(rows) => rows,
                    Err(error) => {
                        let recovery = restore_residency_models(DEFAULT_ENDPOINT, &previous_resident_models, num_parallel, &ollama_version, &mut state.model_last_used);
                        return RuntimeResponse::error(format!("Rust host could not verify model '{name}' release: {error}. {recovery}"));
                    }
                };
                if remaining.iter().any(|item| item.get("name").and_then(|value| value.as_str()) == Some(name.as_str())) {
                    let recovery = restore_residency_models(DEFAULT_ENDPOINT, &previous_resident_models, num_parallel, &ollama_version, &mut state.model_last_used);
                    return RuntimeResponse::error(format!("Ollama did not confirm model '{name}' released before '{workload}' admission. {recovery}"));
                }
                state.model_last_used.remove(&name);
                released_models.push(name);
            }
        }
        let snapshot = match super::gpu_residency::sample() {
            Ok(snapshot) => snapshot,
            Err(error) => {
                let recovery = if releases_idle_ollama_for_workload(&workload) {
                    state.runtime.as_ref().map(|runtime| (
                        runtime.endpoint.clone(), runtime.num_parallel, runtime.version.clone().unwrap_or_default(),
                    )).map(|(endpoint, parallel, version)| restore_residency_models(
                        &endpoint, &previous_resident_models, parallel, &version, &mut state.model_last_used,
                    )).unwrap_or_else(|| "Rust Host has no Ollama runtime available for restoration.".into())
                } else { "No Ollama models were released.".into() };
                return RuntimeResponse::error(format!("Rust host cannot verify GPU memory for workload admission: {error}. {recovery}"));
            }
        };
        let required_free = GPU_SAFETY_RESERVE_BYTES.max(min_free_bytes);
        if snapshot.free_bytes < required_free {
            let recovery = if releases_idle_ollama_for_workload(&workload) {
                state.runtime.as_ref().map(|runtime| (
                    runtime.endpoint.clone(), runtime.num_parallel, runtime.version.clone().unwrap_or_default(),
                )).map(|(endpoint, parallel, version)| restore_residency_models(
                    &endpoint, &previous_resident_models, parallel, &version, &mut state.model_last_used,
                )).unwrap_or_else(|| "Rust Host has no Ollama runtime available for restoration.".into())
            } else { "No Ollama models were released.".into() };
            let restored_free = super::gpu_residency::sample().map(|gpu| gpu.free_bytes / 1_048_576);
            return RuntimeResponse::error(format!("GPU workload '{workload}' requires {} MiB free including the 1 GiB safety reserve; WDDM measured {} MiB free after idle-model release, and {} MiB after recovery. {recovery}.", required_free / 1_048_576, snapshot.free_bytes / 1_048_576, restored_free.map(|value| value.to_string()).unwrap_or_else(|_| "unavailable".into())));
        }
        let lease_id = format!("gpu-{}-{}", std::process::id(), uuid_like());
        state.gpu_leases.insert(lease_id.clone(), GpuLease {
            workload: workload.clone(), model, owner_pid: Some(owner_pid),
            owner_process: Some(owner_process),
        });
        log_line(&format!("Acquired Rust-host GPU workload lease '{}' for '{}' from client PID {:?}.", lease_id, workload, owner_pid));
        RuntimeResponse {
            v: 1, ok: true, state: "admitted".into(),
            detail: format!("Rust host admitted GPU workload '{workload}' with {} MiB free and a 1 GiB reserve; released {} idle Ollama model(s).", snapshot.free_bytes / 1_048_576, released_models.len()),
            pid: None, version: None, models: released_models, lease_id: Some(lease_id),
            residency: serde_json::to_value(snapshot).ok(),
        }
    }

    fn release_gpu_lease(&self, lease_id: &str) -> RuntimeResponse {
        let Ok(mut state) = self.state.lock() else { return RuntimeResponse::error("Ollama supervisor state is unavailable.") };
        prune_dead_gpu_leases(&mut state);
        if let Some(lease) = state.gpu_leases.remove(lease_id) {
            log_line(&format!("Released Rust-host GPU workload lease '{}' for '{}' from client PID {:?}.", lease_id, lease.workload, lease.owner_pid));
            RuntimeResponse { v: 1, ok: true, state: "released".into(), detail: "Rust-host GPU workload lease released.".into(), pid: None, version: None, models: Vec::new(), lease_id: Some(lease_id.into()), residency: None }
        } else {
            RuntimeResponse::error("Unknown or already released Rust-host GPU workload lease.")
        }
    }

    fn verify_gpu_lease_capacity(&self, lease_id: &str, min_free_bytes: u64) -> RuntimeResponse {
        let Ok(mut state) = self.state.lock() else { return RuntimeResponse::error("Ollama supervisor state is unavailable.") };
        prune_dead_gpu_leases(&mut state);
        if !state.gpu_leases.contains_key(lease_id) {
            return RuntimeResponse::error("GPU capacity check requires a live Rust-host workload lease.");
        }
        drop(state);
        let snapshot = match super::gpu_residency::sample() {
            Ok(snapshot) => snapshot,
            Err(error) => return RuntimeResponse::error(format!("Rust host cannot verify GPU memory: {error}")),
        };
        let required_free = GPU_SAFETY_RESERVE_BYTES.max(min_free_bytes);
        if snapshot.free_bytes < required_free {
            return RuntimeResponse::error(format!("GPU workload requires {} MiB free including the 1 GiB safety reserve; WDDM reports {} MiB free.", required_free / 1_048_576, snapshot.free_bytes / 1_048_576));
        }
        RuntimeResponse { v: 1, ok: true, state: "verified".into(), detail: format!("Rust host verified {} MiB free for the active workload lease.", snapshot.free_bytes / 1_048_576), pid: None, version: None, models: Vec::new(), lease_id: Some(lease_id.into()), residency: serde_json::to_value(snapshot).ok() }
    }

    fn renew_model_lease(&self, lease_id: &str) -> RuntimeResponse {
        let (endpoint, model, expected_context) = {
            let Ok(mut state) = self.state.lock() else { return RuntimeResponse::error("Ollama supervisor state is unavailable.") };
            prune_expired_leases(&mut state);
            let Some(lease) = state.inference_leases.get(lease_id) else {
                return RuntimeResponse::error("Unknown or expired inference lease.");
            };
            let Some(runtime) = state.runtime.as_ref() else {
                return RuntimeResponse::error("Rust host lost Ollama ownership while the model lease was active.");
            };
            (runtime.endpoint.clone(), lease.model.clone(), lease.context_length)
        };
        // Direct clients can reach Ollama outside Ariadne's Python lease
        // wrappers. Check the actual resident context on every heartbeat.
        let rows = match ollama_models(&endpoint) {
            Ok(rows) => rows,
            Err(error) => return RuntimeResponse::error(format!("Could not validate active Ollama residency before lease renewal: {error}")),
        };
        let actual_context = rows.iter()
            .find(|row| row.get("name").and_then(|value| value.as_str()) == Some(model.as_str()))
            .and_then(|row| row.get("context_length").and_then(|value| value.as_u64()))
            .unwrap_or(0) as u32;
        if actual_context != expected_context {
            let detail = format!("Rust Host refused to renew '{model}' lease: Ollama reports context {actual_context}, but the admitted profile requires {expected_context}. An out-of-band model request or residency change is suspected.");
            log_line(&detail);
            return RuntimeResponse::error(detail);
        }
        let Ok(mut state) = self.state.lock() else { return RuntimeResponse::error("Ollama supervisor state is unavailable.") };
        prune_expired_leases(&mut state);
        if let Some(lease) = state.inference_leases.get_mut(lease_id) {
            if lease.model != model || lease.context_length != expected_context {
                return RuntimeResponse::error("Inference lease profile changed during residency validation.");
            }
            lease.last_renewed_at = Instant::now();
            log_line(&format!("Renewed Rust-host model lease '{}' for '{}' at context {} after /api/ps validation.", lease_id, model, actual_context));
            RuntimeResponse { v: 1, ok: true, state: "renewed".into(), detail: format!("Rust Host verified '{model}' residency at context {actual_context} before renewing its lease."), pid: None, version: None, models: Vec::new(), lease_id: Some(lease_id.into()), residency: None }
        } else {
            RuntimeResponse::error("Inference lease expired during residency validation.")
        }
    }
    fn suspend_for_external_workload(&self, endpoint: String) -> RuntimeResponse {
        let Ok(mut state) = self.state.lock() else { return RuntimeResponse::error("Ollama supervisor state is unavailable.") };
        prune_expired_leases(&mut state);
        if !state.inference_leases.is_empty() {
            return RuntimeResponse::error("Ariadne inference leases are still active; Dungeon handoff was refused.");
        }
        if let Some(runtime) = state.runtime.as_mut() {
            if endpoint_address(&runtime.endpoint).ok() != endpoint_address(&endpoint).ok() {
                return RuntimeResponse::error("Configured Ollama endpoint does not match the Rust-owned runtime.");
            }
            let models = match ollama_models(&endpoint) {
                Ok(rows) => rows,
                Err(error) => return RuntimeResponse::error(error),
            };
            let names: Vec<String> = models.iter().filter_map(|row| row.get("name").and_then(|value| value.as_str()).map(str::to_owned)).collect();
            for model in &names {
                if let Err(error) = unload_model(&endpoint, model) { return RuntimeResponse::error(error); }
            }
            match ollama_models(&endpoint) {
                Ok(rows) if rows.is_empty() => {},
                Ok(rows) => return RuntimeResponse::error(format!("Ollama still reports resident models after release: {}", rows.iter().filter_map(|row| row.get("name").and_then(|value| value.as_str())).collect::<Vec<_>>().join(", "))),
                Err(error) => return RuntimeResponse::error(error),
            }
            state.externally_delegated = true;
            if let Some(mut runtime) = state.runtime.take() {
                let _ = runtime.child.kill();
                let _ = runtime.child.wait();
                drop(runtime);
            }
            let gpu = match super::gpu_residency::sample() {
                Ok(snapshot) => snapshot,
                Err(error) => return RuntimeResponse::error(format!("GPU memory could not be verified after stopping Ariadne Ollama: {error}")),
            };
            let remaining_runners: Vec<String> = gpu.process_allocations.iter().filter_map(|allocation| {
                let image = process_image_name(allocation.pid)?;
                let name = image.file_name()?.to_string_lossy().to_ascii_lowercase();
                (is_known_model_runner(&name) && allocation.bytes > 0).then(|| format!("PID {} {} MiB", allocation.pid, allocation.bytes / 1_048_576))
            }).collect();
            if !remaining_runners.is_empty() {
                return RuntimeResponse::error(format!("Ariadne model-runner GPU allocations remain after runtime shutdown: {}", remaining_runners.join(", ")));
            }
            log_line("Ariadne Ollama runtime stopped after verified model release; reconciliation paused for Dungeon handoff.");
            RuntimeResponse { v: 1, ok: true, state: "suspended".into(), detail: format!("Ariadne models and service were released; verified {} MiB GPU use remains across the machine.", gpu.used_bytes / 1_048_576), pid: None, version: None, models: names, lease_id: None, residency: serde_json::to_value(gpu).ok() }
        } else {
            RuntimeResponse::error("Rust host has no managed Ollama runtime to suspend.")
        }
    }

    fn resume_after_external_workload(&self) -> RuntimeResponse {
        let Ok(mut state) = self.state.lock() else { return RuntimeResponse::error("Ollama supervisor state is unavailable.") };
        state.externally_delegated = false;
        let Some((endpoint, store, models)) = state.desired.clone() else {
            state.externally_delegated = true;
            return RuntimeResponse::error("Ariadne Ollama configuration is unavailable for restoration.");
        };
        match ensure_locked(&mut state, endpoint, store, models) {
            Ok(response) => response,
            Err(error) => {
                state.externally_delegated = true;
                RuntimeResponse::error(format!("Ariadne runtime restoration failed: {error}"))
            },
        }
    }

    pub fn resume_external_workload(&self) -> RuntimeResponse { self.resume_after_external_workload() }

    fn unload_model(&self, endpoint: String, model: String) -> RuntimeResponse {
        let Ok(mut state) = self.state.lock() else { return RuntimeResponse::error("Ollama supervisor state is unavailable.") };
        prune_expired_leases(&mut state);
        prune_dead_gpu_leases(&mut state);
        if state.inference_leases.values().any(|lease| lease.model == model) {
            return RuntimeResponse::error(format!("Cannot unload '{model}': Rust host has an active inference lease for it."));
        }
        if state.gpu_leases.values().any(|lease| lease.workload == "ai"
            || (lease.workload == "vault" && lease.model.as_deref() == Some(model.as_str()))) {
            return RuntimeResponse::error(format!("Cannot unload '{model}': Rust host has an active GPU workload lease."));
        }
        let Some(runtime) = state.runtime.as_mut() else { return RuntimeResponse::error("Rust host does not own an Ollama runtime; unload blocked.") };
        if endpoint_address(&runtime.endpoint).ok() != endpoint_address(&endpoint).ok()
            || runtime.child.try_wait().ok().flatten().is_some() {
            return RuntimeResponse::error("Ollama runtime identity is not healthy; unload blocked pending host reconciliation.");
        }
        match unload_model(&endpoint, &model).and_then(|_| {
            let deadline = Instant::now() + Duration::from_secs(10);
            loop {
                let resident = ollama_models(&endpoint)?;
                if !resident.iter().any(|row| row.get("name").and_then(|v| v.as_str()) == Some(model.as_str())) { break; }
                if Instant::now() >= deadline { return Err(format!("Ollama did not confirm '{model}' unloaded.")); }
                thread::sleep(Duration::from_millis(250));
            }
            let gpu = super::gpu_residency::sample().map_err(|error| format!("Model unload succeeded but GPU state could not be verified: {error}"))?;
            Ok(gpu)
        }) {
            Ok(gpu) => {
                state.model_last_used.remove(&model);
                let residency = serde_json::to_value(gpu).ok();
                RuntimeResponse { v: 1, ok: true, state: "unloaded".into(), detail: format!("Rust host unloaded '{model}' and verified Ollama residency is clear."), pid: None, version: None, models: Vec::new(), lease_id: None, residency }
            }
            Err(error) => RuntimeResponse::error(error),
        }
    }

    fn release_idle_models(&self, endpoint: String, force: bool) -> RuntimeResponse {
        let Ok(mut state) = self.state.lock() else { return RuntimeResponse::error("Ollama supervisor state is unavailable.") };
        prune_expired_leases(&mut state);
        prune_dead_gpu_leases(&mut state);
        let Some(runtime) = state.runtime.as_mut() else { return RuntimeResponse::error("Rust host does not own an Ollama runtime; model release blocked.") };
        if endpoint_address(&runtime.endpoint).ok() != endpoint_address(&endpoint).ok()
            || runtime.child.try_wait().ok().flatten().is_some() {
            return RuntimeResponse::error("Ollama runtime identity is not healthy; model release blocked pending host reconciliation.");
        }
        let active: std::collections::HashSet<String> = state.inference_leases.values().map(|lease| lease.model.clone()).collect();
        if state.gpu_leases.values().any(|lease| lease.workload == "ai") {
            return RuntimeResponse::error("Rust host has an active Ariadne GPU workload lease; idle model release is deferred.");
        }
        let gpu = match super::gpu_residency::sample() {
            Ok(snapshot) => snapshot,
            Err(error) => return RuntimeResponse::error(format!("Rust host cannot inspect GPU residency before model release: {error}")),
        };
        let memory_pressure = gpu.free_bytes < 2 * 1024 * 1024 * 1024;
        let resident = match ollama_models(&endpoint) { Ok(rows) => rows, Err(error) => return RuntimeResponse::error(error) };
        let now = Instant::now();
        let mut unloaded = Vec::new();
        let mut blocked = Vec::new();
        for row in resident {
            let Some(model) = row.get("name").and_then(|v| v.as_str()).map(str::to_owned) else { continue };
            if active.contains(&model) { blocked.push(model); continue; }
            let idle = state.model_last_used.get(&model).map(|time| now.duration_since(*time)).unwrap_or(Duration::from_secs(900));
            if force || memory_pressure || idle >= Duration::from_secs(900) {
                match unload_model(&endpoint, &model) {
                    Ok(()) => {
                        let remaining = match ollama_models(&endpoint) {
                            Ok(rows) => rows,
                            Err(error) => return RuntimeResponse::error(format!("Could not verify unload of '{model}': {error}")),
                        };
                        if remaining.iter().any(|row| row.get("name").and_then(|v| v.as_str()) == Some(model.as_str())) {
                            return RuntimeResponse::error(format!("Ollama did not confirm '{model}' unloaded."));
                        }
                        state.model_last_used.remove(&model);
                        unloaded.push(model);
                    }
                    Err(error) => return RuntimeResponse::error(error),
                }
            }
        }
        let detail = format!("Rust host released {} idle model(s); protected active model(s): {}.", unloaded.len(), blocked.join(", "));
        log_line(&detail);
        RuntimeResponse { v: 1, ok: true, state: "released".into(), detail, pid: None, version: None, models: unloaded, lease_id: None, residency: None }
    }

    pub fn stop(&self) {
        self.stop.store(true, Ordering::Release);
        if let Ok(mut state) = self.state.lock() {
            state.desired = None;
            if let Some(mut runtime) = state.runtime.take() {
                let _ = runtime.child.kill();
                let _ = runtime.child.wait();
                drop(runtime); // Closing its kill-on-close Job Object also stops child runners.
                log_line("Rust host stopped its Ollama child during shutdown");
            }
        }
    }
}

pub fn pipe_receiver(supervisor: OllamaSupervisor, stop: Arc<std::sync::atomic::AtomicBool>) -> thread::JoinHandle<()> {
    thread::spawn(move || {
        let workers: Vec<_> = (0..RUNTIME_PIPE_WORKERS).map(|_| {
            let supervisor = supervisor.clone();
            let stop = stop.clone();
            thread::spawn(move || {
                while !stop.load(std::sync::atomic::Ordering::Acquire) {
                    match serve_one(&supervisor) {
                        Ok(()) => {}
                        Err(error) => {
                            if !stop.load(std::sync::atomic::Ordering::Acquire) {
                                log_line(&format!("runtime IPC request failed: {error}"));
                            }
                            thread::sleep(Duration::from_millis(250));
                        }
                    }
                }
            })
        }).collect();
        while !stop.load(std::sync::atomic::Ordering::Acquire) {
            thread::sleep(Duration::from_millis(50));
        }
        for worker in workers { let _ = worker.join(); }
    })
}

fn serve_one(supervisor: &OllamaSupervisor) -> Result<(), String> {
    use windows::Win32::Foundation::{CloseHandle, ERROR_PIPE_CONNECTED, HLOCAL, LocalFree};
    use windows::Win32::Storage::FileSystem::{ReadFile, WriteFile};
    use windows::Win32::Storage::FileSystem::PIPE_ACCESS_DUPLEX;
    use windows::Win32::System::Pipes::{ConnectNamedPipe, CreateNamedPipeW, PIPE_READMODE_BYTE, PIPE_REJECT_REMOTE_CLIENTS, PIPE_TYPE_BYTE, PIPE_WAIT};
    use windows::Win32::Security::Authorization::ConvertStringSecurityDescriptorToSecurityDescriptorW;
    use windows::Win32::Security::{PSECURITY_DESCRIPTOR, SECURITY_ATTRIBUTES};
    use windows::core::{PCWSTR, BOOL};
    let wide: Vec<u16> = PIPE_NAME.encode_utf16().chain(std::iter::once(0)).collect();
    let pipe_name = PCWSTR(wide.as_ptr());
    let sddl: Vec<u16> = "D:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GA;;;OW)".encode_utf16().chain(std::iter::once(0)).collect();
    unsafe {
        let mut descriptor = PSECURITY_DESCRIPTOR::default();
        ConvertStringSecurityDescriptorToSecurityDescriptorW(PCWSTR(sddl.as_ptr()), 1, &mut descriptor, None)
            .map_err(|error| format!("could not create runtime IPC security descriptor: {error}"))?;
        let attributes = SECURITY_ATTRIBUTES {
            nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: descriptor.0,
            bInheritHandle: BOOL(0),
        };
        let pipe = CreateNamedPipeW(pipe_name, PIPE_ACCESS_DUPLEX, PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS, RUNTIME_PIPE_WORKERS as u32, 4096, 4096, 0, Some(&attributes));
        let _ = LocalFree(Some(HLOCAL(descriptor.0)));
        if pipe.is_invalid() { return Err("could not create runtime named pipe".into()); }
        let connected = ConnectNamedPipe(pipe, None).is_ok() || windows::Win32::Foundation::GetLastError() == ERROR_PIPE_CONNECTED;
        if !connected { let _ = CloseHandle(pipe); return Err("could not accept runtime IPC client".into()); }
        let mut request_bytes = Vec::new();
        loop {
            let mut buffer = [0u8; 1024];
            let mut read = 0u32;
            if ReadFile(pipe, Some(&mut buffer), Some(&mut read as *mut u32), None).is_err() || read == 0 { break; }
            if let Some(end) = buffer[..read as usize].iter().position(|byte| *byte == b'\n') {
                request_bytes.extend_from_slice(&buffer[..end]);
                break;
            }
            request_bytes.extend_from_slice(&buffer[..read as usize]);
            if request_bytes.len() > 16 * 1024 { break; }
        }
        let parsed = serde_json::from_slice::<RuntimeRequest>(&request_bytes);
        if let Ok(request) = &parsed {
            log_line(&format!("Runtime IPC request received: v={}, type={}", request.v, request.kind));
        }
        let response = match parsed {
            Ok(request) if request.v == 1 && request.kind == "ensure_ollama" => supervisor.ensure(
                request.endpoint.unwrap_or_else(|| DEFAULT_ENDPOINT.into()),
                request.model_store.unwrap_or_else(|| env::var("OLLAMA_MODELS").unwrap_or_else(|_| DEFAULT_STORE.into())),
                request.required_models.unwrap_or_default(),
            ),
            Ok(request) if request.v == 1 && request.kind == "admit_model" => supervisor.admit_model(
                request.endpoint.unwrap_or_else(|| DEFAULT_ENDPOINT.into()),
                request.model.unwrap_or_default(),
                // Missing context is not a safe reason to silently load the
                // Ollama default (commonly 4K). Zero is rejected by preflight.
                request.context_length.unwrap_or(0),
                request.allow_calibration.unwrap_or(false),
                request.inference_kind.as_deref().unwrap_or("generate"),
            ),
            Ok(request) if request.v == 1 && request.kind == "release_model" => supervisor.release_model(
                request.lease_id.as_deref().unwrap_or_default(),
            ),
            Ok(request) if request.v == 1 && request.kind == "gpu_status" => supervisor.gpu_status(),
            Ok(request) if request.v == 1 && request.kind == "ollama_residency" => supervisor.ollama_residency(
                request.endpoint.as_deref().unwrap_or(DEFAULT_ENDPOINT),
            ),
            Ok(request) if request.v == 1 && request.kind == "acquire_gpu_lease" => supervisor.acquire_gpu_lease(
                request.workload.unwrap_or_default(), request.model,
                request.min_free_bytes.unwrap_or(0), request.owner_pid,
            ),
            Ok(request) if request.v == 1 && request.kind == "release_gpu_lease" => supervisor.release_gpu_lease(
                request.lease_id.as_deref().unwrap_or_default(),
            ),
            Ok(request) if request.v == 1 && request.kind == "verify_gpu_lease_capacity" => supervisor.verify_gpu_lease_capacity(
                request.lease_id.as_deref().unwrap_or_default(), request.min_free_bytes.unwrap_or(0),
            ),
            Ok(request) if request.v == 1 && request.kind == "renew_model_lease" => supervisor.renew_model_lease(
                request.lease_id.as_deref().unwrap_or_default(),
            ),
            Ok(request) if request.v == 1 && request.kind == "unload_model" => supervisor.unload_model(
                request.endpoint.unwrap_or_else(|| DEFAULT_ENDPOINT.into()),
                request.model.unwrap_or_default(),
            ),
            Ok(request) if request.v == 1 && request.kind == "release_idle_models" => supervisor.release_idle_models(
                request.endpoint.unwrap_or_else(|| DEFAULT_ENDPOINT.into()),
                request.force.unwrap_or(false),
            ),
            Ok(request) if request.v == 1 && request.kind == "suspend_for_external_workload" => supervisor.suspend_for_external_workload(
                request.endpoint.unwrap_or_else(|| DEFAULT_ENDPOINT.into()),
            ),
            Ok(request) if request.v == 1 && request.kind == "resume_after_external_workload" => supervisor.resume_after_external_workload(),
            Ok(request) => {
                log_line(&format!("Runtime IPC request rejected: v={}, type={}", request.v, request.kind));
                RuntimeResponse::error("Unsupported runtime IPC request.")
            }
            Err(error) => {
                log_line(&format!("Malformed runtime IPC request: {error}"));
                RuntimeResponse::error("Malformed runtime IPC request.")
            }
        };
        log_line(&format!("Runtime IPC response: state={}, ok={}, detail={}", response.state, response.ok, response.detail));
        let mut bytes = serde_json::to_vec(&response).map_err(|error| error.to_string())?;
        bytes.push(b'\n');
        let mut written = 0u32;
        let _ = WriteFile(pipe, Some(&bytes), Some(&mut written as *mut u32), None);
        let _ = windows::Win32::Storage::FileSystem::FlushFileBuffers(pipe);
        let _ = windows::Win32::System::Pipes::DisconnectNamedPipe(pipe);
        let _ = CloseHandle(pipe);
    }
    Ok(())
}

pub fn wake_pipe_receiver() {
    use windows::Win32::Foundation::{CloseHandle, GENERIC_WRITE};
    use windows::Win32::Storage::FileSystem::{CreateFileW, ReadFile, WriteFile, FILE_ATTRIBUTE_NORMAL, FILE_SHARE_MODE, OPEN_EXISTING};
    use windows::core::PCWSTR;
    let wide: Vec<u16> = PIPE_NAME.encode_utf16().chain(std::iter::once(0)).collect();
    let mut awakened = 0;
    for _ in 0..(RUNTIME_PIPE_WORKERS * 4) {
        unsafe {
            if let Ok(handle) = CreateFileW(PCWSTR(wide.as_ptr()), GENERIC_WRITE.0, FILE_SHARE_MODE(0), None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None) {
                let mut written = 0u32;
                let _ = WriteFile(handle, Some(b"{}\n"), Some(&mut written as *mut u32), None);
                let mut response = [0u8; 1024];
                let mut read = 0u32;
                let _ = ReadFile(handle, Some(&mut response), Some(&mut read as *mut u32), None);
                let _ = CloseHandle(handle);
                awakened += 1;
                if awakened >= RUNTIME_PIPE_WORKERS { return; }
            }
        }
        thread::sleep(Duration::from_millis(50));
    }
}

fn ensure_locked(state: &mut State, endpoint: String, store: String, models: Vec<String>) -> Result<RuntimeResponse, String> {
    prune_expired_leases(state);
    let existing_runtime_healthy = if let Some(runtime) = state.runtime.as_mut() {
        if runtime.child.try_wait().map_err(|error| error.to_string())?.is_none()
            && runtime.endpoint == endpoint && runtime.model_store == store && runtime.required_models == models
        {
            runtime_health(runtime).ok()
        } else { None }
    } else { None };
    // Dungeon's lease intentionally spans the runtime suspension; ignore that
    // one while restoring Ollama, but keep it exclusive for every AI admission.
    let has_active_gpu_workload = state.gpu_leases.values().any(|lease| lease.workload != "dungeon");
    match reconciliation_plan(existing_runtime_healthy.is_some(), !state.inference_leases.is_empty() || has_active_gpu_workload) {
        ReconciliationPlan::ReuseAndClearIdleModels => {
            clear_idle_resident_models(&endpoint, &state.inference_leases)?;
            let response = existing_runtime_healthy.expect("reuse plan requires a healthy runtime");
            log_line(&format!("Ariadne startup reused the healthy Rust-owned Ollama runtime after idle model-pool cleanup; state='{}'.", response.state));
            return Ok(response);
        }
        ReconciliationPlan::ReusePreservingActiveLeases => {
            let response = existing_runtime_healthy.expect("reuse plan requires a healthy runtime");
            log_line(&format!(
                "Ariadne core startup reused the healthy Rust-owned Ollama runtime and preserved active GPU/model leases: inference={:?}, workloads={:?}.",
                state.inference_leases.values().map(|lease| lease.model.as_str()).collect::<Vec<_>>()
                , state.gpu_leases.values().map(|lease| lease.workload.as_str()).collect::<Vec<_>>()
            ));
            return Ok(response);
        }
        ReconciliationPlan::WaitForActiveLeases => {
            return Err(format!("Ollama reconciliation is waiting for active Rust-host inference lease(s): {:?}.", state.inference_leases.values().map(|lease| lease.model.as_str()).collect::<Vec<_>>()));
        }
        ReconciliationPlan::ReplaceRuntime => {}
    }
    if installer_running()? {
        return Err("Ollama update installer is active; reconciliation is waiting for it to finish.".into());
    }
    prepare_existing_ollama_for_restart(&endpoint, &state.inference_leases)?;
    state.runtime = None;
    stop_existing_ollama()?;
    let model_store = PathBuf::from(&store);
    if !model_store.is_dir() { return Err(format!("Ariadne model store is unavailable: {}", model_store.display())); }
    for model in &models {
        let (name, tag) = model.split_once(':').unwrap_or((model.as_str(), "latest"));
        let manifest = model_store.join("manifests/registry.ollama.ai/library").join(name).join(tag);
        if !manifest.is_file() { return Err(format!("Required Ollama model manifest is missing: {model}")); }
    }
    let executable = find_ollama_executable()?;
    let num_parallel = effective_num_parallel()?;
    let mut command = Command::new(&executable);
    command.arg("serve").env("OLLAMA_MODELS", &store).env("OLLAMA_NUM_PARALLEL", num_parallel.to_string()).current_dir(executable.parent().unwrap_or(Path::new(".")))
        .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    #[cfg(windows)] { use std::os::windows::process::CommandExt; command.creation_flags(0x0800_0000); }
    let child = command.spawn().map_err(|error| format!("Rust host could not start {}: {error}", executable.display()))?;
    let pid = child.id();
    let job = match super::create_core_job(&child) {
        Ok(job) => job,
        Err(error) => {
            let mut child = child;
            let _ = child.kill();
            let _ = child.wait();
            return Err(format!("Ollama process ownership could not be established: {error}"));
        }
    };
    log_line(&format!("Rust host launched Ollama: pid={pid}, executable={}, store={store}, effective OLLAMA_NUM_PARALLEL={num_parallel}", executable.display()));
    state.runtime = Some(ManagedRuntime { child, job: job.0 as isize, endpoint: endpoint.clone(), model_store: store.clone(), required_models: models.clone(), executable, version: None, num_parallel });
    state.desired = Some((endpoint, store, models));
    let runtime = state.runtime.as_mut().expect("runtime was just created");
    let deadline = Instant::now() + Duration::from_secs(45);
    loop {
        if let Some(code) = runtime.child.try_wait().map_err(|error| error.to_string())? {
            state.runtime = None;
            return Err(format!("Rust-owned Ollama exited during startup: {code}"));
        }
        if let Ok(response) = runtime_health(runtime) { return Ok(response); }
        if Instant::now() >= deadline { return Err("Rust-owned Ollama did not expose its required model catalogue within 45 seconds.".into()); }
        thread::sleep(Duration::from_millis(250));
    }
}

#[derive(Debug, PartialEq, Eq)]
enum ReconciliationPlan {
    ReuseAndClearIdleModels,
    ReusePreservingActiveLeases,
    WaitForActiveLeases,
    ReplaceRuntime,
}

fn reconciliation_plan(runtime_healthy: bool, has_active_leases: bool) -> ReconciliationPlan {
    match (runtime_healthy, has_active_leases) {
        (true, false) => ReconciliationPlan::ReuseAndClearIdleModels,
        (true, true) => ReconciliationPlan::ReusePreservingActiveLeases,
        (false, true) => ReconciliationPlan::WaitForActiveLeases,
        (false, false) => ReconciliationPlan::ReplaceRuntime,
    }
}

#[cfg(test)]
mod reconciliation_plan_tests {
    use super::{has_model_capacity, reconciliation_plan, ReconciliationPlan};

    #[test]
    fn model_admission_uses_available_capacity_instead_of_external_process_identity() {
        let model_bytes = 6_288 * 1024 * 1024;
        let required_bytes = model_bytes + 1024 * 1024 * 1024;
        let free_after_other_processes = 12_800 * 1024 * 1024;

        assert!(has_model_capacity(free_after_other_processes, required_bytes));
        assert!(!has_model_capacity(required_bytes - 1, required_bytes));
    }

    #[test]
    fn healthy_runtime_is_reused_without_unloading_models_held_by_live_leases() {
        assert_eq!(
            reconciliation_plan(true, true),
            ReconciliationPlan::ReusePreservingActiveLeases
        );
    }

    #[test]
    fn healthy_runtime_clears_idle_pool_when_no_leases_exist() {
        assert_eq!(
            reconciliation_plan(true, false),
            ReconciliationPlan::ReuseAndClearIdleModels
        );
    }

    #[test]
    fn unhealthy_runtime_waits_for_active_leases_before_replacement() {
        assert_eq!(
            reconciliation_plan(false, true),
            ReconciliationPlan::WaitForActiveLeases
        );
    }

    #[test]
    fn unhealthy_runtime_is_reconciled_when_no_leases_exist() {
        assert_eq!(
            reconciliation_plan(false, false),
            ReconciliationPlan::ReplaceRuntime
        );
    }
}

fn clear_idle_resident_models(endpoint: &str, leases: &std::collections::HashMap<String, InferenceLease>) -> Result<(), String> {
    let before = super::gpu_residency::sample()
        .map_err(|error| format!("Cannot inspect GPU allocations at Ariadne startup; model pool cleanup blocked: {error}"))?;
    let mut resident = ollama_models(endpoint)
        .map_err(|error| format!("Cannot inspect Ollama model residency at Ariadne startup: {error}"))?;
    log_line(&format!("Ariadne startup residency preflight: GPU {} MiB used / {} MiB total; allocations [{}]; resident models [{}].", before.used_bytes / 1_048_576, before.total_bytes / 1_048_576, allocation_summary(&before), resident.iter().filter_map(|row| row.get("name").and_then(|v| v.as_str())).collect::<Vec<_>>().join(", ")));
    if !resident.is_empty() && !leases.is_empty() {
        return Err(format!("Ariadne startup found active Rust-host model leases {:?}; waiting instead of unloading their models.", leases.values().map(|lease| lease.model.as_str()).collect::<Vec<_>>()));
    }
    for row in &resident {
        if let Some(name) = row.get("name").and_then(|value| value.as_str()) { unload_model(endpoint, name)?; }
    }
    let deadline = Instant::now() + Duration::from_secs(20);
    while !resident.is_empty() {
        if Instant::now() >= deadline {
            return Err("Ollama did not clear its resident model pool during Ariadne startup.".into());
        }
        thread::sleep(Duration::from_millis(300));
        resident = ollama_models(endpoint)?;
    }
    let after = super::gpu_residency::sample()
        .map_err(|error| format!("GPU state could not be verified after Ariadne startup cleanup: {error}"))?;
    report_unknown_gpu_owners(&after, "Ariadne startup");
    log_line(&format!("Ariadne startup model-pool cleanup verified: Ollama has no resident models; GPU {} MiB free / {} MiB total; allocations [{}].", after.free_bytes / 1_048_576, after.total_bytes / 1_048_576, allocation_summary(&after)));
    Ok(())
}

fn prepare_existing_ollama_for_restart(endpoint: &str, leases: &std::collections::HashMap<String, InferenceLease>) -> Result<(), String> {
    let listeners = ollama_listener_pids()?;
    if listeners.is_empty() {
        clear_orphaned_model_runners()?;
        return Ok(());
    }
    for pid in &listeners {
        let image = process_image_name(*pid).ok_or_else(|| format!("Could not inspect GPU/Ollama owner PID {pid}; will not restart the service blindly."))?;
        if !image.file_name().is_some_and(|name| name.to_string_lossy().eq_ignore_ascii_case("ollama.exe")) {
            return Err(format!("Port 11434 belongs to {}, not Ollama; leaving it untouched.", image.display()));
        }
    }
    let before = super::gpu_residency::sample().map_err(|error| format!("Cannot verify GPU allocations before Ollama reconciliation; restart blocked: {error}"))?;
    let mut resident = ollama_models(endpoint).map_err(|error| format!("Ollama is listening but its resident-model inventory failed; restart blocked: {error}"))?;
    log_line(&format!("Pre-restart residency inspection: GPU {} MiB used / {} MiB total across {:?}; allocations [{}]; Ollama models: {}", before.used_bytes / 1_048_576, before.total_bytes / 1_048_576, before.adapters, allocation_summary(&before), resident.iter().filter_map(|row| row.get("name").and_then(|v| v.as_str())).collect::<Vec<_>>().join(", ")));
    if !resident.is_empty() && !leases.is_empty() {
        return Err(format!("Ollama reconciliation found active inference lease(s) {:?}; waiting instead of unloading or restarting.", leases.values().map(|lease| lease.model.as_str()).collect::<Vec<_>>()));
    }
    for row in &resident {
        if let Some(name) = row.get("name").and_then(|value| value.as_str()) { unload_model(endpoint, name)?; }
    }
    let deadline = Instant::now() + Duration::from_secs(20);
    while !resident.is_empty() {
        if Instant::now() >= deadline {
            return Err("Ollama did not release resident models before reconciliation; service left running.".into());
        }
        thread::sleep(Duration::from_millis(400));
        resident = ollama_models(endpoint)?;
    }
    let after = super::gpu_residency::sample().map_err(|error| format!("GPU allocation state could not be rechecked after unloading models; restart blocked: {error}"))?;
    log_line(&format!("Pre-restart unload verified: GPU {} MiB used / {} MiB total; Ollama reports no resident models.", after.used_bytes / 1_048_576, after.total_bytes / 1_048_576));
    report_unknown_gpu_owners(&after, "Ollama restart");
    Ok(())
}

fn clear_orphaned_model_runners() -> Result<(), String> {
    let initial = super::gpu_residency::sample()
        .map_err(|error| format!("GPU startup inventory unavailable; refusing to start or load a model: {error}"))?;
    log_line(&format!("Startup GPU inventory with Ollama offline: {} MiB used / {} MiB total; allocations [{}].", initial.used_bytes / 1_048_576, initial.total_bytes / 1_048_576, allocation_summary(&initial)));
    let runner_pids: Vec<u32> = initial.process_allocations.iter().filter_map(|allocation| {
        let image = process_image_name(allocation.pid)?;
        let name = image.file_name()?.to_string_lossy().to_ascii_lowercase();
        is_known_model_runner(&name).then_some(allocation.pid)
    }).collect();
    for pid in runner_pids {
        let image = process_image_name(pid).ok_or_else(|| format!("Could not re-verify model-runner PID {pid}; leaving it untouched and blocking model startup."))?;
        let name = image.file_name().unwrap_or_default().to_string_lossy().to_ascii_lowercase();
        if !is_known_model_runner(&name) {
            return Err(format!("GPU owner PID {pid} changed identity during startup; leaving it untouched."));
        }
        let stopped = Command::new("taskkill.exe").args(["/PID", &pid.to_string(), "/T", "/F"])
            .creation_flags(0x0800_0000).output()
            .map_err(|error| format!("Could not release orphaned model-runner PID {pid}: {error}"))?;
        if !stopped.status.success() {
            return Err(format!("Could not stop orphaned model-runner PID {pid} ({}); refusing model startup.", image.display()));
        }
        log_line(&format!("Stopped orphaned GPU model runner PID {pid} ({}) during Rust-host startup.", image.display()));
    }
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        let snapshot = super::gpu_residency::sample()
            .map_err(|error| format!("GPU state could not be verified after clearing orphaned model runners: {error}"))?;
        let remaining_runners: Vec<_> = snapshot.process_allocations.iter().filter(|allocation| {
            process_image_name(allocation.pid).and_then(|path| path.file_name().map(|name| name.to_string_lossy().to_ascii_lowercase()))
                .is_some_and(|name| is_known_model_runner(&name))
        }).collect();
        if remaining_runners.is_empty() {
            log_line(&format!("Startup model-runner cleanup verified: GPU {} MiB used / {} MiB total; remaining allocations [{}].", snapshot.used_bytes / 1_048_576, snapshot.total_bytes / 1_048_576, allocation_summary(&snapshot)));
            report_unknown_gpu_owners(&snapshot, "Ariadne startup");
            return Ok(());
        }
        if Instant::now() >= deadline {
            return Err(format!("GPU memory remained allocated by model runner(s) after cleanup: {}", allocation_summary(&snapshot)));
        }
        thread::sleep(Duration::from_millis(400));
    }
}

fn is_known_model_runner(image_name: &str) -> bool {
    matches!(image_name, "ollama.exe" | "llama.exe" | "llama-server.exe" | "llama-cli.exe" | "llamacppserver.exe")
}

fn gpu_owner_basename(pid: u32) -> Option<String> {
    if let Some(path) = process_image_name(pid) {
        return path.file_name().map(|name| name.to_string_lossy().to_ascii_lowercase());
    }
    // Windows may deny QueryFullProcessImageNameW for protected system
    // processes such as Desktop Window Manager. tasklist can still identify
    // the image name; use that only for classification/reporting, never to
    // authorize terminating a process.
    let output = Command::new("tasklist.exe")
        .args(["/FI", &format!("PID eq {pid}"), "/FO", "CSV", "/NH"])
        .creation_flags(0x0800_0000)
        .output()
        .ok()?;
    if !output.status.success() { return None; }
    let text = String::from_utf8_lossy(&output.stdout);
    for line in text.lines() {
        let fields: Vec<_> = line.trim().trim_matches('"').split("\",\"").collect();
        if fields.len() >= 2 && fields[1].parse::<u32>().ok() == Some(pid) {
            return Some(fields[0].to_ascii_lowercase());
        }
    }
    None
}

fn is_known_system_gpu_owner(image_name: &str) -> bool {
    image_name == "dwm.exe"
}

fn report_unknown_gpu_owners(snapshot: &super::gpu_residency::GpuMemorySnapshot, phase: &str) {
    let unknown: Vec<String> = snapshot.process_allocations.iter().filter(|allocation| {
        allocation.bytes >= UNKNOWN_GPU_OWNER_BLOCK_BYTES
            && gpu_owner_basename(allocation.pid)
                .is_none_or(|name| !is_known_model_runner(&name) && !is_known_system_gpu_owner(&name))
    }).map(|allocation| {
        let owner = gpu_owner_basename(allocation.pid).unwrap_or_else(|| "process name unavailable".into());
        format!("PID {} ({owner})={} MiB", allocation.pid, allocation.bytes / 1_048_576)
    }).collect();
    if !unknown.is_empty() {
        log_line(&format!("GPU inventory during {phase} found external GPU allocation(s): {}. Processes were left untouched; model admission will use measured free VRAM and model-size headroom.", unknown.join(", ")));
    }
}

fn is_exclusive_gpu_workload(workload: &str) -> bool {
    workload != "ai"
}

fn releases_idle_ollama_for_workload(workload: &str) -> bool {
    matches!(workload, "renderer" | "dungeon" | "music" | "image")
}

fn restore_residency_models(endpoint: &str, rows: &[serde_json::Value], num_parallel: u32, version: &str,
                            last_used: &mut std::collections::HashMap<String, Instant>) -> String {
    if rows.is_empty() { return "No previously resident Ollama models required restoration.".into(); }
    if version.is_empty() {
        return "Previous model residency could not be restored because Ollama version is unavailable for profile verification.".into();
    }
    let mut issues = Vec::new();
    for row in rows {
        let Some(name) = row.get("name").and_then(|value| value.as_str()) else { continue };
        let Some(context) = row.get("context_length").and_then(|value| value.as_u64()) else {
            issues.push(format!("{name} was not restored: Ollama did not report its loaded context length."));
            continue;
        };
        let context = context as u32;
        let inference_kind = match model_inference_kind(endpoint, name) {
            Ok(kind) => kind,
            Err(error) => {
                issues.push(format!("{name} was not restored: could not determine its Ollama inference capability: {error}"));
                continue;
            }
        };
        match preflight_model(endpoint, name, context, num_parallel, version, false, inference_kind, &std::collections::HashMap::new()) {
            Ok(_) => { last_used.insert(name.to_owned(), Instant::now()); }
            Err(error) => issues.push(format!("{name} was not restored: {error}")),
        }
    }
    let residency = match ollama_models(endpoint) {
        Ok(rows) => rows,
        Err(error) => return format!("Could not verify restored Ollama residency: {error}"),
    };
    for row in rows {
        let Some(name) = row.get("name").and_then(|value| value.as_str()) else { continue };
        if !residency.iter().any(|candidate| candidate.get("name").and_then(|value| value.as_str()).is_some_and(|candidate_name| ollama_model_name_matches(name, candidate_name))) {
            issues.push(format!("{name} is not resident after restoration"));
        }
    }
    if issues.is_empty() { "Previously resident Ollama models were restored through verified Host profiles.".into() }
    else { format!("Recovery warning: {}", issues.join("; ")) }
}

fn gpu_workload_conflicts(workload: &str, externally_delegated: bool,
                          active_workloads: &[String], inference_lease_count: usize) -> bool {
    if externally_delegated && workload == "ai" { return true; }
    if is_exclusive_gpu_workload(workload) {
        !active_workloads.is_empty() || inference_lease_count > 0
    } else {
        active_workloads.iter().any(|active| is_exclusive_gpu_workload(active))
    }
}

fn model_blocked_by_gpu_leases<'a>(model: &str, leases: impl Iterator<Item = &'a GpuLease>) -> bool {
    leases.into_iter().any(|lease| {
        is_exclusive_gpu_workload(&lease.workload)
            && !((lease.workload == "vault" || lease.workload == "transition")
                && lease.model.as_deref() == Some(model))
    })
}

#[cfg(test)]
mod gpu_workload_lease_tests {
    use super::{gpu_lease_owner_terminated, gpu_workload_conflicts, model_blocked_by_gpu_leases, releases_idle_ollama_for_workload, GpuLease};
    use std::collections::HashMap;

    #[test]
    fn workload_leases_reject_conflicting_gpu_owners() {
        assert!(!gpu_workload_conflicts("ai", false, &[], 0));
        assert!(gpu_workload_conflicts("ai", true, &[], 0));
        assert!(gpu_workload_conflicts("ai", false, &["renderer".into()], 0));
        assert!(gpu_workload_conflicts("renderer", false, &["ai".into()], 0));
        assert!(gpu_workload_conflicts("dungeon", false, &[], 1));
    }

    #[test]
    fn exclusive_workflow_lease_allows_only_its_configured_model() {
        let mut leases: HashMap<String, GpuLease> = HashMap::new();
        leases.insert("vault".into(), GpuLease { workload: "vault".into(), model: Some("configured-vault-model".into()), owner_pid: None, owner_process: None });
        assert!(!model_blocked_by_gpu_leases("configured-vault-model", leases.values()));
        assert!(model_blocked_by_gpu_leases("home-model", leases.values()));
        leases.clear();
        leases.insert("switch".into(), GpuLease { workload: "transition".into(), model: Some("selected-model".into()), owner_pid: None, owner_process: None });
        assert!(!model_blocked_by_gpu_leases("selected-model", leases.values()));
        assert!(model_blocked_by_gpu_leases("previous-model", leases.values()));
    }

    #[test]
    fn external_gpu_workloads_require_host_model_release_before_capacity_check() {
        for workload in ["renderer", "dungeon", "music", "image"] {
            assert!(releases_idle_ollama_for_workload(workload), "{workload} must free Ollama residency before checking its reserve");
        }
        for workload in ["vault", "transition", "ai"] {
            assert!(!releases_idle_ollama_for_workload(workload), "{workload} must follow model admission or inference ownership");
        }
    }

    #[test]
    fn only_confirmed_dead_client_owners_are_reclaimed() {
        assert!(gpu_lease_owner_terminated(Some(1234), Some(false)));
        assert!(!gpu_lease_owner_terminated(Some(1234), Some(true)));
        assert!(!gpu_lease_owner_terminated(Some(1234), None));
        assert!(!gpu_lease_owner_terminated(None, Some(false)));
    }

    #[test]
    fn ownerless_legacy_leases_are_not_reclaimed_by_guessing() {
        assert!(!gpu_lease_owner_terminated(None, None));
        assert!(!gpu_lease_owner_terminated(Some(1234), None));
    }
}

fn prune_expired_leases(state: &mut State) {
    let expired: Vec<String> = state.inference_leases.iter()
        .filter(|(_, lease)| lease.last_renewed_at.elapsed() > Duration::from_secs(15 * 60))
        .map(|(id, _)| id.clone()).collect();
    for id in expired {
        if let Some(lease) = state.inference_leases.remove(&id) {
            log_line(&format!("Expired stale model lease '{}' for '{}' after 15 minutes without a heartbeat.", id, lease.model));
        }
    }
}

fn gpu_lease_owner_terminated(owner_pid: Option<u32>, process_state: Option<bool>) -> bool {
    matches!((owner_pid, process_state), (Some(_), Some(false)))
}

fn open_client_process(pid: u32) -> Result<ClientProcessHandle, String> {
    use windows::Win32::System::Threading::{
        GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_SYNCHRONIZE,
    };
    unsafe {
        let process = match OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SYNCHRONIZE, false, pid) {
            Ok(process) => process,
            Err(error) => return Err(format!("Rust Host could not open verified GPU lease owner PID {pid}: {error}")),
        };
        let mut exit_code = 0u32;
        if let Err(error) = GetExitCodeProcess(process, &mut exit_code) {
            let _ = windows::Win32::Foundation::CloseHandle(process);
            return Err(format!("Rust Host could not validate GPU lease owner PID {pid}: {error}"));
        }
        if exit_code != 259 { // STILL_ACTIVE
            let _ = windows::Win32::Foundation::CloseHandle(process);
            return Err(format!("Rust Host refused a GPU lease because client PID {pid} has already exited."));
        }
        Ok(ClientProcessHandle(process.0 as isize))
    }
}

fn client_process_handle_is_active(handle: &ClientProcessHandle) -> Option<bool> {
    use windows::Win32::Foundation::HANDLE;
    use windows::Win32::System::Threading::GetExitCodeProcess;
    unsafe {
        let mut exit_code = 0u32;
        GetExitCodeProcess(HANDLE(handle.0 as *mut c_void), &mut exit_code)
            .ok().map(|_| exit_code == 259) // STILL_ACTIVE
    }
}

fn prune_dead_gpu_leases(state: &mut State) {
    let dead: Vec<String> = state.gpu_leases.iter()
        .filter_map(|(id, lease)| {
            let process_state = lease.owner_process.as_ref().and_then(client_process_handle_is_active);
            gpu_lease_owner_terminated(lease.owner_pid, process_state).then(|| id.clone())
        })
        .collect();
    for id in dead {
        if let Some(lease) = state.gpu_leases.remove(&id) {
            log_line(&format!(
                "Reclaimed orphaned Rust-host GPU workload lease '{}' for '{}' after owner PID {:?} exited.",
                id, lease.workload, lease.owner_pid,
            ));
        }
    }
}

fn runtime_health(runtime: &mut ManagedRuntime) -> Result<RuntimeResponse, String> {
    let listeners = ollama_listener_pids()?;
    if !listeners.contains(&runtime.child.id()) {
        return Err(format!("Ollama listener identity changed: host-owned PID {} is not listening on port 11434 (observed {:?}).", runtime.child.id(), listeners));
    }
    let address = endpoint_address(&runtime.endpoint)?;
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_millis(500)).map_err(|error| error.to_string())?;
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    stream.write_all(b"GET /api/tags HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n\r\n").map_err(|error| error.to_string())?;
    let mut response = String::new(); stream.read_to_string(&mut response).map_err(|error| error.to_string())?;
    let (head, body) = response.split_once("\r\n\r\n").ok_or("invalid Ollama catalogue response")?;
    if !head.starts_with("HTTP/1.0 200") && !head.starts_with("HTTP/1.1 200") { return Err(format!("Ollama catalogue returned {}", head.lines().next().unwrap_or("an error"))); }
    let payload: serde_json::Value = serde_json::from_str(body.trim()).map_err(|error| error.to_string())?;
    let models: Vec<String> = payload.get("models").and_then(|value| value.as_array()).into_iter().flatten().filter_map(|row| row.get("name").and_then(|name| name.as_str()).map(str::to_owned)).collect();
    let missing: Vec<&str> = runtime.required_models.iter().map(String::as_str).filter(|model| !models.iter().any(|known| known == model)).collect();
    if !missing.is_empty() { return Err(format!("Ollama catalogue is missing required models: {}", missing.join(", "))); }
    verify_chat_route(&runtime.endpoint)?;
    let version = api_version(&runtime.endpoint);
    if runtime.version.as_ref().is_some_and(|previous| Some(previous) != version.as_ref()) {
        log_line(&format!("Ollama API version changed under host supervision: {:?} -> {:?}", runtime.version, version));
    }
    runtime.version = version.clone();
    Ok(RuntimeResponse { v: 1, ok: true, state: "online".into(), detail: format!("Rust host owns Ollama PID {} at {}; required catalogue verified.", runtime.child.id(), runtime.executable.display()), pid: Some(runtime.child.id()), version, models, lease_id: None, residency: None })
}

fn preflight_model(
    endpoint: &str,
    model: &str,
    context_length: u32,
    num_parallel: u32,
    ollama_version: &str,
    allow_calibration: bool,
    inference_kind: &str,
    leases: &std::collections::HashMap<String, String>,
) -> Result<(String, serde_json::Value, String), String> {
    if !matches!(inference_kind, "generate" | "embed") {
        return Err(format!("Unsupported Rust-host inference kind '{inference_kind}'."));
    }
    if model.trim().is_empty() { return Err("Inference request did not specify a model; blocked by Rust-host preflight.".into()); }
    if context_length == 0 || num_parallel == 0 || ollama_version.is_empty() {
        return Err("Runtime profile key is incomplete (context, effective parallelism or Ollama version); inference blocked.".into());
    }
    let model_row = ollama_tags(endpoint)?.into_iter().find(|row| row.get("name").and_then(|v| v.as_str()).is_some_and(|name| ollama_model_name_matches(model, name)))
        .ok_or_else(|| format!("Requested model '{model}' is not in Ollama's local catalogue; inference blocked."))?;
    let digest = model_row.get("digest").and_then(|v| v.as_str()).unwrap_or_default().to_owned();
    if digest.is_empty() { return Err(format!("Ollama did not report a digest for '{model}'; runtime profile cannot be keyed safely.")); }
    let key = (digest.as_str(), ollama_version, context_length, num_parallel);
    let mut profiles = load_residency_profiles()?;
    let known_profile = profiles.iter().find(|profile| profile_matches(profile, key)).cloned();
    if known_profile.is_none() && !allow_calibration {
        return Err(format!("No verified full-GPU residency profile exists for '{model}' (digest {}, Ollama {}, context {}, parallelism {}). Explicit calibration is required; inference was not sent.", &digest[..digest.len().min(12)], ollama_version, context_length, num_parallel));
    }
    let resident = ollama_models(endpoint)?;
    let resident_target = resident.iter().find(|row| row.get("name").and_then(|v| v.as_str()).is_some_and(|name| ollama_model_name_matches(model, name)));
    let target_was_resident = resident_target.is_some();
    let resident_context = resident_target.and_then(|row| row.get("context_length").and_then(|value| value.as_u64())).unwrap_or(0) as u32;
    // Ollama may keep a model warm at an older context. Reconcile that resident
    // state before validating it against the exact digest/version/context key.
    let reload_target_for_admission = target_was_resident
        && resident_context_requires_reload(known_profile.is_some(), resident_context, context_length);
    if reload_target_for_admission && leases.values().any(|leased| ollama_model_name_matches(leased, model)) {
        return Err(format!("'{model}' is already in active use at context {resident_context}; it cannot be reloaded for requested context {context_length} or profile calibration while leased."));
    }
    for row in &resident {
        let Some(name) = row.get("name").and_then(|v| v.as_str()) else { continue };
        if ollama_model_name_matches(model, name) { continue; }
        if leases.values().any(|leased| ollama_model_name_matches(leased, name)) {
            return Err(format!("GPU preflight found Ollama model '{name}' resident and in active use; '{model}' must wait."));
        }
    }
    let previous_models: Vec<serde_json::Value> = resident.iter().filter(|row| {
        row.get("name").and_then(|v| v.as_str()).is_some_and(|name| !ollama_model_name_matches(model, name))
    }).cloned().collect();
    // A verified profile supplies this model's incremental footprint, so admit
    // it against fresh WDDM free memory while keeping other models warm.
    // Unknown-profile calibration still needs an isolated baseline.
    let mut models_to_release = if !release_competing_models_for_admission(known_profile.is_some()) {
        Vec::new()
    } else {
        previous_models.clone()
    };
    let mut models_to_restore = previous_models.clone();
    if reload_target_for_admission {
        if let Some(row) = resident_target {
            models_to_release.push(row.clone());
            models_to_restore.push(row.clone());
        }
    }
    let result = (|| -> Result<(serde_json::Value, String), String> {
        for row in &models_to_release {
            let name = row.get("name").and_then(|v| v.as_str()).unwrap_or_default();
            if !name.is_empty() { unload_model(endpoint, name)?; }
        }
        // Baseline includes retained verified residents and follows any
        // release required for calibration or target-context reconciliation.
        let before = super::gpu_residency::sample().map_err(|error| format!("WDDM baseline unavailable after idle-model release: {error}"))?;
        let reserve = GPU_SAFETY_RESERVE_BYTES;
        if before.free_bytes <= reserve {
            return Err(format!("Calibration blocked before preload: WDDM free memory is {} MiB, which does not exceed the configured 1024 MiB reserve.", before.free_bytes / 1_048_576));
        }
        let loaded_by_this_admission = !target_was_resident || reload_target_for_admission;
        if let Some(profile) = known_profile.as_ref().filter(|_| loaded_by_this_admission) {
            let required_free_bytes = verified_profile_required_free_bytes(profile);
            if before.free_bytes < required_free_bytes {
                return Err(format!("Verified profile requires about {} MiB WDDM impact plus the configured 1024 MiB reserve; baseline free is {} MiB. Model was not loaded.", profile.wddm_impact_bytes / 1_048_576, before.free_bytes / 1_048_576));
            }
        } else if known_profile.is_none() {
            log_line(&format!("Explicit residency calibration started for '{model}': digest={}, Ollama={ollama_version}, context={context_length}, parallelism={num_parallel}; baseline WDDM free={} MiB.", &digest[..digest.len().min(12)], before.free_bytes / 1_048_576));
        }
        if loaded_by_this_admission {
            let (path, preload) = residency_probe(inference_kind, model, context_length);
            ollama_http_timeout(endpoint, "POST", path, Some(&preload), 300)?;
            wait_for_target_resident(endpoint, model)?;
        }
        let after = super::gpu_residency::sample().map_err(|error| format!("WDDM post-load sample unavailable: {error}"))?;
        let loaded = ollama_models(endpoint)?.into_iter().find(|row| row.get("name").and_then(|v| v.as_str()).is_some_and(|name| ollama_model_name_matches(model, name)))
            .ok_or_else(|| format!("Ollama /api/ps did not report '{model}' after preload."))?;
        let model_bytes = loaded.get("size").and_then(|v| v.as_u64()).unwrap_or(0);
        let gpu_bytes = loaded.get("size_vram").and_then(|v| v.as_u64()).unwrap_or(0);
        let actual_context = loaded.get("context_length").and_then(|v| v.as_u64()).unwrap_or(0) as u32;
        if actual_context != context_length {
            return Err(format!("Ollama /api/ps reports context {actual_context}, requested {context_length}; WDDM free after preload={} MiB, total GPU impact={} MiB; profile was not accepted.", after.free_bytes / 1_048_576, after.used_bytes.saturating_sub(before.used_bytes) / 1_048_576));
        }
        if !is_full_gpu_residency(model_bytes, gpu_bytes) {
            return Err(format!("Full GPU residency verification failed: /api/ps size={} MiB, size_vram={} MiB; WDDM free after preload={} MiB, total GPU impact={} MiB, reserve=1024 MiB. CPU offload is not admitted.", model_bytes / 1_048_576, gpu_bytes / 1_048_576, after.free_bytes / 1_048_576, after.used_bytes.saturating_sub(before.used_bytes) / 1_048_576));
        }
        if !meets_gpu_reserve(after.free_bytes, reserve) {
            return Err(format!("Insufficient VRAM after preload: WDDM free={} MiB, below configured 1024 MiB reserve; total GPU memory impact={} MiB. Model will be unloaded.", after.free_bytes / 1_048_576, after.used_bytes.saturating_sub(before.used_bytes) / 1_048_576));
        }
        let impact = verified_profile_impact(
            loaded_by_this_admission,
            before.used_bytes,
            after.used_bytes,
            known_profile.as_ref().map(|profile| profile.wddm_impact_bytes),
        );
        let Some(impact) = impact else {
            return Err("WDDM showed no positive total GPU memory impact after preload; refusing to create an unverified profile.".into());
        };
        let profile = ResidencyProfile { digest: digest.clone(), ollama_version: ollama_version.to_owned(), context_length, num_parallel, model_bytes, gpu_bytes, wddm_impact_bytes: impact };
        let updated = if let Some(old) = profiles.iter_mut().find(|candidate| profile_matches(candidate, key)) {
            old.wddm_impact_bytes = old.wddm_impact_bytes.max(impact);
            old.model_bytes = model_bytes; old.gpu_bytes = gpu_bytes; old.clone()
        } else { profiles.push(profile.clone()); profile };
        if loaded_by_this_admission { save_residency_profiles(&profiles)?; }
        let inventory = serde_json::json!({
            "before": before, "after": after, "model": loaded,
            "profile": updated, "reserve_bytes": reserve,
            "full_gpu_residency": true,
        });
        let detail = format!("{} profile for '{model}' verified: /api/ps size=size_vram={} MiB; WDDM total impact={} MiB; free after load={} MiB; configured reserve=1024 MiB.", if known_profile.is_some() { "Known" } else { "Calibrated" }, gpu_bytes / 1_048_576, impact / 1_048_576, after.free_bytes / 1_048_576);
        Ok((inventory, detail))
    })();
    let (inventory, detail) = match result {
        Ok(value) => value,
        Err(error) => {
            let mut recovery = Vec::new();
            if !target_was_resident || reload_target_for_admission { let _ = unload_model(endpoint, model); }
            for row in &models_to_restore {
                let name = row.get("name").and_then(|v| v.as_str()).unwrap_or_default();
                let context = row.get("context_length").and_then(|v| v.as_u64()).unwrap_or(0);
                if !name.is_empty() {
                    if context == 0 {
                        recovery.push(format!("restore {name} failed: Ollama did not report its loaded context length."));
                        continue;
                    }
                    let inference_kind = match model_inference_kind(endpoint, name) {
                        Ok(kind) => kind,
                        Err(restore_error) => {
                            recovery.push(format!("restore {name} failed: could not determine its Ollama inference capability: {restore_error}"));
                            continue;
                        }
                    };
                    match preflight_model(endpoint, name, context as u32, num_parallel, ollama_version, false, inference_kind, &std::collections::HashMap::new()) {
                        Ok((_lease_id, _inventory, detail)) => log_line(&format!("Prior model residency restored through verified Rust-host admission: {detail}")),
                        Err(restore_error) => recovery.push(format!("restore {name} failed: {restore_error}")),
                    }
                }
            }
            if let Ok(resident_after_recovery) = ollama_models(endpoint) {
                for row in &models_to_restore {
                    let name = row.get("name").and_then(|v| v.as_str()).unwrap_or_default();
                    if !name.is_empty() && !resident_after_recovery.iter().any(|candidate| candidate.get("name").and_then(|v| v.as_str()).is_some_and(|candidate_name| ollama_model_name_matches(name, candidate_name))) {
                        recovery.push(format!("{name} was not resident after restoration"));
                    }
                }
            } else if !models_to_restore.is_empty() {
                recovery.push("could not verify restored Ollama residency".into());
            }
            if recovery.is_empty() { return Err(format!("{error} Prior idle model residency was restored where available.")); }
            return Err(format!("{error} Recovery warning: {}.", recovery.join("; ")));
        }
    };
    log_line(&format!("GPU/Ollama profile admission for '{model}': {detail}"));
    let lease_id = format!("{}-{}", std::process::id(), uuid_like());
    Ok((lease_id, inventory, detail))
}

fn wait_for_target_resident(endpoint: &str, model: &str) -> Result<(), String> {
    let deadline = Instant::now() + Duration::from_secs(20);
    loop {
        let resident = ollama_models(endpoint)?;
        if resident.iter().any(|row| row.get("name").and_then(|v| v.as_str()).is_some_and(|name| ollama_model_name_matches(model, name))) { return Ok(()); }
        if Instant::now() >= deadline {
            return Err(format!("Ollama did not report target model '{model}' resident within 20 seconds."));
        }
        thread::sleep(Duration::from_millis(400));
    }
}

fn profile_matches(profile: &ResidencyProfile, key: (&str, &str, u32, u32)) -> bool {
    profile.digest == key.0 && profile.ollama_version == key.1 && profile.context_length == key.2 && profile.num_parallel == key.3
}

fn release_competing_models_for_admission(has_verified_profile: bool) -> bool {
    !has_verified_profile
}

fn verified_profile_required_free_bytes(profile: &ResidencyProfile) -> u64 {
    profile.wddm_impact_bytes.saturating_add(GPU_SAFETY_RESERVE_BYTES)
}

fn is_full_gpu_residency(model_bytes: u64, gpu_bytes: u64) -> bool {
    model_bytes > 0 && gpu_bytes == model_bytes
}

fn meets_gpu_reserve(free_bytes: u64, reserve_bytes: u64) -> bool {
    free_bytes >= reserve_bytes
}

fn residency_profile_path() -> Result<PathBuf, String> {
    let root = env::var_os("LOCALAPPDATA").map(PathBuf::from).ok_or("LOCALAPPDATA is unavailable; runtime profiles cannot be persisted")?;
    Ok(root.join("Ariadne").join("host").join("residency-profiles.json"))
}

fn load_residency_profiles() -> Result<Vec<ResidencyProfile>, String> {
    let path = residency_profile_path()?;
    if !path.exists() { return Ok(Vec::new()); }
    let bytes = std::fs::read(path).map_err(|error| format!("could not read residency profiles: {error}"))?;
    serde_json::from_slice(&bytes).map_err(|error| format!("residency profile file is invalid: {error}"))
}

fn save_residency_profiles(profiles: &[ResidencyProfile]) -> Result<(), String> {
    let path = residency_profile_path()?;
    let parent = path.parent().ok_or("invalid residency profile path")?;
    std::fs::create_dir_all(parent).map_err(|error| format!("could not create residency profile directory: {error}"))?;
    let bytes = serde_json::to_vec_pretty(profiles).map_err(|error| error.to_string())?;
    let temporary = path.with_extension(format!("{}.tmp", uuid_like()));
    std::fs::write(&temporary, bytes).map_err(|error| format!("could not write residency profile: {error}"))?;
    if path.exists() { std::fs::remove_file(&path).map_err(|error| format!("could not replace residency profile: {error}"))?; }
    std::fs::rename(&temporary, &path).map_err(|error| format!("could not publish residency profile: {error}"))
}

fn effective_num_parallel() -> Result<u32, String> {
    match env::var("OLLAMA_NUM_PARALLEL") {
        Ok(value) => value.parse::<u32>().ok().filter(|value| *value > 0)
            .ok_or_else(|| format!("OLLAMA_NUM_PARALLEL='{value}' is not a positive integer; Rust host will not start an unkeyable Ollama runtime.")),
        Err(env::VarError::NotPresent) => Ok(1),
        Err(error) => Err(format!("could not read OLLAMA_NUM_PARALLEL: {error}")),
    }
}

#[cfg(test)]
fn has_model_capacity(free_bytes: u64, required_bytes: u64) -> bool {
    free_bytes >= required_bytes
}

fn verified_profile_impact(
    loaded_by_this_admission: bool,
    before_used_bytes: u64,
    after_used_bytes: u64,
    known_wddm_impact_bytes: Option<u64>,
) -> Option<u64> {
    let impact = if loaded_by_this_admission {
        after_used_bytes.saturating_sub(before_used_bytes)
    } else {
        known_wddm_impact_bytes.unwrap_or_default()
    };
    (impact > 0).then_some(impact)
}

fn resident_context_requires_reload(has_matching_profile: bool, resident_context: u32, requested_context: u32) -> bool {
    !has_matching_profile || resident_context != requested_context
}

fn ollama_model_name_matches(requested: &str, available: &str) -> bool {
    requested == available
        || (!requested.contains(':') && available == format!("{requested}:latest"))
}

fn uuid_like() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    format!("{:x}", SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_nanos())
}

fn allocation_summary(gpu: &super::gpu_residency::GpuMemorySnapshot) -> String {
    gpu.process_allocations.iter().map(|allocation| {
        let owner = gpu_owner_basename(allocation.pid)
            .unwrap_or_else(|| "process name unavailable".into());
        format!("PID {} ({owner})={} MiB", allocation.pid, allocation.bytes / 1_048_576)
    }).collect::<Vec<_>>().join(", ")
}

fn ollama_models(endpoint: &str) -> Result<Vec<serde_json::Value>, String> {
    let payload = ollama_http(endpoint, "GET", "/api/ps", None)?;
    Ok(payload.get("models").and_then(|value| value.as_array()).cloned().unwrap_or_default())
}

fn ollama_tags(endpoint: &str) -> Result<Vec<serde_json::Value>, String> {
    let payload = ollama_http(endpoint, "GET", "/api/tags", None)?;
    Ok(payload.get("models").and_then(|value| value.as_array()).cloned().unwrap_or_default())
}

#[cfg(test)]
mod model_name_tests {
    use super::{
        has_model_capacity, inference_kind_from_capabilities, is_full_gpu_residency,
        meets_gpu_reserve, ollama_model_name_matches, profile_matches,
        release_competing_models_for_admission, residency_probe,
        resident_context_requires_reload, verified_profile_impact,
        verified_profile_required_free_bytes, preflight_model, ResidencyProfile,
    };

    #[test]
    fn verified_profile_admits_alongside_resident_models_instead_of_evicting_them() {
        assert!(!release_competing_models_for_admission(true));
        assert!(release_competing_models_for_admission(false));
    }

    #[test]
    fn untagged_requested_model_matches_ollama_latest_alias() {
        assert!(ollama_model_name_matches("nomic-embed-text", "nomic-embed-text:latest"));
        assert!(ollama_model_name_matches("nomic-embed-text", "nomic-embed-text"));
        assert!(!ollama_model_name_matches("nomic-embed-text:latest", "nomic-embed-text"));
        assert!(!ollama_model_name_matches("nomic-embed-text", "nomic-embed-text:other"));
    }

    #[test]
    fn runtime_profile_requires_exact_digest_version_context_and_parallelism() {
        let profile = ResidencyProfile { digest: "sha256:abc".into(), ollama_version: "0.40.6".into(), context_length: 16_384, num_parallel: 1,
            model_bytes: 12_000, gpu_bytes: 12_000, wddm_impact_bytes: 12_400 };
        assert!(profile_matches(&profile, ("sha256:abc", "0.40.6", 16_384, 1)));
        assert!(!profile_matches(&profile, ("sha256:def", "0.40.6", 16_384, 1)));
        assert!(!profile_matches(&profile, ("sha256:abc", "0.40.7", 16_384, 1)));
        assert!(!profile_matches(&profile, ("sha256:abc", "0.40.6", 8_192, 1)));
        assert!(!profile_matches(&profile, ("sha256:abc", "0.40.6", 16_384, 2)));
    }

    #[test]
    fn verified_admission_uses_profiled_wddm_impact_plus_reserve_not_catalogue_estimate() {
        let mib = 1024 * 1024;
        let profile = ResidencyProfile {
            digest: "gpt-oss-20b".into(), ollama_version: "0.40.6".into(),
            context_length: 16_384, num_parallel: 1,
            model_bytes: 13_154 * mib, gpu_bytes: 12_161 * mib,
            wddm_impact_bytes: 12_325 * mib,
        };
        let free_bytes = 13_479 * mib;
        let verified_requirement = verified_profile_required_free_bytes(&profile);
        let obsolete_catalogue_requirement = 14_178 * mib;

        assert_eq!(verified_requirement, 13_349 * mib);
        assert!(has_model_capacity(free_bytes, verified_requirement));
        assert!(!has_model_capacity(free_bytes, obsolete_catalogue_requirement));
    }

    #[test]
    fn full_gpu_and_one_gib_reserve_are_both_required() {
        assert!(is_full_gpu_residency(12_000, 12_000));
        assert!(!is_full_gpu_residency(12_000, 11_999));
        assert!(!is_full_gpu_residency(0, 0));
        assert!(meets_gpu_reserve(1024 * 1024 * 1024, 1024 * 1024 * 1024));
        assert!(!meets_gpu_reserve(1024 * 1024 * 1024 - 1, 1024 * 1024 * 1024));
    }

    #[test]
    fn cold_calibration_requires_positive_measured_wddm_impact() {
        assert_eq!(verified_profile_impact(true, 10_000, 20_000, None), Some(10_000));
        assert_eq!(verified_profile_impact(true, 10_000, 10_000, None), None);
    }

    #[test]
    fn warm_verified_model_uses_saved_footprint_when_incremental_impact_is_zero() {
        assert_eq!(verified_profile_impact(false, 20_000, 20_000, Some(12_400)), Some(12_400));
        assert_eq!(verified_profile_impact(false, 20_000, 20_000, None), None);
        assert_eq!(verified_profile_impact(false, 20_000, 20_000, Some(0)), None);
    }

    #[test]
    fn warm_model_reloads_only_when_its_resident_context_does_not_match_the_profile_key() {
        assert!(!resident_context_requires_reload(true, 32_768, 32_768));
        assert!(resident_context_requires_reload(true, 4_096, 32_768));
        assert!(resident_context_requires_reload(false, 32_768, 32_768));
    }

    #[test]
    fn missing_context_budget_is_rejected_before_ollama_access() {
        let error = preflight_model(
            "http://127.0.0.1:9", "qwen", 0, 1, "0.0.0", false,
            "generate", &std::collections::HashMap::new(),
        ).unwrap_err();
        assert!(error.contains("context"));
        assert!(error.contains("blocked"));
    }

    #[test]
    fn explicit_embedding_calibration_probes_embed_endpoint_and_context() {
        let (path, body) = residency_probe("embed", "nomic-embed-text", 4096);
        assert_eq!(path, "/api/embed");
        assert_eq!(body["model"], "nomic-embed-text");
        assert_eq!(body["options"]["num_ctx"], 4096);
        assert!(body["input"][0].as_str().unwrap().contains("calibration probe"));
        assert_eq!(residency_probe("generate", "qwen", 8192).0, "/api/generate");
    }

    #[test]
    fn recovery_uses_ollama_capabilities_for_embedding_only_models() {
        assert_eq!(inference_kind_from_capabilities(&[serde_json::json!("embedding")]), Ok("embed"));
        assert_eq!(inference_kind_from_capabilities(&[serde_json::json!("completion"), serde_json::json!("embedding")]), Ok("generate"));
        assert!(inference_kind_from_capabilities(&[]).is_err());
    }
}

fn residency_probe(inference_kind: &str, model: &str, context_length: u32) -> (&'static str, serde_json::Value) {
    if inference_kind == "embed" {
        (
            "/api/embed",
            serde_json::json!({
                "model": model,
                "input": ["Ariadne Rust-host residency calibration probe."],
                "truncate": true,
                "keep_alive": -1,
                "options": {"num_ctx": context_length}
            }),
        )
    } else {
        (
            "/api/generate",
            serde_json::json!({"model": model, "prompt": "", "stream": false, "keep_alive": -1, "options": {"num_ctx": context_length}}),
        )
    }
}

fn model_inference_kind(endpoint: &str, model: &str) -> Result<&'static str, String> {
    let details = ollama_http(endpoint, "POST", "/api/show", Some(&serde_json::json!({"model": model})))?;
    let capabilities = details.get("capabilities").and_then(serde_json::Value::as_array)
        .ok_or_else(|| format!("Ollama /api/show omitted capabilities for '{model}'"))?;
    inference_kind_from_capabilities(capabilities)
        .map_err(|_| format!("Ollama model '{model}' supports neither completion nor embedding"))
}

fn inference_kind_from_capabilities(capabilities: &[serde_json::Value]) -> Result<&'static str, ()> {
    let supports_embedding = capabilities.iter().any(|value| value.as_str() == Some("embedding"));
    let supports_completion = capabilities.iter().any(|value| value.as_str() == Some("completion"));
    match (supports_completion, supports_embedding) {
        (true, _) => Ok("generate"),
        (false, true) => Ok("embed"),
        (false, false) => Err(()),
    }
}

fn unload_model(endpoint: &str, model: &str) -> Result<(), String> {
    let body = serde_json::json!({"model": model, "keep_alive": 0, "stream": false});
    let _ = ollama_http(endpoint, "POST", "/api/generate", Some(&body))?;
    log_line(&format!("Rust-host preflight unloaded idle Ollama model '{model}' before admitting another workload"));
    Ok(())
}

fn ollama_http(endpoint: &str, method: &str, path: &str, body: Option<&serde_json::Value>) -> Result<serde_json::Value, String> {
    ollama_http_timeout(endpoint, method, path, body, 5)
}

fn ollama_http_timeout(endpoint: &str, method: &str, path: &str, body: Option<&serde_json::Value>, timeout_seconds: u64) -> Result<serde_json::Value, String> {
    let address = endpoint_address(endpoint)?;
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(2)).map_err(|error| format!("Ollama preflight connection failed: {error}"))?;
    stream.set_read_timeout(Some(Duration::from_secs(timeout_seconds))).map_err(|error| error.to_string())?;
    let payload = body.map(serde_json::to_vec).transpose().map_err(|error| error.to_string())?.unwrap_or_default();
    let headers = if payload.is_empty() { String::new() } else { format!("Content-Type: application/json\r\nContent-Length: {}\r\n", payload.len()) };
    write!(stream, "{method} {path} HTTP/1.0\r\nHost: localhost\r\nAccept: application/json\r\nConnection: close\r\n{headers}\r\n").map_err(|error| error.to_string())?;
    if !payload.is_empty() { stream.write_all(&payload).map_err(|error| error.to_string())?; }
    let mut response = String::new();
    stream.read_to_string(&mut response).map_err(|error| format!("Ollama preflight response read failed: {error}"))?;
    let (head, response_body) = response.split_once("\r\n\r\n").ok_or("invalid Ollama preflight HTTP response")?;
    if !head.starts_with("HTTP/1.0 200") && !head.starts_with("HTTP/1.1 200") {
        return Err(format!("Ollama preflight {method} {path} returned {}: {}", head.lines().next().unwrap_or("unknown status"), response_body.trim()));
    }
    serde_json::from_str(response_body.trim()).map_err(|error| format!("invalid Ollama preflight JSON: {error}"))
}

fn verify_chat_route(endpoint: &str) -> Result<(), String> {
    let address = endpoint_address(endpoint)?;
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_millis(500))
        .map_err(|error| error.to_string())?;
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let body = r#"{"model":"__ariadne_route_probe_missing_model__","messages":[],"stream":false}"#;
    write!(
        stream,
        "POST /api/chat HTTP/1.0\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
        body.len(),
        body
    )
    .map_err(|error| error.to_string())?;
    let mut response = String::new();
    stream.read_to_string(&mut response).map_err(|error| error.to_string())?;
    let status = response.lines().next().unwrap_or_default();
    if status.contains(" 400 ") || status.contains(" 422 ") {
        return Ok(());
    }
    if status.contains(" 404 ") {
        let message = response
            .split_once("\r\n\r\n")
            .and_then(|(_, body)| serde_json::from_str::<serde_json::Value>(body.trim()).ok())
            .and_then(|payload| payload.get("error").and_then(|value| value.as_str()).map(str::to_owned))
            .unwrap_or_default()
            .to_ascii_lowercase();
        if message.contains("model") && message.contains("not found") {
            return Ok(());
        }
    }
    Err(format!("Ollama /api/chat compatibility probe failed: {status}"))
}

fn ollama_listener_pids() -> Result<Vec<u32>, String> {
    let output = Command::new("netstat.exe").args(["-ano", "-p", "tcp"]).creation_flags(0x0800_0000).output().map_err(|error| format!("could not inspect Ollama listener: {error}"))?;
    if !output.status.success() { return Err("could not inspect Ollama listener".into()); }
    let mut pids = Vec::new();
    for line in String::from_utf8_lossy(&output.stdout).lines() {
        let fields: Vec<&str> = line.split_whitespace().collect();
        if fields.len() >= 5 && fields[1].ends_with(":11434") && fields[3].eq_ignore_ascii_case("LISTENING") {
            if let Ok(pid) = fields[4].parse::<u32>() { pids.push(pid); }
        }
    }
    Ok(pids)
}

fn endpoint_address(endpoint: &str) -> Result<std::net::SocketAddr, String> {
    let authority = endpoint.strip_prefix("http://").ok_or("Rust Ollama supervisor currently requires a local HTTP endpoint")?.split('/').next().unwrap_or("");
    let addresses: Vec<_> = authority.to_socket_addrs().map_err(|error| error.to_string())?.collect();
    let address = addresses.iter().find(|address| address.is_ipv4()).copied()
        .or_else(|| addresses.first().copied()).ok_or("Ollama endpoint did not resolve".to_string())?;
    if !address.ip().is_loopback() { return Err("Rust Ollama supervisor only admits loopback endpoints".into()); }
    Ok(address)
}

fn api_version(endpoint: &str) -> Option<String> {
    let address = endpoint_address(endpoint).ok()?;
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_millis(300)).ok()?;
    let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
    stream.write_all(b"GET /api/version HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n\r\n").ok()?;
    let mut response = String::new(); stream.read_to_string(&mut response).ok()?;
    let body = response.split_once("\r\n\r\n")?.1;
    serde_json::from_str::<serde_json::Value>(body.trim()).ok()?.get("version")?.as_str().map(str::to_owned)
}

fn installer_running() -> Result<bool, String> {
    let output = Command::new("tasklist.exe").args(["/FI", "IMAGENAME eq OllamaSetup.exe", "/FO", "CSV", "/NH"]).creation_flags(0x0800_0000).output().map_err(|error| error.to_string())?;
    if !output.status.success() { return Err("Could not inspect the Ollama update installer.".into()); }
    Ok(String::from_utf8_lossy(&output.stdout).to_ascii_lowercase().contains("ollamasetup.exe"))
}

fn stop_existing_ollama() -> Result<(), String> {
    // Only target executable names reported by Windows for the Ollama service;
    // unrelated listeners on the configured port are left alone.
    // Stop the desktop supervisor first so it cannot respawn its listener.
    let desktop = Command::new("tasklist.exe").args(["/FI", "IMAGENAME eq Ollama app.exe", "/FO", "CSV", "/NH"]).creation_flags(0x0800_0000).output().map_err(|error| error.to_string())?;
    for line in String::from_utf8_lossy(&desktop.stdout).lines() {
        if line.to_ascii_lowercase().contains("ollama app.exe") {
            let fields: Vec<&str> = line.split(',').collect();
            if let Some(pid) = fields.get(1).map(|field| field.trim_matches('"').trim()) {
                let pid_value = pid.parse::<u32>().map_err(|_| "Ollama Desktop returned an invalid PID")?;
                let image = process_image_name(pid_value).ok_or_else(|| format!("Could not verify Ollama Desktop process image for PID {pid_value}; left it running."))?;
                if !image.file_name().is_some_and(|name| name.to_string_lossy().eq_ignore_ascii_case("ollama app.exe")) {
                    return Err(format!("Ollama Desktop PID {pid_value} did not match its expected executable; left it running."));
                }
                let killed = Command::new("taskkill.exe").args(["/PID", pid, "/T", "/F"]).creation_flags(0x0800_0000).output().map_err(|error| error.to_string())?;
                if !killed.status.success() { return Err(format!("Could not stop Ollama Desktop supervisor PID {pid}.")); }
            }
        }
    }
    let output = Command::new("netstat.exe").args(["-ano", "-p", "tcp"]).creation_flags(0x0800_0000).output().map_err(|error| error.to_string())?;
    if !output.status.success() { return Err("Could not inspect local TCP listeners before Ollama startup.".into()); }
    let mut pids = Vec::new();
    for line in String::from_utf8_lossy(&output.stdout).lines() {
        let fields: Vec<&str> = line.split_whitespace().collect();
        if fields.len() >= 5 && fields[1].ends_with(":11434") && fields[3].eq_ignore_ascii_case("LISTENING") {
            if let Ok(pid) = fields[4].parse::<u32>() { pids.push(pid); }
        }
    }
    for pid in pids {
        let image = process_image_name(pid).ok_or_else(|| format!("Could not verify process image for port 11434 owner PID {pid}; left it running."))?;
        if !image.file_name().is_some_and(|name| name.to_string_lossy().eq_ignore_ascii_case("ollama.exe")) {
            return Err(format!("Port 11434 is owned by a process other than Ollama (PID {pid}); it was left untouched."));
        }
        let killed = Command::new("taskkill.exe").args(["/PID", &pid.to_string(), "/T", "/F"]).creation_flags(0x0800_0000).output().map_err(|error| error.to_string())?;
        if !killed.status.success() { return Err(format!("Could not stop verified Ollama process PID {pid}.")); }
    }
    Ok(())
}

fn process_image_name(pid: u32) -> Option<PathBuf> {
    use windows::core::PWSTR;
    use windows::Win32::Foundation::CloseHandle;
    use windows::Win32::System::Threading::{OpenProcess, QueryFullProcessImageNameW, PROCESS_NAME_FORMAT, PROCESS_QUERY_LIMITED_INFORMATION};
    unsafe {
        let process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid).ok()?;
        let mut buffer = vec![0u16; 32768];
        let mut length = buffer.len() as u32;
        let result = QueryFullProcessImageNameW(process, PROCESS_NAME_FORMAT(0), PWSTR(buffer.as_mut_ptr()), &mut length).ok();
        let _ = CloseHandle(process);
        result?;
        Some(PathBuf::from(String::from_utf16_lossy(&buffer[..length as usize])))
    }
}

fn find_ollama_executable() -> Result<PathBuf, String> {
    let mut candidates = Vec::new();
    if let Some(local) = env::var_os("LOCALAPPDATA") { candidates.push(PathBuf::from(local).join("Programs/Ollama/ollama.exe")); }
    if let Some(program_files) = env::var_os("PROGRAMFILES") { candidates.push(PathBuf::from(program_files).join("Ollama/ollama.exe")); }
    if let Some(path) = env::var_os("PATH") { candidates.extend(env::split_paths(&path).map(|directory| directory.join("ollama.exe"))); }
    candidates.into_iter().find(|path| path.is_file()).ok_or("Could not find Ollama executable in its standard install locations or PATH.".into())
}

fn log_line(message: &str) {
    super::log_line(message);
}
