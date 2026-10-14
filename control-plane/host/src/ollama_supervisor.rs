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
const UNKNOWN_GPU_OWNER_BLOCK_BYTES: u64 = 1024 * 1024 * 1024;

#[derive(Debug, Deserialize)]
struct RuntimeRequest {
    v: u32,
    #[serde(rename = "type")]
    kind: String,
    endpoint: Option<String>,
    model_store: Option<String>,
    required_models: Option<Vec<String>>,
    model: Option<String>,
    lease_id: Option<String>,
    force: Option<bool>,
    preserve_models: Option<Vec<String>>,
}

#[derive(Debug, Serialize)]
struct RuntimeResponse {
    v: u32,
    ok: bool,
    state: String,
    detail: String,
    pid: Option<u32>,
    version: Option<String>,
    models: Vec<String>,
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
}

struct InferenceLease {
    model: String,
    acquired_at: Instant,
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
    model_last_used: std::collections::HashMap<String, Instant>,
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

    fn admit_model(&self, endpoint: String, model: String) -> RuntimeResponse {
        let Ok(mut state) = self.state.lock() else { return RuntimeResponse::error("Ollama supervisor state is unavailable.") };
        prune_expired_leases(&mut state);
        let (pid, version, models) = {
            let Some(runtime) = state.runtime.as_mut() else { return RuntimeResponse::error("Rust host has not established Ollama ownership; inference is blocked.") };
            if endpoint_address(&runtime.endpoint).ok() != endpoint_address(&endpoint).ok()
                || runtime.child.try_wait().ok().flatten().is_some() {
                return RuntimeResponse::error("Ollama runtime identity is not healthy; inference is blocked pending host reconciliation.");
            }
            (runtime.child.id(), runtime.version.clone(), runtime.required_models.clone())
        };
        let active_models: std::collections::HashMap<String, String> = state.inference_leases.iter()
            .map(|(id, lease)| (id.clone(), lease.model.clone())).collect();
        match preflight_model(&endpoint, &model, &active_models) {
            Ok((lease_id, inventory, detail)) => {
                log_line(&detail);
                state.inference_leases.insert(lease_id.clone(), InferenceLease { model, acquired_at: Instant::now() });
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

    fn unload_model(&self, endpoint: String, model: String) -> RuntimeResponse {
        let Ok(mut state) = self.state.lock() else { return RuntimeResponse::error("Ollama supervisor state is unavailable.") };
        prune_expired_leases(&mut state);
        if state.inference_leases.values().any(|lease| lease.model == model) {
            return RuntimeResponse::error(format!("Cannot unload '{model}': Rust host has an active inference lease for it."));
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

    fn release_idle_models(&self, endpoint: String, force: bool, preserve: Vec<String>) -> RuntimeResponse {
        let Ok(mut state) = self.state.lock() else { return RuntimeResponse::error("Ollama supervisor state is unavailable.") };
        prune_expired_leases(&mut state);
        let Some(runtime) = state.runtime.as_mut() else { return RuntimeResponse::error("Rust host does not own an Ollama runtime; model release blocked.") };
        if endpoint_address(&runtime.endpoint).ok() != endpoint_address(&endpoint).ok()
            || runtime.child.try_wait().ok().flatten().is_some() {
            return RuntimeResponse::error("Ollama runtime identity is not healthy; model release blocked pending host reconciliation.");
        }
        let active: std::collections::HashSet<String> = state.inference_leases.values().map(|lease| lease.model.clone()).collect();
        let preserved: std::collections::HashSet<String> = preserve.into_iter().collect();
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
            if preserved.contains(&model) { continue; }
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
        let pipe = CreateNamedPipeW(pipe_name, PIPE_ACCESS_DUPLEX, PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS, 1, 4096, 4096, 0, Some(&attributes));
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
        let response = match serde_json::from_slice::<RuntimeRequest>(&request_bytes) {
            Ok(request) if request.v == 1 && request.kind == "ensure_ollama" => supervisor.ensure(
                request.endpoint.unwrap_or_else(|| DEFAULT_ENDPOINT.into()),
                request.model_store.unwrap_or_else(|| env::var("OLLAMA_MODELS").unwrap_or_else(|_| DEFAULT_STORE.into())),
                request.required_models.unwrap_or_default(),
            ),
            Ok(request) if request.v == 1 && request.kind == "admit_model" => supervisor.admit_model(
                request.endpoint.unwrap_or_else(|| DEFAULT_ENDPOINT.into()),
                request.model.unwrap_or_default(),
            ),
            Ok(request) if request.v == 1 && request.kind == "release_model" => supervisor.release_model(
                request.lease_id.as_deref().unwrap_or_default(),
            ),
            Ok(request) if request.v == 1 && request.kind == "unload_model" => supervisor.unload_model(
                request.endpoint.unwrap_or_else(|| DEFAULT_ENDPOINT.into()),
                request.model.unwrap_or_default(),
            ),
            Ok(request) if request.v == 1 && request.kind == "release_idle_models" => supervisor.release_idle_models(
                request.endpoint.unwrap_or_else(|| DEFAULT_ENDPOINT.into()),
                request.force.unwrap_or(false),
                request.preserve_models.unwrap_or_default(),
            ),
            Ok(_) => RuntimeResponse::error("Unsupported runtime IPC request."),
            Err(_) => RuntimeResponse::error("Malformed runtime IPC request."),
        };
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
    for _ in 0..20 {
        unsafe {
            if let Ok(handle) = CreateFileW(PCWSTR(wide.as_ptr()), GENERIC_WRITE.0, FILE_SHARE_MODE(0), None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None) {
                let mut written = 0u32;
                let _ = WriteFile(handle, Some(b"{}\n"), Some(&mut written as *mut u32), None);
                let mut response = [0u8; 1024];
                let mut read = 0u32;
                let _ = ReadFile(handle, Some(&mut response), Some(&mut read as *mut u32), None);
                let _ = CloseHandle(handle);
                return;
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
    match reconciliation_plan(existing_runtime_healthy.is_some(), !state.inference_leases.is_empty()) {
        ReconciliationPlan::ReuseAndClearIdleModels => {
            clear_idle_resident_models(&endpoint, &state.inference_leases)?;
            let response = existing_runtime_healthy.expect("reuse plan requires a healthy runtime");
            log_line(&format!("Ariadne startup reused the healthy Rust-owned Ollama runtime after idle model-pool cleanup; state='{}'.", response.state));
            return Ok(response);
        }
        ReconciliationPlan::ReusePreservingActiveLeases => {
            let response = existing_runtime_healthy.expect("reuse plan requires a healthy runtime");
            log_line(&format!(
                "Ariadne core startup reused the healthy Rust-owned Ollama runtime and preserved active inference lease(s): {:?}.",
                state.inference_leases.values().map(|lease| lease.model.as_str()).collect::<Vec<_>>()
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
    let mut command = Command::new(&executable);
    command.arg("serve").env("OLLAMA_MODELS", &store).current_dir(executable.parent().unwrap_or(Path::new(".")))
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
    log_line(&format!("Rust host launched Ollama: pid={pid}, executable={}, store={store}", executable.display()));
    state.runtime = Some(ManagedRuntime { child, job: job.0 as isize, endpoint: endpoint.clone(), model_store: store.clone(), required_models: models.clone(), executable, version: None });
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
    use super::{reconciliation_plan, ReconciliationPlan};

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
    refuse_unknown_gpu_owners(&after)?;
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
    refuse_unknown_gpu_owners(&after)?;
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
            return refuse_unknown_gpu_owners(&snapshot);
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

fn refuse_unknown_gpu_owners(snapshot: &super::gpu_residency::GpuMemorySnapshot) -> Result<(), String> {
    let unknown: Vec<String> = snapshot.process_allocations.iter().filter(|allocation| {
        allocation.bytes >= UNKNOWN_GPU_OWNER_BLOCK_BYTES
            && gpu_owner_basename(allocation.pid)
                .is_none_or(|name| !is_known_model_runner(&name) && !is_known_system_gpu_owner(&name))
    }).map(|allocation| {
        let owner = gpu_owner_basename(allocation.pid).unwrap_or_else(|| "process name unavailable".into());
        format!("PID {} ({owner})={} MiB", allocation.pid, allocation.bytes / 1_048_576)
    }).collect();
    if unknown.is_empty() { return Ok(()); }
    Err(format!("Startup found GPU memory held by other process(es): {}. Rust host left them running and blocked model admission.", unknown.join(", ")))
}

fn prune_expired_leases(state: &mut State) {
    let expired: Vec<String> = state.inference_leases.iter()
        .filter(|(_, lease)| lease.acquired_at.elapsed() > Duration::from_secs(15 * 60))
        .map(|(id, _)| id.clone()).collect();
    for id in expired {
        if let Some(lease) = state.inference_leases.remove(&id) {
            log_line(&format!("Expired stale inference lease '{}' for '{}' after 15 minutes.", id, lease.model));
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
    leases: &std::collections::HashMap<String, String>,
) -> Result<(String, serde_json::Value, String), String> {
    if model.trim().is_empty() { return Err("Inference request did not specify a model; blocked by Rust-host preflight.".into()); }
    let model_row = ollama_tags(endpoint)?.into_iter().find(|row| row.get("name").and_then(|v| v.as_str()).is_some_and(|name| ollama_model_name_matches(model, name)))
        .ok_or_else(|| format!("Requested model '{model}' is not in Ollama's local catalogue; inference blocked."))?;
    let model_bytes = model_row.get("size").and_then(|v| v.as_u64()).unwrap_or(0);
    if model_bytes == 0 { return Err(format!("Ollama did not report a model size for '{model}'; GPU admission cannot be established.")); }
    let mut resident = ollama_models(endpoint)?;
    let target_was_resident = resident.iter().any(|row| row.get("name").and_then(|v| v.as_str()).is_some_and(|name| ollama_model_name_matches(model, name)));
    for row in &resident {
        let Some(name) = row.get("name").and_then(|v| v.as_str()) else { continue };
        if ollama_model_name_matches(model, name) { continue; }
        if leases.values().any(|leased| ollama_model_name_matches(leased, name)) {
            return Err(format!("GPU preflight found Ollama model '{name}' resident and in active use; '{model}' must wait."));
        }
    }
    for row in &resident {
        let Some(name) = row.get("name").and_then(|v| v.as_str()) else { continue };
        if !ollama_model_name_matches(model, name) { unload_model(endpoint, name)?; }
    }
    let deadline = Instant::now() + Duration::from_secs(20);
    loop {
        resident = ollama_models(endpoint)?;
        if resident.iter().all(|row| row.get("name").and_then(|v| v.as_str()).is_some_and(|name| ollama_model_name_matches(model, name))) { break; }
        if Instant::now() >= deadline {
            return Err(format!("Ollama did not release competing resident model(s) within 20 seconds: {}", resident.iter().filter_map(|r| r.get("name").and_then(|v| v.as_str()).filter(|name| !ollama_model_name_matches(model, name))).collect::<Vec<_>>().join(", ")));
        }
        thread::sleep(Duration::from_millis(400));
    }

    // Capture the actual system-wide WDDM state only after the Ollama resident
    // set has been inspected and competing idle Ollama models have been drained.
    let gpu = super::gpu_residency::sample().map_err(|error| format!("GPU residency preflight unavailable; inference blocked: {error}"))?;
    let required_bytes = model_bytes.saturating_add(1024 * 1024 * 1024);
    if !target_was_resident && gpu.free_bytes < required_bytes {
        return Err(format!("GPU residency preflight denied '{model}': {} MiB free, about {} MiB required including 1 GiB headroom; no inference was sent. WDDM allocations: {}.", gpu.free_bytes / 1_048_576, required_bytes / 1_048_576, gpu.process_allocations.iter().map(|p| format!("PID {}={} MiB", p.pid, p.bytes / 1_048_576)).collect::<Vec<_>>().join(", ")));
    }
    let inventory = serde_json::to_value(&gpu).map_err(|error| error.to_string())?;
    log_line(&format!("GPU/Ollama preflight inventory for '{model}': {} MiB free / {} MiB total; WDDM allocations [{}]; resident Ollama models [{}]", gpu.free_bytes / 1_048_576, gpu.total_bytes / 1_048_576, allocation_summary(&gpu), resident.iter().filter_map(|row| row.get("name").and_then(|v| v.as_str())).collect::<Vec<_>>().join(", ")));
    let lease_id = format!("{}-{}", std::process::id(), uuid_like());
    let detail = format!("Rust-host GPU preflight admitted '{model}' ({} MiB free, {} MiB model, 1024 MiB headroom); {}.", gpu.free_bytes / 1_048_576, model_bytes / 1_048_576, if target_was_resident { "reusing resident model" } else { "prior Ollama models unloaded and model may load" });
    Ok((lease_id, inventory, detail))
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
    use super::ollama_model_name_matches;

    #[test]
    fn untagged_requested_model_matches_ollama_latest_alias() {
        assert!(ollama_model_name_matches("nomic-embed-text", "nomic-embed-text:latest"));
        assert!(ollama_model_name_matches("nomic-embed-text", "nomic-embed-text"));
        assert!(!ollama_model_name_matches("nomic-embed-text:latest", "nomic-embed-text"));
        assert!(!ollama_model_name_matches("nomic-embed-text", "nomic-embed-text:other"));
    }
}

fn unload_model(endpoint: &str, model: &str) -> Result<(), String> {
    let body = serde_json::json!({"model": model, "keep_alive": 0, "stream": false});
    let _ = ollama_http(endpoint, "POST", "/api/generate", Some(&body))?;
    log_line(&format!("Rust-host preflight unloaded idle Ollama model '{model}' before admitting another workload"));
    Ok(())
}

fn ollama_http(endpoint: &str, method: &str, path: &str, body: Option<&serde_json::Value>) -> Result<serde_json::Value, String> {
    let address = endpoint_address(endpoint)?;
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(2)).map_err(|error| format!("Ollama preflight connection failed: {error}"))?;
    stream.set_read_timeout(Some(Duration::from_secs(5))).map_err(|error| error.to_string())?;
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
