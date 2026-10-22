//! Host-owned lifecycle for the read-only Knowledge Vault MCP server and its
//! optional OpenAI Secure MCP Tunnel client.

use serde_json::Value;
use std::env;
use std::ffi::c_void;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::os::windows::io::AsRawHandle;
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};
use windows::core::PCWSTR;
use windows::Win32::Foundation::{CloseHandle, HANDLE};
use windows::Win32::Security::Credentials::{
    CredFree, CredReadW, CredWriteW, CREDENTIALW, CRED_PERSIST_LOCAL_MACHINE, CRED_TYPE_GENERIC,
};
use windows::Win32::Security::Cryptography::{BCryptGenRandom, BCRYPT_USE_SYSTEM_PREFERRED_RNG};
use windows::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};

const MCP_TOKEN_TARGET: &str = "Ariadne/KnowledgeVault/MCP_BEARER_TOKEN";
const TUNNEL_KEY_TARGET: &str = "Ariadne/KnowledgeVault/OpenAI_TUNNEL_API_KEY";
const MCP_HEALTH_INTERVAL: Duration = Duration::from_secs(2);
const STARTUP_GRACE: Duration = Duration::from_secs(90);
const MCP_LIVENESS_TIMEOUT: Duration = Duration::from_millis(500);
const DIAGNOSTIC_MAX_BYTES: u64 = 1024 * 1024;
const DIAGNOSTIC_MAX_LINE_BYTES: usize = 16 * 1024;
static DIAGNOSTIC_LOCK: OnceLock<Mutex<()>> = OnceLock::new();
#[cfg(test)]
static TEST_KILL_MCP_CHILD: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

#[derive(Clone, Debug, PartialEq, Eq)]
struct ServiceConfig {
    vault_root: PathBuf,
    control_plane: PathBuf,
    python: PathBuf,
    mcp_script: PathBuf,
    tunnel_client: PathBuf,
    tunnel_id: Option<String>,
    tunnel_key: Option<String>,
    embedding_model: String,
    embedding_context_tokens: String,
}

pub struct ManagedMcpServices {
    stop: Arc<AtomicBool>,
    join: Option<JoinHandle<()>>,
}

impl ManagedMcpServices {
    pub fn start(project_root: PathBuf) -> Self {
        let stop = Arc::new(AtomicBool::new(false));
        let thread_stop = stop.clone();
        let join = thread::spawn(move || supervise(project_root, thread_stop));
        Self {
            stop,
            join: Some(join),
        }
    }

    pub fn stop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(join) = self.join.take() {
            let _ = join.join();
        }
    }
}

impl Drop for ManagedMcpServices {
    fn drop(&mut self) {
        self.stop();
    }
}

struct ProcessSlot {
    child: Child,
    job: HANDLE,
    started: Instant,
    healthy: bool,
    consecutive_failures: u8,
    next_check: Instant,
    log_readers: Vec<JoinHandle<()>>,
}

#[derive(Clone)]
struct DiagnosticSink {
    path: PathBuf,
    secrets: Arc<Vec<String>>,
}

impl DiagnosticSink {
    fn new(name: &str, secrets: impl IntoIterator<Item = String>) -> Option<Self> {
        let local = env::var_os("LOCALAPPDATA")?;
        Some(Self {
            path: PathBuf::from(local)
                .join("Ariadne")
                .join("logs")
                .join(name),
            secrets: Arc::new(secrets.into_iter().filter(|value| !value.is_empty()).collect()),
        })
    }

    fn write_line(&self, line: &str) {
        let mut safe = line.to_owned();
        for secret in self.secrets.iter() {
            safe = safe.replace(secret, "[redacted]");
        }
        let mut bounded = String::new();
        for character in safe.chars() {
            if bounded.len() + character.len_utf8() > DIAGNOSTIC_MAX_LINE_BYTES {
                break;
            }
            bounded.push(character);
        }
        let safe = bounded;
        let _guard = DIAGNOSTIC_LOCK
            .get_or_init(|| Mutex::new(()))
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        let Some(parent) = self.path.parent() else {
            return;
        };
        if std::fs::create_dir_all(parent).is_err() {
            return;
        }
        let current = std::fs::metadata(&self.path).map_or(0, |metadata| metadata.len());
        if current.saturating_add(safe.len() as u64 + 1) > DIAGNOSTIC_MAX_BYTES {
            let previous = self.path.with_extension("log.1");
            let _ = std::fs::remove_file(&previous);
            if std::fs::rename(&self.path, previous).is_err() {
                let _ = std::fs::OpenOptions::new()
                    .write(true)
                    .truncate(true)
                    .open(&self.path);
            }
        }
        if let Ok(mut file) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
        {
            let _ = writeln!(file, "{safe}");
        }
    }
}

struct RetryState {
    attempts: u32,
    retry_at: Instant,
}

impl Default for RetryState {
    fn default() -> Self {
        Self {
            attempts: 0,
            retry_at: Instant::now(),
        }
    }
}

impl RetryState {
    fn failed(&mut self, name: &str, error: &str) {
        self.attempts = self.attempts.saturating_add(1);
        let delay = retry_delay(self.attempts);
        self.retry_at = Instant::now() + delay;
        let message = format!(
            "{name} start/recovery failed; retry {} in {}s: {error}",
            self.attempts,
            delay.as_secs()
        );
        if name.contains("Tunnel") {
            tunnel_log(&message);
        } else {
            mcp_log(&message);
        }
    }
    fn succeeded(&mut self) {
        self.attempts = 0;
        self.retry_at = Instant::now();
    }
}

fn retry_delay(attempt: u32) -> Duration {
    Duration::from_secs(
        2u64.saturating_pow(attempt.saturating_sub(1).min(6))
            .min(60),
    )
}

fn mcp_log(message: &str) {
    if let Some(sink) = DiagnosticSink::new("knowledge-vault-mcp.log", std::iter::empty()) {
        sink.write_line(&format!("[managed-mcp] {message}"));
    }
}

fn tunnel_log(message: &str) {
    if let Some(sink) = DiagnosticSink::new("secure-mcp-tunnel.log", std::iter::empty()) {
        sink.write_line(&format!("[managed-tunnel] {message}"));
    }
}

fn configuration_path() -> PathBuf {
    if let Some(value) = env::var_os("ARIADNE_CONFIG_PATH") {
        return PathBuf::from(value);
    }
    env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            env::var_os("USERPROFILE")
                .map(PathBuf::from)
                .unwrap_or_default()
        })
        .join("Ariadne")
        .join("configuration.json")
}

fn read_config() -> Value {
    std::fs::read(configuration_path())
        .ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or(Value::Null)
}

fn service_config(project_root: &Path) -> Result<ServiceConfig, String> {
    let saved = read_config();
    let vault_root = env::var_os("ARIADNE_VAULT_ROOT")
        .map(PathBuf::from)
        .or_else(|| {
            saved
                .pointer("/storage/knowledge_vault")
                .and_then(Value::as_str)
                .map(PathBuf::from)
        })
        .unwrap_or_else(|| PathBuf::from(r"D:\Downloads\KnowledgeVault"));
    let mcp_script = vault_root.join("00_System").join("ariadne_mcp_http.py");
    let python = env::var_os("ARIADNE_MCP_PYTHON")
        .map(PathBuf::from)
        .unwrap_or_else(|| vault_root.join(".venv").join("Scripts").join("python.exe"));
    let tunnel_client = env::var_os("ARIADNE_TUNNEL_CLIENT_PATH")
        .map(PathBuf::from)
        .or_else(|| {
            saved
                .pointer("/knowledge_vault_mcp/tunnel_client_path")
                .and_then(Value::as_str)
                .map(PathBuf::from)
        })
        .unwrap_or_else(|| {
            PathBuf::from(r"D:\Downloads\tunnel-client-v0.0.16-windows-amd64\tunnel-client.exe")
        });
    let tunnel_id = env::var("ARIADNE_MCP_TUNNEL_ID")
        .ok()
        .or_else(|| {
            saved
                .pointer("/knowledge_vault_mcp/tunnel_id")
                .and_then(Value::as_str)
                .map(str::to_owned)
        })
        .filter(|value| valid_tunnel_id(value));
    let tunnel_key = match credential_read(TUNNEL_KEY_TARGET) {
        Ok(value) => value,
        Err(error) => {
            mcp_log(&format!(
                "could not read the Secure MCP Tunnel runtime credential: {error}"
            ));
            None
        }
    };
    let embedding_model = env::var("ARIADNE_EMBEDDING_MODEL")
        .ok()
        .or_else(|| saved.pointer("/vault_runtime/embedding_model").and_then(Value::as_str).map(str::to_owned))
        .unwrap_or_else(|| "nomic-embed-text".into());
    let embedding_context_tokens = env::var("ARIADNE_EMBEDDING_CONTEXT")
        .ok()
        .or_else(|| saved.pointer("/vault_runtime/embedding_context_tokens").and_then(Value::as_u64).map(|value| value.to_string()))
        .unwrap_or_else(|| "2048".into());
    if !mcp_script.is_file() {
        return Err(format!(
            "Knowledge Vault MCP server was not found at {}",
            mcp_script.display()
        ));
    }
    if !python.is_file() {
        return Err(format!(
            "Knowledge Vault Python environment was not found at {}",
            python.display()
        ));
    }
    // Python MCP inference is admitted through this Ariadne install's Rust Host.
    let control_plane = project_root.join("control-plane");
    if !control_plane.join("ollama_runtime.py").is_file() {
        return Err(format!("Ariadne Rust-host inference adapter was not found under {}", control_plane.display()));
    }
    Ok(ServiceConfig {
        vault_root,
        control_plane,
        python,
        mcp_script,
        tunnel_client,
        tunnel_id,
        tunnel_key,
        embedding_model,
        embedding_context_tokens,
    })
}

fn valid_tunnel_id(value: &str) -> bool {
    let Some(suffix) = value.strip_prefix("tunnel_") else {
        return false;
    };
    let (namespace, id) = if suffix.len() == 32 {
        (None, suffix)
    } else if suffix.len() == 37 && suffix.as_bytes().get(4) == Some(&b'_') {
        let Some(id) = suffix.get(5..) else {
            return false;
        };
        (suffix.get(..4), id)
    } else {
        return false;
    };
    namespace.is_none_or(|value| {
        value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit())
    }) && id.len() == 32
        && id
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit())
}

fn supervise(project_root: PathBuf, stop: Arc<AtomicBool>) {
    let mut credential_attempt = 0;
    let bearer = loop {
        if stop.load(Ordering::Acquire) {
            return;
        }
        let loaded = match credential_read(MCP_TOKEN_TARGET) {
            Ok(Some(value)) if value.len() >= 32 => Ok(value),
            Ok(_) => create_and_store_token().map(|value| {
                mcp_log("created the local MCP bearer credential in Windows Credential Manager");
                value
            }),
            Err(error) => Err(error),
        };
        match loaded {
            Ok(value) => break value,
            Err(error) => {
                credential_attempt += 1;
                let delay = retry_delay(credential_attempt);
                mcp_log(&format!("MCP supervisor could not load or create its Credential Manager token; retrying in {}s: {error}", delay.as_secs()));
                pause_or_stop(&stop, delay);
            }
        }
    };
    let mut mcp: Option<ProcessSlot> = None;
    let mut tunnel: Option<ProcessSlot> = None;
    let mut mcp_retry = RetryState::default();
    let mut tunnel_retry = RetryState::default();
    let mut next_config_read = Instant::now();
    let mut config: Option<ServiceConfig> = None;
    mcp_log("Rust Host MCP lifecycle supervisor started; MCP binds only to 127.0.0.1:8790");

    while !stop.load(Ordering::Acquire) {
        #[cfg(test)]
        if TEST_KILL_MCP_CHILD.swap(false, Ordering::AcqRel) {
            if let Some(slot) = mcp.as_mut() {
                let _ = slot.child.kill();
            }
        }
        if Instant::now() >= next_config_read {
            match service_config(&project_root) {
                Ok(value) => {
                    let changed = config.as_ref().is_some_and(|previous| previous != &value);
                    if changed {
                        stop_slot(&mut tunnel, "Tunnel configuration changed");
                        stop_slot(&mut mcp, "Knowledge Vault MCP configuration changed");
                        mcp_retry = RetryState::default();
                        tunnel_retry = RetryState::default();
                        mcp_log("Knowledge Vault runtime configuration changed; restarting managed MCP workers with the saved settings");
                    }
                    config = Some(value);
                }
                Err(error) => mcp_log(&error),
            }
            next_config_read = Instant::now() + Duration::from_secs(10);
        }
        let Some(current) = config.as_ref() else {
            pause_or_stop(&stop, Duration::from_secs(1));
            continue;
        };

        if mcp
            .as_mut()
            .is_some_and(|slot| slot.child.try_wait().ok().flatten().is_some())
        {
            stop_slot(&mut tunnel, "Tunnel client stopped because MCP exited");
            stop_slot(&mut mcp, "Knowledge Vault MCP child exited");
            mcp_retry.failed("Knowledge Vault MCP", "child process exited");
            tunnel_retry = RetryState::default();
        }
        if mcp.is_none() && Instant::now() >= mcp_retry.retry_at {
            match spawn_mcp(current, &bearer) {
                Ok(slot) => {
                    mcp_log(&format!(
                        "started Knowledge Vault MCP (pid {})",
                        slot.child.id()
                    ));
                    mcp = Some(slot);
                }
                Err(error) => mcp_retry.failed("Knowledge Vault MCP", &error),
            }
        }
        if let Some(slot) = mcp.as_mut() {
            if Instant::now() >= slot.next_check {
                slot.next_check = Instant::now() + MCP_HEALTH_INTERVAL;
                if mcp_liveness_check() {
                    slot.consecutive_failures = 0;
                    if !slot.healthy {
                        mcp_log("Knowledge Vault MCP liveness check passed");
                    }
                    slot.healthy = true;
                    mcp_retry.succeeded();
                } else {
                    slot.consecutive_failures = slot.consecutive_failures.saturating_add(1);
                    let expired = slot.started.elapsed() >= STARTUP_GRACE;
                    if (slot.healthy && slot.consecutive_failures >= 3) || expired {
                        mcp_log("Knowledge Vault MCP liveness check failed; restarting the managed process");
                        stop_slot(&mut tunnel, "Tunnel client stopped while MCP is recovering");
                        stop_slot(&mut mcp, "Knowledge Vault MCP health failure");
                        mcp_retry.failed("Knowledge Vault MCP", "health check did not pass");
                        tunnel_retry = RetryState::default();
                    }
                }
            }
        }

        let mcp_healthy = mcp.as_ref().is_some_and(|slot| slot.healthy);
        let tunnel_configured = current.tunnel_id.is_some()
            && current.tunnel_key.is_some()
            && current.tunnel_client.is_file();
        if mcp_healthy && !tunnel_configured && tunnel.is_none() {
            // Report once per config-read interval; never include secret material.
            if Instant::now() >= next_config_read - Duration::from_secs(9) {
                tunnel_log("Tunnel client is waiting for a tunnel ID, OpenAI runtime key, and v0.0.16 executable");
            }
        }
        if mcp_healthy && tunnel_configured {
            if tunnel
                .as_mut()
                .is_some_and(|slot| slot.child.try_wait().ok().flatten().is_some())
            {
                stop_slot(&mut tunnel, "Secure MCP Tunnel child exited");
                tunnel_retry.failed("Secure MCP Tunnel client", "child process exited");
            }
            if tunnel.is_none() && Instant::now() >= tunnel_retry.retry_at {
                match spawn_tunnel(current, &bearer) {
                    Ok(slot) => {
                        tunnel_log(&format!(
                            "started Secure MCP Tunnel client (pid {})",
                            slot.child.id()
                        ));
                        tunnel = Some(slot);
                    }
                    Err(error) => tunnel_retry.failed("Secure MCP Tunnel client", &error),
                }
            }
            if let Some(slot) = tunnel.as_mut() {
                if Instant::now() >= slot.next_check {
                    slot.next_check = Instant::now() + MCP_HEALTH_INTERVAL;
                    if tunnel_health_check() {
                        slot.consecutive_failures = 0;
                        if !slot.healthy {
                            tunnel_log("Secure MCP Tunnel client is ready");
                        }
                        slot.healthy = true;
                        tunnel_retry.succeeded();
                    } else {
                        slot.consecutive_failures = slot.consecutive_failures.saturating_add(1);
                        if (slot.healthy && slot.consecutive_failures >= 3)
                            || slot.started.elapsed() >= STARTUP_GRACE
                        {
                            stop_slot(
                                &mut tunnel,
                                "Secure MCP Tunnel client failed readiness checks",
                            );
                            tunnel_retry
                                .failed("Secure MCP Tunnel client", "readiness check did not pass");
                        }
                    }
                }
            }
        } else if tunnel.is_some() {
            stop_slot(
                &mut tunnel,
                "Tunnel client stopped because MCP is unavailable or configuration was removed",
            );
        }
        pause_or_stop(&stop, Duration::from_millis(500));
    }
    stop_slot(&mut tunnel, "Ariadne Host shutdown");
    stop_slot(&mut mcp, "Ariadne Host shutdown");
    mcp_log("Rust Host MCP services stopped cleanly");
}

fn pause_or_stop(stop: &AtomicBool, duration: Duration) {
    let deadline = Instant::now() + duration;
    while !stop.load(Ordering::Acquire) && Instant::now() < deadline {
        thread::sleep(Duration::from_millis(100));
    }
}

fn spawn_mcp(config: &ServiceConfig, bearer: &str) -> Result<ProcessSlot, String> {
    let mut command = Command::new(&config.python);
    command
        .arg(&config.mcp_script)
        .current_dir(&config.vault_root)
        .env("ARIADNE_MCP_HOST", "127.0.0.1")
        .env("ARIADNE_MCP_PORT", mcp_port().to_string())
        .env("ARIADNE_MCP_PATH", "/mcp")
        .env("ARIADNE_CONTROL_PLANE", &config.control_plane)
        .env("ARIADNE_EMBEDDING_MODEL", &config.embedding_model)
        .env("ARIADNE_EMBEDDING_CONTEXT", &config.embedding_context_tokens)
        .env("ARIADNE_MCP_BEARER_TOKEN", bearer)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .creation_flags(0x08000000);
    let mut child = command
        .spawn()
        .map_err(|error| format!("could not launch {}: {error}", config.mcp_script.display()))?;
    let job = match create_service_job(&child) {
        Ok(value) => value,
        Err(error) => {
            let mut child = child;
            let _ = child.kill();
            let _ = child.wait();
            return Err(error);
        }
    };
    #[cfg(test)]
    if let Some(path) = env::var_os("ARIADNE_TEST_MCP_PID_FILE") {
        let _ = std::fs::write(path, child.id().to_string());
    }
    let mut log_readers = Vec::new();
    if let Some(stderr) = child.stderr.take() {
        let sink = DiagnosticSink::new(
            "knowledge-vault-mcp.log",
            [bearer.to_owned()],
        );
        log_readers.push(spawn_log_reader(stderr, sink));
    }
    Ok(ProcessSlot {
        child,
        job,
        started: Instant::now(),
        healthy: false,
        consecutive_failures: 0,
        next_check: Instant::now(),
        log_readers,
    })
}

fn spawn_log_reader(
    reader: impl Read + Send + 'static,
    sink: Option<DiagnosticSink>,
) -> JoinHandle<()> {
    thread::spawn(move || {
        let mut reader = BufReader::new(reader);
        let mut bytes = Vec::new();
        loop {
            bytes.clear();
            match reader.read_until(b'\n', &mut bytes) {
                Ok(0) | Err(_) => break,
                Ok(_) => {
                    while matches!(bytes.last(), Some(b'\n' | b'\r')) {
                        bytes.pop();
                    }
                    if let Some(sink) = &sink {
                        sink.write_line(&String::from_utf8_lossy(&bytes));
                    }
                }
            }
        }
    })
}

fn spawn_tunnel(config: &ServiceConfig, bearer: &str) -> Result<ProcessSlot, String> {
    let tunnel_id = config
        .tunnel_id
        .as_deref()
        .ok_or("tunnel ID is not configured")?;
    let api_key = config
        .tunnel_key
        .as_deref()
        .ok_or("OpenAI runtime key is not saved in Windows Credential Manager")?;
    let mut command = Command::new(&config.tunnel_client);
    command
        .args([
            "run",
            "--control-plane.tunnel-id",
            tunnel_id,
            "--control-plane.api-key",
            "env:ARIADNE_TUNNEL_CONTROL_API_KEY",
            "--mcp.server-url",
            "url=http://127.0.0.1:8790/mcp,channel=main",
            "--mcp.extra-headers",
            "Authorization: env:ARIADNE_TUNNEL_MCP_AUTHORIZATION",
            "--health.listen-addr",
            "127.0.0.1:8791",
            "--log.level",
            "info",
            "--log.format",
            "json",
        ])
        .env("ARIADNE_TUNNEL_CONTROL_API_KEY", api_key)
        .env(
            "ARIADNE_TUNNEL_MCP_AUTHORIZATION",
            format!("Bearer {bearer}"),
        )
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .creation_flags(0x08000000);
    let mut child = command.spawn().map_err(|error| {
        format!(
            "could not launch {}: {error}",
            config.tunnel_client.display()
        )
    })?;
    let job = match create_service_job(&child) {
        Ok(value) => value,
        Err(error) => {
            let mut child = child;
            let _ = child.kill();
            let _ = child.wait();
            return Err(error);
        }
    };
    let secrets = [bearer.to_owned(), api_key.to_owned(), tunnel_id.to_owned()];
    let mut log_readers = Vec::new();
    if let Some(stdout) = child.stdout.take() {
        let sink = DiagnosticSink::new("secure-mcp-tunnel.log", secrets.clone());
        log_readers.push(spawn_log_reader(stdout, sink));
    }
    if let Some(stderr) = child.stderr.take() {
        let sink = DiagnosticSink::new("secure-mcp-tunnel.log", secrets);
        log_readers.push(spawn_log_reader(stderr, sink));
    }
    Ok(ProcessSlot {
        child,
        job,
        started: Instant::now(),
        healthy: false,
        consecutive_failures: 0,
        next_check: Instant::now(),
        log_readers,
    })
}

fn stop_slot(slot: &mut Option<ProcessSlot>, reason: &str) {
    let Some(mut current) = slot.take() else {
        return;
    };
    if reason.contains("Tunnel") || reason.contains("tunnel") {
        tunnel_log(&format!("stopping managed process: {reason}"));
    } else {
        mcp_log(&format!("stopping managed process: {reason}"));
    }
    unsafe {
        let _ = windows::Win32::System::JobObjects::TerminateJobObject(current.job, 1);
    }
    if current.child.try_wait().ok().flatten().is_none() {
        let _ = current.child.kill();
        let _ = current.child.wait();
    }
    for reader in current.log_readers.drain(..) {
        let _ = reader.join();
    }
    unsafe {
        let _ = CloseHandle(current.job);
    }
}

fn create_service_job(child: &Child) -> Result<HANDLE, String> {
    unsafe {
        let job = CreateJobObjectW(None, PCWSTR::null()).map_err(|error| error.to_string())?;
        let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if let Err(error) = SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &limits as *const _ as *const c_void,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        ) {
            let _ = CloseHandle(job);
            return Err(format!(
                "could not configure managed-service Job Object: {error}"
            ));
        }
        let process = HANDLE(child.as_raw_handle() as *mut c_void);
        if let Err(error) = AssignProcessToJobObject(job, process) {
            let _ = CloseHandle(job);
            return Err(format!(
                "could not assign service to lifecycle Job Object: {error}"
            ));
        }
        Ok(job)
    }
}

fn mcp_liveness_check() -> bool {
    tcp_liveness_check(&format!("127.0.0.1:{}", mcp_port()))
}

fn mcp_port() -> u16 {
    #[cfg(test)]
    if let Some(port) = env::var("ARIADNE_TEST_MCP_PORT")
        .ok()
        .and_then(|value| value.parse().ok())
    {
        return port;
    }
    8790
}

fn tcp_liveness_check(address: &str) -> bool {
    let Ok(mut addresses) = address.to_socket_addrs() else {
        return false;
    };
    let Some(address) = addresses.next() else {
        return false;
    };
    TcpStream::connect_timeout(&address, MCP_LIVENESS_TIMEOUT).is_ok()
}

fn tunnel_health_check() -> bool {
    http_get_status("127.0.0.1:8791", "/readyz")
}

fn http_get_status(address: &str, path: &str) -> bool {
    let Ok(mut addresses) = address.to_socket_addrs() else {
        return false;
    };
    let Some(address) = addresses.next() else {
        return false;
    };
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(500)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    if stream
        .write_all(
            format!("GET {path} HTTP/1.1\r\nHost: 127.0.0.1:8791\r\nConnection: close\r\n\r\n")
                .as_bytes(),
        )
        .is_err()
    {
        return false;
    }
    read_http_status(stream) == Some(200)
}

#[cfg(test)]
fn http_post_status_code(
    address: &str,
    path: &str,
    body: &str,
    authorization: Option<&str>,
) -> Option<u16> {
    let Ok(mut addresses) = address.to_socket_addrs() else {
        return None;
    };
    let Some(address) = addresses.next() else {
        return None;
    };
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(500)) else {
        return None;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(1)));
    let auth = authorization
        .map(|value| format!("Authorization: {value}\r\n"))
        .unwrap_or_default();
    let request = format!("POST {path} HTTP/1.1\r\nHost: 127.0.0.1:8790\r\nContent-Type: application/json\r\nAccept: application/json, text/event-stream\r\n{auth}Content-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len());
    stream.write_all(request.as_bytes()).ok()?;
    read_http_status(stream)
}

#[cfg(test)]
fn http_post_response(
    address: &str,
    body: &str,
    authorization: Option<&str>,
) -> Option<(u16, String)> {
    let address = address.to_socket_addrs().ok()?.next()?;
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_millis(500)).ok()?;
    let _ = stream.set_read_timeout(Some(Duration::from_secs(130)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
    let auth = authorization
        .map(|value| format!("Authorization: {value}\r\n"))
        .unwrap_or_default();
    let request = format!(
        "POST /mcp HTTP/1.1\r\nHost: {address}\r\nContent-Type: application/json\r\nAccept: application/json, text/event-stream\r\n{auth}Content-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    stream.write_all(request.as_bytes()).ok()?;
    let mut response = Vec::new();
    stream.read_to_end(&mut response).ok()?;
    let response = String::from_utf8_lossy(&response).into_owned();
    let status = response
        .lines()
        .next()?
        .strip_prefix("HTTP/1.")?
        .split_whitespace()
        .nth(1)?
        .parse()
        .ok()?;
    Some((status, response))
}

fn read_http_status(stream: TcpStream) -> Option<u16> {
    let mut reader = BufReader::new(stream);
    let mut status_line = String::new();
    reader.read_line(&mut status_line).ok()?;
    status_line.strip_prefix("HTTP/1.")?;
    status_line.split_whitespace().nth(1)?.parse().ok()
}

fn credential_read(target: &str) -> Result<Option<String>, String> {
    #[cfg(test)]
    if target == MCP_TOKEN_TARGET {
        if let Ok(value) = env::var("ARIADNE_TEST_MCP_BEARER_TOKEN") {
            return Ok(Some(value));
        }
    }
    let target_wide: Vec<u16> = target.encode_utf16().chain(Some(0)).collect();
    let mut credential = std::ptr::null_mut();
    unsafe {
        if let Err(error) = CredReadW(
            PCWSTR(target_wide.as_ptr()),
            CRED_TYPE_GENERIC,
            None,
            &mut credential,
        ) {
            if error.code().0 as u32 == 0x80070490 {
                return Ok(None);
            }
            return Err(format!("Windows Credential Manager read failed: {error}"));
        }
        let item = &*credential;
        let bytes =
            std::slice::from_raw_parts(item.CredentialBlob, item.CredentialBlobSize as usize);
        let value = String::from_utf8(bytes.to_vec())
            .map_err(|_| "credential is not valid UTF-8".to_string());
        CredFree(credential as *const c_void);
        value.map(Some)
    }
}

fn credential_write(target: &str, value: &[u8]) -> Result<(), String> {
    let target_wide: Vec<u16> = target.encode_utf16().chain(Some(0)).collect();
    let comment: Vec<u16> = "Ariadne Knowledge Vault MCP credential"
        .encode_utf16()
        .chain(Some(0))
        .collect();
    let username: Vec<u16> = "Ariadne".encode_utf16().chain(Some(0)).collect();
    let mut bytes = value.to_vec();
    let credential = CREDENTIALW {
        Type: CRED_TYPE_GENERIC,
        TargetName: windows::core::PWSTR(target_wide.as_ptr() as *mut u16),
        Comment: windows::core::PWSTR(comment.as_ptr() as *mut u16),
        CredentialBlobSize: bytes.len() as u32,
        CredentialBlob: bytes.as_mut_ptr(),
        Persist: CRED_PERSIST_LOCAL_MACHINE,
        UserName: windows::core::PWSTR(username.as_ptr() as *mut u16),
        ..Default::default()
    };
    unsafe {
        CredWriteW(&credential, 0)
            .map_err(|error| format!("Windows Credential Manager write failed: {error}"))
    }
}

fn create_and_store_token() -> Result<String, String> {
    let mut random = [0u8; 48];
    let status = unsafe { BCryptGenRandom(None, &mut random, BCRYPT_USE_SYSTEM_PREFERRED_RNG) };
    if status.0 < 0 {
        return Err(format!(
            "Windows secure random generation failed: {:#x}",
            status.0
        ));
    }
    let token = random
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect::<String>();
    credential_write(MCP_TOKEN_TARGET, token.as_bytes())?;
    Ok(token)
}

#[cfg(test)]
mod tests {
    use super::{
        http_post_response, http_post_status_code, mcp_liveness_check, retry_delay,
        tcp_liveness_check, valid_tunnel_id, DiagnosticSink, ManagedMcpServices, DIAGNOSTIC_MAX_BYTES,
        DIAGNOSTIC_MAX_LINE_BYTES, TEST_KILL_MCP_CHILD,
    };
    use serde_json::json;
    use std::env;
    use std::fs;
    use std::io::Write;
    use std::net::{TcpListener, TcpStream};
    use std::path::PathBuf;
    use std::sync::atomic::Ordering;
    use std::sync::Arc;
    use std::thread;
    use std::time::{Duration, Instant};

    #[test]
    fn retry_backoff_is_bounded_and_exponential() {
        assert_eq!(retry_delay(1), Duration::from_secs(1));
        assert_eq!(retry_delay(2), Duration::from_secs(2));
        assert_eq!(retry_delay(7), Duration::from_secs(60));
        assert_eq!(retry_delay(50), Duration::from_secs(60));
    }

    #[test]
    fn tunnel_id_validation_accepts_openai_id_shapes() {
        assert!(valid_tunnel_id("tunnel_0123456789abcdef0123456789abcdef"));
        assert!(valid_tunnel_id(
            "tunnel_ab12_0123456789abcdef0123456789abcdef"
        ));
        assert!(!valid_tunnel_id("tunnel_short"));
        assert!(!valid_tunnel_id(
            "tunnel_AB12_0123456789abcdef0123456789abcdef"
        ));
    }

    #[test]
    fn mcp_liveness_survives_a_slow_active_request() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap().to_string();
        let (accepted_tx, accepted_rx) = std::sync::mpsc::channel();
        let server = thread::spawn(move || {
            let (_slow_request, _) = listener.accept().unwrap();
            accepted_tx.send(()).unwrap();
            thread::sleep(Duration::from_secs(7));
        });

        let _slow_request = TcpStream::connect(&address).unwrap();
        accepted_rx.recv_timeout(Duration::from_secs(1)).unwrap();
        for _ in 0..3 {
            thread::sleep(Duration::from_secs(2));
            let started = Instant::now();
            assert!(tcp_liveness_check(&address));
            assert!(started.elapsed() < Duration::from_millis(500));
        }
        server.join().unwrap();
    }

    #[test]
    fn diagnostics_rotate_with_secrets_scrubbed_and_lines_bounded() {
        let root = env::temp_dir().join(format!(
            "ariadne-mcp-diagnostics-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&root).unwrap();
        let path = root.join("knowledge-vault-mcp.log");
        let sink = DiagnosticSink {
            path: path.clone(),
            secrets: Arc::new(vec![
                "test-bearer-secret".to_owned(),
                "test-tunnel-api-key".to_owned(),
                "test-tunnel-id".to_owned(),
            ]),
        };
        let line = format!(
            "Bearer test-bearer-secret API test-tunnel-api-key ID test-tunnel-id {}",
            "x".repeat(DIAGNOSTIC_MAX_LINE_BYTES * 2)
        );
        for _ in 0..100 {
            sink.write_line(&line);
        }
        let current = fs::read_to_string(&path).unwrap();
        assert!(!current.contains("test-bearer-secret"));
        assert!(!current.contains("test-tunnel-api-key"));
        assert!(!current.contains("test-tunnel-id"));
        assert!(current.lines().all(|entry| entry.len() <= DIAGNOSTIC_MAX_LINE_BYTES));
        assert!(fs::metadata(&path).unwrap().len() <= DIAGNOSTIC_MAX_BYTES);
        let previous = path.with_extension("log.1");
        if previous.exists() {
            let previous_content = fs::read_to_string(&previous).unwrap();
            assert!(!previous_content.contains("test-bearer-secret"));
            assert!(!previous_content.contains("test-tunnel-api-key"));
            assert!(!previous_content.contains("test-tunnel-id"));
            assert!(fs::metadata(previous).unwrap().len() <= DIAGNOSTIC_MAX_BYTES);
        }
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    #[ignore = "live Windows acceptance test; requires the local Knowledge Vault Python environment"]
    fn managed_mcp_starts_recovers_stops_and_rejects_unauthorized_requests() {
        let vault = PathBuf::from(r"D:\Downloads\KnowledgeVault");
        let python = vault.join(r".venv\Scripts\python.exe");
        assert!(
            python.is_file(),
            "Knowledge Vault Python environment is unavailable"
        );
        let port_listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = port_listener.local_addr().unwrap().port();
        drop(port_listener);

        let suffix = format!(
            "{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        );
        let test_root = env::temp_dir().join(format!("ariadne-managed-mcp-{suffix}"));
        fs::create_dir_all(&test_root).unwrap();
        let config_path = test_root.join("configuration.json");
        env::set_var("LOCALAPPDATA", &test_root);
        fs::write(
            &config_path,
            json!({ "storage": { "knowledge_vault": vault } }).to_string(),
        )
        .unwrap();

        env::set_var("ARIADNE_CONFIG_PATH", &config_path);
        env::set_var("ARIADNE_VAULT_ROOT", &vault);
        env::set_var("ARIADNE_MCP_PYTHON", &python);
        env::set_var("ARIADNE_TEST_MCP_PORT", port.to_string());
        env::remove_var("ARIADNE_MCP_TUNNEL_ID");

        let bearer = format!("ariadne-test-token-{}-{}", std::process::id(), suffix);
        env::set_var("ARIADNE_TEST_MCP_BEARER_TOKEN", &bearer);
        let pid_file = test_root.join("mcp-child.pid");
        env::set_var("ARIADNE_TEST_MCP_PID_FILE", &pid_file);
        let mut services = ManagedMcpServices::start(PathBuf::from(r"D:\Downloads\Ariadne"));

        assert!(
            wait_until(Duration::from_secs(40), mcp_liveness_check),
            "MCP failed to become live on the isolated test port {port}"
        );
        assert_eq!(
            http_post_status_code(&format!("127.0.0.1:{port}"), "/mcp", "{}", None),
            Some(401)
        );

        let address = format!("127.0.0.1:{port}");
        let authorization = format!("Bearer {bearer}");
        let initialize = r#"{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2026-07-28","capabilities":{},"clientInfo":{"name":"ariadne-supervisor-test","version":"1"}}}"#;
        assert_eq!(
            http_post_status_code(
                &address,
                "/mcp",
                initialize,
                Some(&authorization)
            ),
            Some(200),
            "normal MCP initialization failed"
        );
        assert_eq!(
            http_post_status_code(
                &address,
                "/mcp",
                r#"{"jsonrpc":"2.0","method":"notifications/initialized"}"#,
                Some(&authorization)
            ),
            Some(202),
            "MCP initialization notification failed"
        );
        let query = r#"{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search_knowledge_chunks","arguments":{"query":"Ariadne coding agent architecture Abacus agent framework","limit":2}}}"#;
        let (status, response) = http_post_response(&address, query, Some(&authorization))
            .expect("normal Knowledge Vault query did not return an HTTP response");
        assert_eq!(status, 200, "normal Knowledge Vault query failed: {response}");
        assert!(response.contains("content"), "query response has no MCP content: {response}");
        assert!(!response.contains("\"isError\":true"), "MCP reported a tool error: {response}");

        let pid = read_test_pid(&pid_file).expect("MCP child PID was not reported");
        assert_ne!(
            pid,
            std::process::id(),
            "the Rust test process cannot own the MCP listener"
        );

        // Hold an incomplete MCP POST open longer than three supervisor intervals.
        // A liveness check must still pass because it only connects to the listener.
        let mut slow_request = TcpStream::connect(&address).unwrap();
        slow_request
            .write_all(b"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 1048576\r\n\r\n{")
            .unwrap();
        for _ in 0..3 {
            thread::sleep(Duration::from_secs(2));
            assert!(mcp_liveness_check(), "MCP liveness failed during a slow request");
        }
        let pid_during_slow_request = read_test_pid(&pid_file)
            .expect("MCP child PID disappeared during slow request");
        assert_eq!(pid_during_slow_request, pid, "slow request caused an MCP restart");
        drop(slow_request);

        TEST_KILL_MCP_CHILD.store(true, Ordering::Release);
        assert!(
            wait_until(Duration::from_secs(40), || {
                mcp_liveness_check() && read_test_pid(&pid_file).is_some_and(|current| current != pid)
            }),
            "MCP failed to recover on the isolated test port {port}"
        );
        let recovered_pid = read_test_pid(&pid_file).expect("recovered MCP child PID was not reported");
        assert_ne!(recovered_pid, pid, "MCP child was not replaced after termination");

        services.stop();
        let mcp_log = test_root.join(r"Ariadne\logs\knowledge-vault-mcp.log");
        let log_contents = fs::read_to_string(&mcp_log).expect("MCP diagnostics were not written");
        assert!(log_contents.contains("started Knowledge Vault MCP"));
        assert!(!log_contents.contains(&bearer), "MCP diagnostics leaked the bearer token");
        assert!(fs::metadata(&mcp_log).unwrap().len() <= DIAGNOSTIC_MAX_BYTES);
        assert!(
            wait_until(Duration::from_secs(5), || TcpStream::connect(&address)
            .is_err()),
            "MCP listener remained open after Host shutdown"
        );
        let _ = fs::remove_file(config_path);
        let _ = fs::remove_dir_all(test_root);
    }

    fn wait_until(timeout: Duration, mut predicate: impl FnMut() -> bool) -> bool {
        let deadline = std::time::Instant::now() + timeout;
        while std::time::Instant::now() < deadline {
            if predicate() {
                return true;
            }
            thread::sleep(Duration::from_millis(250));
        }
        predicate()
    }

    fn read_test_pid(path: &std::path::Path) -> Option<u32> {
        fs::read_to_string(path).ok()?.trim().parse().ok()
    }
}
