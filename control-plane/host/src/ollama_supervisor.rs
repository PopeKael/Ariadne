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

#[derive(Debug, Deserialize)]
struct RuntimeRequest {
    v: u32,
    #[serde(rename = "type")]
    kind: String,
    endpoint: Option<String>,
    model_store: Option<String>,
    required_models: Option<Vec<String>>,
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
}

impl RuntimeResponse {
    fn error(detail: impl Into<String>) -> Self {
        Self { v: 1, ok: false, state: "error".into(), detail: detail.into(), pid: None, version: None, models: Vec::new() }
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
                RuntimeResponse::error(error)
            }
        }
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
    if installer_running()? { return Err("Ollama update installer is active; reconciliation is waiting for it to finish.".into()); }
    if let Some(runtime) = state.runtime.as_mut() {
        if runtime.child.try_wait().map_err(|error| error.to_string())?.is_none()
            && runtime.endpoint == endpoint && runtime.model_store == store && runtime.required_models == models
        {
            if let Ok(response) = runtime_health(runtime) { return Ok(response); }
        }
    }
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
    Ok(RuntimeResponse { v: 1, ok: true, state: "online".into(), detail: format!("Rust host owns Ollama PID {} at {}; required catalogue verified.", runtime.child.id(), runtime.executable.display()), pid: Some(runtime.child.id()), version, models })
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
    authority.to_socket_addrs().map_err(|error| error.to_string())?.next().ok_or("Ollama endpoint did not resolve".into())
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
