#![cfg_attr(windows, windows_subsystem = "windows")]

mod idle_webm;

use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet, VecDeque};
use std::env;
use std::ffi::c_void;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::os::windows::io::AsRawHandle;
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::ptr::null_mut;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{mpsc::Receiver, Arc, Mutex, OnceLock};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use windows::core::{w, GUID, PCWSTR};
use windows::Win32::Foundation::{
    CloseHandle, GetLastError, ERROR_ALREADY_EXISTS, ERROR_PIPE_CONNECTED, GENERIC_WRITE, HINSTANCE,
    HWND, LPARAM, LRESULT, POINT, RECT, WPARAM,
};
use windows::Win32::Graphics::Gdi::{
    CreateCompatibleDC, CreateDIBSection, CreateFontW, DeleteDC, DeleteObject, DrawTextW,
    GetMonitorInfoW, MonitorFromPoint, MONITORINFO, MONITOR_DEFAULTTONEAREST,
    SelectObject, SetBkMode, SetTextColor, AC_SRC_ALPHA, BITMAPINFO, BITMAPINFOHEADER, BI_RGB,
    BLENDFUNCTION, CLIP_DEFAULT_PRECIS, DEFAULT_CHARSET, DEFAULT_PITCH, DEFAULT_QUALITY,
    DIB_RGB_COLORS, DT_CENTER, DT_END_ELLIPSIS, DT_NOPREFIX, DT_SINGLELINE, DT_VCENTER,
    FF_DONTCARE, FW_NORMAL, OUT_DEFAULT_PRECIS, TRANSPARENT,
};
use windows::Win32::Storage::FileSystem::{CreateFileW, ReadFile, WriteFile, PIPE_ACCESS_INBOUND};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, SetInformationJobObject, TerminateJobObject,
    JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    JobObjectExtendedLimitInformation,
};
use windows::Win32::System::Pipes::{
    ConnectNamedPipe, CreateNamedPipeW, DisconnectNamedPipe, PIPE_READMODE_MESSAGE,
    PIPE_TYPE_MESSAGE, PIPE_WAIT,
};
use windows::Win32::System::Threading::{GetCurrentProcessId, GetCurrentThreadId};
use windows::Win32::UI::Shell::{
    ShellExecuteW, Shell_NotifyIconW, NIF_GUID, NIF_ICON, NIF_MESSAGE, NIF_TIP, NIM_ADD,
    NIM_DELETE, NIM_SETVERSION, NOTIFYICONDATAW, NOTIFYICON_VERSION_4,
};
use windows::Win32::UI::Input::KeyboardAndMouse::{ReleaseCapture, SetCapture};
use windows::Win32::System::SystemServices::MK_LBUTTON;
use windows::Win32::UI::WindowsAndMessaging::{
    AppendMenuW, CreatePopupMenu, CreateWindowExW, DefWindowProcW, DestroyMenu, DestroyWindow,
    DispatchMessageW, GetCursorPos, GetMessageW, GetSystemMetrics, GetWindowLongPtrW,
    GetWindowRect, GetWindowThreadProcessId, IsWindow, LoadCursorW, LoadIconW, PostMessageW,
    PostQuitMessage, RegisterClassW, RegisterWindowMessageW, SetForegroundWindow,
    SetWindowLongPtrW, SetWindowPos, ShowWindow, TrackPopupMenu, TranslateMessage,
    UpdateLayeredWindow, CS_HREDRAW, CS_VREDRAW, CW_USEDEFAULT, GWLP_USERDATA, HTCLIENT,
    HTTRANSPARENT, IDC_ARROW, IDI_APPLICATION, MA_NOACTIVATE, MF_SEPARATOR, MF_STRING,
    MSG, SWP_NOACTIVATE, SWP_NOSIZE, SW_HIDE,
    SW_SHOWNORMAL, SW_SHOWNOACTIVATE, TPM_RIGHTBUTTON, ULW_ALPHA, WM_APP, WM_CLOSE, WM_COMMAND,
    WM_CONTEXTMENU, WM_DESTROY, WM_LBUTTONDBLCLK, WM_LBUTTONDOWN, WM_LBUTTONUP,
    WM_MOUSEACTIVATE, WM_MOUSEMOVE, WM_NCHITTEST, WM_NULL, WM_RBUTTONUP,
    WNDCLASSW, WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_POPUP,
};

const PIPE_NAME: &str = r"\\.\pipe\ariadne-control";
const PIPE_NAME_W: PCWSTR = w!(r"\\.\pipe\ariadne-control");
const PIPE_MESSAGE: u32 = WM_APP + 11;
const FINAL_IDLE_MESSAGE: u32 = WM_APP + 12;
const IDLE_WEBM_FRAME_MESSAGE: u32 = WM_APP + 13;
const TRAY_OPEN_MESSAGE: u32 = WM_APP + 14;
const TRAY_MENU_MESSAGE: u32 = WM_APP + 15;
const ID_OPEN: usize = 1001;
const ID_RESTART: usize = 1002;
const ID_SHOW_AVATAR: usize = 1003;
const ID_HIDE_AVATAR: usize = 1004;
const ID_EXIT: usize = 1005;
const HOST_MUTEX: PCWSTR = w!("Local\\AriadneHost");
const CREATE_NO_WINDOW_FLAGS: u32 = 0x0800_0000;
const TRAY_GUID: GUID = GUID::from_u128(0x9b1f5e23_7b83_4f39_a7c8_5e3e4f2ad6b1);
const AVATAR_MAX_HEIGHT: u32 = 300;
const BUBBLE_MAX_HEIGHT: u32 = 50;
const FINAL_STATE_HOLD_SECONDS: u64 = 15;
const BUBBLE_HORIZONTAL_PADDING: u32 = 8;
const BUBBLE_CORNER_RADIUS: u32 = 6;
// Derived from configuration-avatar.css .avatar-preview-button: #13303B.
// Pixels remain alpha-bearing so the layered overlay keeps its transparency.
const BUBBLE_BACKGROUND_BGRA: [u8; 4] = [59, 48, 19, 220];
const MAX_SOURCE_DIMENSION: u32 = 4096;
const DASHBOARD_URL: &str = "http://127.0.0.1:8765/";
const DASHBOARD_URL_W: PCWSTR = w!("http://127.0.0.1:8765/");
const NIN_SELECT_EVENT: u32 = 0x0400;
const NIN_KEYSELECT_EVENT: u32 = 0x0401;

static LOG_LOCK: OnceLock<Mutex<()>> = OnceLock::new();
static TRAY_CALLBACK_MESSAGE: AtomicU32 = AtomicU32::new(0);

#[derive(Debug, Deserialize)]
struct AvatarManifest {
    version: u32,
    states: HashMap<String, String>,
}

#[derive(Debug, Deserialize, Default)]
struct AriadneConfiguration {
    avatar: Option<AvatarConfiguration>,
}

#[derive(Debug, Deserialize, Default)]
struct AvatarConfiguration {
    enabled: Option<bool>,
    asset_directory: Option<String>,
    state_assets: Option<HashMap<String, String>>,
}

struct AvatarSettings {
    enabled: bool,
    asset_root: PathBuf,
    state_assets: HashMap<String, String>,
}

#[derive(Debug, Deserialize)]
struct HostMessage {
    v: u32,
    #[serde(rename = "type")]
    kind: String,
    state: Option<String>,
    text: Option<String>,
    x: Option<i32>,
    y: Option<i32>,
}

enum UiEvent {
    Pipe(HostMessage),
    CoreLaunched(String),
    CoreAvailable,
    CoreUnavailable(String),
    CoreExited,
}

#[derive(Clone)]
struct UiQueue {
    events: Arc<Mutex<VecDeque<UiEvent>>>,
    hwnd: isize,
}

impl UiQueue {
    fn push(&self, event: UiEvent) {
        if let Ok(mut events) = self.events.lock() {
            events.push_back(event);
        }
        unsafe {
            let _ = PostMessageW(
                Some(HWND(self.hwnd as *mut c_void)),
                PIPE_MESSAGE,
                WPARAM(0),
                LPARAM(0),
            );
        }
    }

    fn drain(&self) -> Vec<UiEvent> {
        self.events
            .lock()
            .map(|mut events| events.drain(..).collect())
            .unwrap_or_default()
    }
}

fn now_text() -> String {
    let elapsed = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    format!("{}.{:03}", elapsed.as_secs(), elapsed.subsec_millis())
}

fn log_line(message: impl AsRef<str>) {
    let guard = LOG_LOCK.get_or_init(|| Mutex::new(())).lock();
    if guard.is_err() {
        return;
    }
    let path = env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
        .join("Ariadne");
    if fs::create_dir_all(&path).is_err() {
        return;
    }
    let file_path = path.join("host.log");
    if let Ok(metadata) = fs::metadata(&file_path) {
        if metadata.len() > 256 * 1024 {
            if let Ok(content) = fs::read_to_string(&file_path) {
                let keep = content.len().min(128 * 1024);
                let _ = fs::write(&file_path, &content[content.len() - keep..]);
            }
        }
    }
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(file_path) {
        let _ = writeln!(file, "[{}] {}", now_text(), message.as_ref());
    }
}

fn tray_log_line(message: impl AsRef<str>) {
    let guard = LOG_LOCK.get_or_init(|| Mutex::new(())).lock();
    if guard.is_err() {
        return;
    }
    let path = env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
        .join("Ariadne");
    if fs::create_dir_all(&path).is_err() {
        return;
    }
    let file_path = path.join("tray.log");
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(file_path) {
        let _ = writeln!(file, "[{}] {}", now_text(), message.as_ref());
    }
}

fn project_root_from(candidate: &Path) -> Option<PathBuf> {
    let mut current = if candidate.is_file() {
        candidate.parent().unwrap_or(candidate).to_path_buf()
    } else {
        candidate.to_path_buf()
    };
    for _ in 0..12 {
        if current.join("control-plane").join("server.py").is_file() {
            return Some(current);
        }
        if !current.pop() {
            break;
        }
    }
    None
}

fn find_project_root(exe: &Path) -> PathBuf {
    if let Some(configured) = env::var_os("ARIADNE_PROJECT_ROOT") {
        if let Some(root) = project_root_from(&PathBuf::from(configured)) {
            return root;
        }
    }
    if let Some(root) = project_root_from(exe) {
        return root;
    }
    if let Ok(current_dir) = env::current_dir() {
        if let Some(root) = project_root_from(&current_dir) {
            return root;
        }
    }
    if let Some(root) = project_root_from(Path::new(env!("CARGO_MANIFEST_DIR"))) {
        return root;
    }
    env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

fn resolve_python(project_root: &Path) -> Option<PathBuf> {
    let mut candidates = Vec::new();
    if let Some(value) = env::var_os("ARIADNE_PYTHON") {
        candidates.push(PathBuf::from(value));
    }
    candidates.push(project_root.join(".venv\\Scripts\\python.exe"));
    if let Some(local_app_data) = env::var_os("LOCALAPPDATA") {
        candidates
            .push(PathBuf::from(local_app_data).join("Programs\\Python\\Python312\\python.exe"));
    }
    if let Some(path_value) = env::var_os("PATH") {
        for directory in env::split_paths(&path_value) {
            candidates.push(directory.join("python.exe"));
        }
    }
    candidates.into_iter().find(|candidate| candidate.is_file())
}

fn health_check() -> bool {
    let address = ("127.0.0.1", 8765)
        .to_socket_addrs()
        .ok()
        .and_then(|mut addrs| addrs.next());
    let Some(address) = address else { return false };
    let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(250)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(500)));
    if stream
        .write_all(b"GET / HTTP/1.0\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        .is_err()
    {
        return false;
    }
    let mut body = String::new();
    let _ = stream.read_to_string(&mut body);
    (body.starts_with("HTTP/1.0 200") || body.starts_with("HTTP/1.1 200"))
        && body.contains("Server: AriadneLocal/")
}

fn spawn_core(
    project_root: &Path,
    ui: UiQueue,
) -> Option<ProcessState> {
    let Some(python) = resolve_python(project_root) else {
        log_line("Python interpreter unavailable; core is offline");
        ui.push(UiEvent::CoreUnavailable(
            "No Python interpreter found. Set ARIADNE_PYTHON.".into(),
        ));
        return None;
    };
    let server = project_root.join("control-plane").join("server.py");
    if !server.is_file() {
        log_line(format!(
            "Python core entry point missing: {}",
            server.display()
        ));
        ui.push(UiEvent::CoreUnavailable(
            "control-plane/server.py is missing.".into(),
        ));
        return None;
    }
    let launcher = python
        .parent()
        .map(|parent| parent.join("pythonw.exe"))
        .filter(|p| p.is_file())
        .unwrap_or(python.clone());
    let command_text = format!("{} {}", launcher.display(), server.display());
    log_line(format!("launching Python core: {}", command_text));
    let mut command = Command::new(&launcher);
    command
        .arg(&server)
        .current_dir(project_root)
        .creation_flags(CREATE_NO_WINDOW_FLAGS);
    let Ok(mut child) = command.spawn() else {
        log_line("Python core failed to launch; core is offline");
        ui.push(UiEvent::CoreUnavailable(
            "Python core failed to launch.".into(),
        ));
        return None;
    };
    let job = match create_core_job(&child) {
        Ok(job) => job,
        Err(error) => {
            log_line(format!("Python core process ownership setup failed: {}", error));
            let _ = child.kill();
            let _ = child.wait();
            ui.push(UiEvent::CoreUnavailable(
                "Python core process ownership could not be established.".into(),
            ));
            return None;
        }
    };
    log_line(format!(
        "Python core assigned to lifecycle job: pid={}, job={:?}",
        child.id(), job
    ));
    let child = Arc::new(Mutex::new(child));
    let wait_events = ui.clone();
    let wait_child = child.clone();
    let (wait_tx, wait_rx) = std::sync::mpsc::channel();
    thread::spawn(move || {
        let status = loop {
            let result = wait_child
                .lock()
                .ok()
                .and_then(|mut owned_child| owned_child.try_wait().ok());
            match result {
                Some(Some(value)) => break value.code(),
                Some(None) => thread::sleep(Duration::from_millis(100)),
                None => break None,
            }
        };
        log_line(format!("Python core exited: {:?}", status));
        wait_events.push(UiEvent::CoreExited);
        let _ = wait_tx.send(());
    });
    let readiness_ui = ui.clone();
    thread::spawn(move || {
        for _ in 0..60 {
            if health_check() {
                log_line("core available");
                readiness_ui.push(UiEvent::CoreAvailable);
                return;
            }
            thread::sleep(Duration::from_millis(500));
        }
        log_line("core unavailable after launch health window");
        readiness_ui.push(UiEvent::CoreUnavailable(
            "Python launched but /api/status did not become ready.".into(),
        ));
    });
    ui.push(UiEvent::CoreLaunched(command_text));
    Some(ProcessState { child, job, wait_rx })
}

struct ProcessState {
    child: Arc<Mutex<Child>>,
    job: windows::Win32::Foundation::HANDLE,
    wait_rx: std::sync::mpsc::Receiver<()>,
}

impl Drop for ProcessState {
    fn drop(&mut self) {
        unsafe {
            let _ = CloseHandle(self.job);
        }
    }
}

enum SupervisorCommand {
    Restart,
    Stop,
}

struct CoreSupervisor {
    commands: std::sync::mpsc::Sender<SupervisorCommand>,
    join: Option<thread::JoinHandle<()>>,
}

impl CoreSupervisor {
    fn start(project_root: PathBuf, ui: UiQueue) -> Self {
        let (tx, rx) = std::sync::mpsc::channel();
        let join = thread::spawn(move || {
            let mut process = spawn_core(&project_root, ui.clone());
            loop {
                if let Some(current) = process.as_ref() {
                    if current.wait_rx.try_recv().is_ok() {
                        process = None;
                    }
                }
                match rx.recv_timeout(Duration::from_millis(500)) {
                    Ok(SupervisorCommand::Restart) => {
                        log_line("restart requested");
                        if let Some(current) = process.as_ref() {
                            terminate_core(current);
                        }
                        if let Some(current) = process.take() {
                            let _ = current.wait_rx.recv_timeout(Duration::from_secs(10));
                        }
                        process = spawn_core(&project_root, ui.clone());
                    }
                    Ok(SupervisorCommand::Stop)
                    | Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {
                        if let Some(current) = process.as_ref() {
                            terminate_core(current);
                        }
                        if let Some(current) = process.take() {
                            let _ = current.wait_rx.recv_timeout(Duration::from_secs(10));
                        }
                        log_line("Python core stopped during host shutdown");
                        break;
                    }
                    Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {}
                }
            }
        });
        Self {
            commands: tx,
            join: Some(join),
        }
    }

    fn restart(&self) {
        let _ = self.commands.send(SupervisorCommand::Restart);
    }

    fn stop(mut self) {
        let _ = self.commands.send(SupervisorCommand::Stop);
        if let Some(join) = self.join.take() {
            let _ = join.join();
        }
    }
}

fn terminate_core(process: &ProcessState) {
    unsafe {
        match TerminateJobObject(process.job, 1) {
            Ok(()) => log_line("Python core lifecycle job termination requested"),
            Err(error) => {
                log_line(format!(
                    "Python core lifecycle job termination failed: {}; falling back to direct child termination",
                    error
                ));
                match process.child.lock() {
                    Ok(mut child) => match child.kill() {
                        Ok(()) => log_line("Python core direct termination requested"),
                        Err(error) => log_line(format!(
                            "Python core direct termination failed: {}",
                            error
                        )),
                    },
                    Err(_) => log_line("Python core direct termination failed: child lock poisoned"),
                }
            }
        }
    }
}

fn create_core_job(child: &Child) -> Result<windows::Win32::Foundation::HANDLE, String> {
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
            return Err(format!("could not configure lifecycle job: {}", error));
        }
        let process = windows::Win32::Foundation::HANDLE(child.as_raw_handle() as *mut c_void);
        if let Err(error) = AssignProcessToJobObject(job, process) {
            let _ = CloseHandle(job);
            return Err(format!("could not assign Python core to lifecycle job: {}", error));
        }
        Ok(job)
    }
}

fn canonical_state(value: &str) -> bool {
    matches!(
        value,
        "idle"
            | "listening"
            | "thinking"
            | "searching_vault"
            | "reading"
            | "cross_referencing"
            | "loading_model"
            | "working"
            | "speaking"
            | "waiting"
            | "success"
            | "warning"
            | "confused"
            | "recovering"
            | "error"
            | "offline"
    )
}

fn state_status(state: &str) -> Option<&'static str> {
    match state {
        "listening" => Some("Listening..."),
        "thinking" => Some("Thinking..."),
        "searching_vault" => Some("Searching the Vault..."),
        "reading" => Some("Reading..."),
        "cross_referencing" => Some("Cross-referencing..."),
        "loading_model" => Some("Loading model..."),
        "working" => Some("Working..."),
        "waiting" => Some("Waiting..."),
        "success" => Some("Done."),
        "warning" => Some("Warning..."),
        "confused" => Some("Checking that..."),
        "recovering" => Some("Recovering..."),
        "error" => Some("An error occurred."),
        "offline" => Some("Offline"),
        // Speaking receives its dialogue through the separate `say` event.
        "idle" | "speaking" => None,
        _ => None,
    }
}

fn pipe_receiver(ui: UiQueue, stop: Arc<AtomicBool>) -> thread::JoinHandle<()> {
    thread::spawn(move || {
        log_line(format!("IPC receiver listening on {}", PIPE_NAME));
        while !stop.load(Ordering::Acquire) {
            let pipe = unsafe {
                CreateNamedPipeW(
                    PIPE_NAME_W,
                    PIPE_ACCESS_INBOUND,
                    PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT,
                    1,
                    8192,
                    8192,
                    0,
                    None,
                )
            };
            if pipe.is_invalid() {
                log_line("could not create named pipe");
                break;
            }
            let connected = unsafe {
                ConnectNamedPipe(pipe, None).is_ok() || GetLastError() == ERROR_PIPE_CONNECTED
            };
            if connected {
                let mut bytes = Vec::new();
                loop {
                    let mut buffer = [0u8; 2048];
                    let mut read = 0u32;
                    let ok = unsafe {
                        ReadFile(pipe, Some(&mut buffer), Some(&mut read as *mut u32), None).is_ok()
                    };
                    if !ok || read == 0 {
                        break;
                    }
                    bytes.extend_from_slice(&buffer[..read as usize]);
                    while let Some(index) = bytes.iter().position(|byte| *byte == b'\n') {
                        let line = bytes.drain(..=index).collect::<Vec<_>>();
                        let text = String::from_utf8_lossy(&line[..line.len().saturating_sub(1)]);
                        match serde_json::from_str::<HostMessage>(&text) {
                            Ok(message) if message.v == 1 => ui.push(UiEvent::Pipe(message)),
                            Ok(_) => log_line("ignored IPC message with unsupported version"),
                            Err(_) => log_line("ignored malformed IPC message"),
                        }
                    }
                }
            }
            unsafe {
                let _ = DisconnectNamedPipe(pipe);
                let _ = CloseHandle(pipe);
            }
        }
        log_line("IPC receiver stopped");
    })
}

fn wake_pipe_receiver() {
    unsafe {
        if let Ok(handle) = CreateFileW(
            PIPE_NAME_W,
            GENERIC_WRITE.0,
            windows::Win32::Storage::FileSystem::FILE_SHARE_MODE(0),
            None,
            windows::Win32::Storage::FileSystem::OPEN_EXISTING,
            windows::Win32::Storage::FileSystem::FILE_ATTRIBUTE_NORMAL,
            None,
        ) {
            let mut written = 0u32;
            let _ = WriteFile(handle, Some(b"\n"), Some(&mut written as *mut u32), None);
            let _ = CloseHandle(handle);
        }
    }
}

fn load_manifest(asset_root: &Path) -> AvatarManifest {
    let path = asset_root.join("avatar_states.json");
    match fs::read_to_string(&path)
        .ok()
        .and_then(|text| serde_json::from_str::<AvatarManifest>(&text).ok())
    {
        Some(manifest) if manifest.version == 1 => manifest,
        _ => {
            log_line(format!(
                "avatar manifest unavailable or unsupported: {}",
                path.display()
            ));
            AvatarManifest {
                version: 1,
                states: HashMap::new(),
            }
        }
    }
}

fn configuration_path() -> PathBuf {
    if let Some(explicit) = env::var_os("ARIADNE_CONFIG_PATH") {
        let path = PathBuf::from(explicit);
        if !path.as_os_str().is_empty() {
            return path;
        }
    }
    env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
        .join("Ariadne\\configuration.json")
}

fn load_avatar_settings(exe: &Path, project_root: &Path) -> AvatarSettings {
    let default_root = find_asset_root(exe, project_root);
    let saved = fs::read_to_string(configuration_path())
        .ok()
        .and_then(|text| serde_json::from_str::<AriadneConfiguration>(&text).ok());
    let Some(avatar) = saved.and_then(|configuration| configuration.avatar) else {
        return AvatarSettings {
            enabled: true,
            asset_root: default_root,
            state_assets: HashMap::new(),
        };
    };

    let asset_root = match avatar.asset_directory.as_deref() {
        Some(value) if !value.trim().is_empty() => {
            let candidate = PathBuf::from(value);
            if candidate.is_absolute() {
                candidate
            } else {
                log_line(format!(
                    "avatar asset directory is not absolute; using default: {}",
                    value
                ));
                default_root
            }
        }
        _ => default_root,
    };
    AvatarSettings {
        enabled: avatar.enabled.unwrap_or(true),
        asset_root,
        state_assets: avatar.state_assets.unwrap_or_default(),
    }
}

fn find_asset_root(exe: &Path, project_root: &Path) -> PathBuf {
    let candidates = [
        exe.parent()
            .unwrap_or_else(|| Path::new("."))
            .join("assets\\avatar"),
        project_root.join("control-plane\\host\\assets\\avatar"),
    ];
    candidates
        .iter()
        .find(|path| path.join("avatar_states.json").is_file())
        .cloned()
        .unwrap_or_else(|| candidates[0].clone())
}

#[derive(Deserialize, Serialize, Default)]
struct AvatarPosition {
    x: i32,
    y: i32,
}

struct AvatarDrag {
    anchor_x: i32,
    anchor_y: i32,
}

struct AvatarOverlay {
    hwnd: HWND,
    message_hwnd: HWND,
    executable: PathBuf,
    project_root: PathBuf,
    asset_root: PathBuf,
    manifest: AvatarManifest,
    position: AvatarPosition,
    position_saved: bool,
    rendered_width: u32,
    rendered_height: u32,
    hit_alpha: Vec<u8>,
    drag: Option<AvatarDrag>,
    state: String,
    status_text: Option<String>,
    final_hold_generation: u64,
    final_hold_active: bool,
    idle_webm_rx: Option<Receiver<idle_webm::IdleWebmMessage>>,
    idle_webm_stop: Option<Arc<AtomicBool>>,
    idle_webm_failed: bool,
    enabled: bool,
    logged_missing: HashSet<String>,
}

fn scaled_avatar_dimensions(source_width: u32, source_height: u32) -> (u32, u32) {
    if source_height <= AVATAR_MAX_HEIGHT {
        return (source_width, source_height);
    }
    let width =
        ((source_width as u64 * AVATAR_MAX_HEIGHT as u64) / source_height as u64).max(1) as u32;
    (width, AVATAR_MAX_HEIGHT)
}

fn layout_height(avatar_height: u32, has_status: bool) -> u32 {
    avatar_height + if has_status { BUBBLE_MAX_HEIGHT } else { 0 }
}

const ALPHA_HIT_THRESHOLD: u8 = 24;

fn premultiply_channel(value: u8, alpha: u8) -> u8 {
    ((value as u16 * alpha as u16 + 127) / 255) as u8
}

fn premultiply_rgba(image: &mut image::RgbaImage) {
    for pixel in image.pixels_mut() {
        let alpha = pixel[3];
        pixel[0] = premultiply_channel(pixel[0], alpha);
        pixel[1] = premultiply_channel(pixel[1], alpha);
        pixel[2] = premultiply_channel(pixel[2], alpha);
    }
}

fn clamp_premultiplied_rgba(image: &mut image::RgbaImage) {
    for pixel in image.pixels_mut() {
        let alpha = pixel[3];
        pixel[0] = pixel[0].min(alpha);
        pixel[1] = pixel[1].min(alpha);
        pixel[2] = pixel[2].min(alpha);
    }
}

fn configured_manifest(mut manifest: AvatarManifest, state_assets: &HashMap<String, String>) -> AvatarManifest {
    for (state, filename) in state_assets {
        if canonical_state(state) {
            let relative = Path::new(filename);
            if !relative.is_absolute()
                && !relative.components().any(|component| {
                    matches!(component, std::path::Component::ParentDir)
                })
                && relative.extension().is_some_and(|extension| extension.eq_ignore_ascii_case("png"))
            {
                manifest.states.insert(state.clone(), filename.clone());
            }
        }
    }
    manifest
}

fn rounded_bubble_pixel_inside(x: u32, y: u32, width: u32, height: u32) -> bool {
    if width == 0 || height == 0 {
        return false;
    }
    let radius = BUBBLE_CORNER_RADIUS.min(width / 2).min(height / 2);
    if radius == 0 || width <= radius * 2 || height <= radius * 2 {
        return true;
    }

    let x = x as i32;
    let y = y as i32;
    let radius = radius as i32;
    let right = width as i32 - 1;
    let bottom = height as i32 - 1;
    let center_x = if x < radius {
        radius
    } else if x > right - radius {
        right - radius
    } else {
        x
    };
    let center_y = if y < radius {
        radius
    } else if y > bottom - radius {
        bottom - radius
    } else {
        y
    };
    let dx = x - center_x;
    let dy = y - center_y;
    dx * dx + dy * dy <= radius * radius
}

impl AvatarOverlay {
    unsafe fn new(
        instance: HINSTANCE,
        message_hwnd: HWND,
        executable: PathBuf,
        project_root: PathBuf,
    ) -> windows::core::Result<Self> {
        let hwnd = CreateWindowExW(
            WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_NOACTIVATE,
            w!("AriadneAvatarWindow"),
            w!("Ariadne Avatar"),
            WS_POPUP,
            0,
            0,
            1,
            1,
            None,
            None,
            Some(instance),
            None,
        )?;
        let settings = load_avatar_settings(&executable, &project_root);
        let (position, position_saved) = load_position();
        Ok(Self {
            hwnd,
            message_hwnd,
            executable,
            project_root,
            asset_root: settings.asset_root.clone(),
            manifest: configured_manifest(load_manifest(&settings.asset_root), &settings.state_assets),
            position,
            position_saved,
            rendered_width: 1,
            rendered_height: 1,
            hit_alpha: vec![0],
            drag: None,
            state: "idle".into(),
            status_text: None,
            final_hold_generation: 0,
            final_hold_active: false,
            idle_webm_rx: None,
            idle_webm_stop: None,
            idle_webm_failed: false,
            enabled: settings.enabled,
            logged_missing: HashSet::new(),
        })
    }

    fn show(&mut self) {
        if !self.enabled {
            return;
        }
        log_line(format!(
            "avatar show requested: state={}, webm_active={}, webm_failed={}",
            self.state,
            self.idle_webm_rx.is_some(),
            self.idle_webm_failed
        ));
        self.render_current_state();
        unsafe {
            let _ = ShowWindow(self.hwnd, SW_SHOWNOACTIVATE);
        }
    }
    fn hide(&mut self) {
        self.stop_idle_webm("hide");
        self.cancel_final_hold("hide");
        unsafe {
            let _ = ShowWindow(self.hwnd, SW_HIDE);
        }
    }

    fn set_position(&mut self, x: i32, y: i32) {
        self.apply_position(x, y, true);
    }

    fn apply_position(&mut self, x: i32, y: i32, persist: bool) {
        let (x, y) = clamp_position_to_work_area(
            x,
            y,
            self.rendered_width.max(1),
            self.rendered_height.max(1),
        );
        self.position = AvatarPosition { x, y };
        self.position_saved = true;
        if persist {
            save_position(&self.position);
        }
        unsafe {
            let _ = SetWindowPos(
                self.hwnd,
                Some(windows::Win32::UI::WindowsAndMessaging::HWND_TOPMOST),
                x,
                y,
                0,
                0,
                SWP_NOACTIVATE | SWP_NOSIZE,
            );
        }
    }

    fn begin_drag(&mut self) {
        let mut point = POINT::default();
        unsafe {
            if GetCursorPos(&mut point).is_ok() {
                self.drag = Some(AvatarDrag {
                    anchor_x: point.x - self.position.x,
                    anchor_y: point.y - self.position.y,
                });
                let _ = SetCapture(self.hwnd);
                log_line(format!("avatar drag started: position=({}, {}), cursor=({}, {})", self.position.x, self.position.y, point.x, point.y));
            }
        }
    }

    fn continue_drag(&mut self) {
        let Some(drag) = self.drag.as_ref() else {
            return;
        };
        let mut point = POINT::default();
        unsafe {
            if GetCursorPos(&mut point).is_ok() {
                self.apply_position(point.x - drag.anchor_x, point.y - drag.anchor_y, false);
            }
        }
    }

    fn end_drag(&mut self) {
        if self.drag.take().is_some() {
            unsafe {
                let _ = ReleaseCapture();
            }
            save_position(&self.position);
            log_line(format!("avatar drag ended: position=({}, {})", self.position.x, self.position.y));
        }
    }

    fn hit_test(&self, screen_x: i32, screen_y: i32) -> bool {
        let mut rect = RECT::default();
        unsafe {
            if GetWindowRect(self.hwnd, &mut rect).is_err() {
                return false;
            }
        }
        let x = screen_x - rect.left;
        let y = screen_y - rect.top;
        if x < 0 || y < 0 || x >= self.rendered_width as i32 || y >= self.rendered_height as i32 {
            return false;
        }
        self.hit_alpha
            .get((y as u32 * self.rendered_width + x as u32) as usize)
            .copied()
            .unwrap_or_default()
            >= ALPHA_HIT_THRESHOLD
    }

    unsafe fn install_window_userdata(&mut self) {
        let previous = SetWindowLongPtrW(
            self.hwnd,
            GWLP_USERDATA,
            self as *mut AvatarOverlay as isize,
        );
        if previous != 0 {
            log_line(format!("avatar window userdata replaced: hwnd={}, previous={previous}", self.hwnd.0 as usize));
        }
    }

    fn cancel_final_hold(&mut self, reason: &str) {
        if !self.final_hold_active {
            return;
        }
        self.final_hold_active = false;
        self.final_hold_generation = self.final_hold_generation.wrapping_add(1);
        log_line(format!(
            "avatar final hold cancelled: reason={}, generation={}",
            reason, self.final_hold_generation
        ));
    }

    fn stop_idle_webm(&mut self, reason: &str) {
        if let Some(stop) = self.idle_webm_stop.take() {
            stop.store(true, Ordering::Release);
            self.idle_webm_rx = None;
            log_line(format!("avatar idle WebM stopped: reason={reason}"));
        }
    }

    fn idle_webm_path(&self) -> PathBuf {
        let scaled = self.asset_root.join("ariadne_idle_small.webm");
        if scaled.is_file() {
            scaled
        } else {
            self.asset_root.join("ariadne_idle.webm")
        }
    }

    fn start_idle_webm(&mut self) -> bool {
        if self.idle_webm_rx.is_some() {
            return true;
        }
        let path = self.idle_webm_path();
        if !path.is_file() {
            self.log_missing_once(
                "idle-webm-missing".into(),
                format!("idle WebM is unavailable; using PNG fallback: {}", path.display()),
            );
            return false;
        }
        let playback = match idle_webm::IdleWebmPlayback::open(&path) {
            Ok(playback) => playback,
            Err(error) => {
                self.log_missing_once(
                    "idle-webm-invalid".into(),
                    format!("idle WebM failed validation; using PNG fallback: {error}"),
                );
                self.idle_webm_failed = true;
                return false;
            }
        };
        let stop = Arc::new(AtomicBool::new(false));
        self.idle_webm_rx = Some(playback.spawn(self.message_hwnd.0 as isize, stop.clone()));
        self.idle_webm_stop = Some(stop);
        log_line(format!(
            "avatar idle WebM playback started: path={}, loop=true, alpha=true",
            path.display()
        ));
        true
    }

    fn idle_webm_frame_tick(&mut self) {
        if self.state != "idle" {
            return;
        }
        let Some(receiver) = self.idle_webm_rx.as_ref() else {
            return;
        };
        let mut latest = None;
        while let Ok(message) = receiver.try_recv() {
            latest = Some(message);
        }
        match latest {
            Some(idle_webm::IdleWebmMessage::Frame(image)) => {
                // Filmora's WebM metadata marks this alpha stream as
                // premultiplied; do not multiply the edge colours twice.
                if let Err(error) = self.render_rgba(image, true) {
                    self.log_missing_once(
                        "idle-webm-render".into(),
                        format!("idle WebM frame render failed; using PNG fallback: {error}"),
                    );
                    self.idle_webm_failed = true;
                    self.stop_idle_webm("frame-render-failed");
                    let _ = self.render_state_asset("idle");
                }
            }
            Some(idle_webm::IdleWebmMessage::Failed(error)) => {
                self.log_missing_once(
                    "idle-webm-decode".into(),
                    format!("idle WebM playback failed; using PNG fallback: {error}"),
                );
                self.idle_webm_failed = true;
                self.stop_idle_webm("decode-failed");
                let _ = self.render_state_asset("idle");
            }
            None => {}
        }
    }

    fn begin_final_hold(&mut self, message_hwnd: HWND) {
        self.cancel_final_hold("replaced");
        self.final_hold_generation = self.final_hold_generation.wrapping_add(1);
        let generation = self.final_hold_generation;
        self.final_hold_active = true;
        log_line(format!(
            "avatar final hold started: state=speaking, seconds={}, generation={}",
            FINAL_STATE_HOLD_SECONDS, generation
        ));
        let message_hwnd_value = message_hwnd.0 as isize;
        thread::spawn(move || {
            thread::sleep(Duration::from_secs(FINAL_STATE_HOLD_SECONDS));
            unsafe {
                if PostMessageW(
                    Some(HWND(message_hwnd_value as *mut c_void)),
                    FINAL_IDLE_MESSAGE,
                    WPARAM(generation as usize),
                    LPARAM(0),
                )
                .is_err()
                {
                    log_line(format!(
                        "avatar final hold completion post failed: generation={}",
                        generation
                    ));
                }
            }
        });
    }

    fn final_hold_expired(&mut self, generation: u64) {
        if !self.final_hold_active || generation != self.final_hold_generation {
            log_line(format!(
                "avatar final hold completion ignored: generation={}, active_generation={}, active={}",
                generation, self.final_hold_generation, self.final_hold_active
            ));
            return;
        }
        self.final_hold_active = false;
        log_line(format!(
            "avatar final hold expired: seconds={}, generation={}; returning to idle",
            FINAL_STATE_HOLD_SECONDS, generation
        ));
        self.set_state("idle");
    }

    fn set_status(&mut self, text: Option<String>) {
        self.cancel_final_hold("say-event");
        self.status_text = text.and_then(|value| {
            let normalized = value
                .chars()
                .map(|character| {
                    if character == '\r' || character == '\n' {
                        ' '
                    } else {
                        character
                    }
                })
                .collect::<String>()
                .trim()
                .chars()
                .take(500)
                .collect::<String>();
            (!normalized.is_empty()).then_some(normalized)
        });
        log_line(format!(
            "avatar bubble requested: source=say, state={}, chars={}",
            self.state,
            self.status_text.as_ref().map(|value| value.chars().count()).unwrap_or(0)
        ));
        if self.enabled {
            self.render_current_state();
        } else {
            log_line(format!(
                "avatar bubble hidden: source=say, state={}, reason=avatar-disabled",
                self.state
            ));
        }
    }

    fn render_current_state(&mut self) {
        let state = self.state.clone();
        if state == "idle" && !self.idle_webm_failed && self.start_idle_webm() {
            return;
        }
        if self.render_state_asset(&state) {
            return;
        }
        if state != "idle" && self.render_state_asset("idle") {
            self.log_missing_once(
                format!("fallback:{state}"),
                format!("avatar state {} fell back to idle", state),
            );
            return;
        }
        self.hide();
        log_line(format!(
            "avatar bubble hidden: state={}, reason=no-renderable-asset",
            state
        ));
    }

    fn reload_from_configuration(&mut self) {
        let settings = load_avatar_settings(&self.executable, &self.project_root);
        self.enabled = settings.enabled;
        self.asset_root = settings.asset_root;
        self.manifest = configured_manifest(load_manifest(&self.asset_root), &settings.state_assets);
        self.logged_missing.clear();
        self.idle_webm_failed = false;
        self.stop_idle_webm("configuration-reload");
        if !self.enabled {
            self.hide();
            log_line("avatar overlay disabled by configuration");
            return;
        }
        let state = self.state.clone();
        self.set_state(&state);
        log_line(format!(
            "avatar configuration reloaded: enabled={}, asset_root={}",
            self.enabled,
            self.asset_root.display()
        ));
    }

    fn log_missing_once(&mut self, key: String, message: String) {
        if self.logged_missing.insert(key) {
            log_line(message);
        }
    }

    fn safe_asset_path(&self, filename: &str) -> Option<PathBuf> {
        let relative = Path::new(filename);
        if relative.is_absolute()
            || relative
                .components()
                .any(|component| matches!(component, std::path::Component::ParentDir))
        {
            return None;
        }
        Some(self.asset_root.join(relative))
    }

    fn render_state_asset(&mut self, state: &str) -> bool {
        let Some(filename) = self.manifest.states.get(state).cloned() else {
            self.log_missing_once(
                format!("manifest:{state}"),
                format!("avatar state is absent from manifest: {state}"),
            );
            return false;
        };
        let Some(path) = self.safe_asset_path(&filename) else {
            self.log_missing_once(
                format!("unsafe:{state}"),
                format!("avatar asset path rejected for {state}: {filename}"),
            );
            return false;
        };
        if !path.is_file() {
            self.log_missing_once(
                format!("missing:{state}"),
                format!("missing avatar asset for {}: {}", state, path.display()),
            );
            return false;
        }
        match self.render_png(&path) {
            Ok(()) => true,
            Err(error) => {
                self.log_missing_once(
                    format!("invalid:{state}"),
                    format!("avatar asset failed for {}: {}", state, error),
                );
                false
            }
        }
    }

    fn set_state(&mut self, state: &str) {
        if !canonical_state(state) {
            log_line(format!("ignored unknown avatar state: {}", state));
            return;
        }
        self.cancel_final_hold("state-event");
        if state != "idle" {
            self.stop_idle_webm("state-change");
        }
        self.state = state.to_string();
        self.status_text = state_status(state).map(str::to_string);
        log_line(format!(
            "avatar bubble requested: source=state, state={}, chars={}",
            state,
            self.status_text.as_ref().map(|value| value.chars().count()).unwrap_or(0)
        ));
        if !self.enabled {
            self.hide();
            log_line(format!(
                "avatar bubble hidden: state={}, reason=avatar-disabled",
                state
            ));
            return;
        }
        self.render_current_state();
    }

    fn render_png(&mut self, path: &Path) -> Result<(), String> {
        let image = image::open(path)
            .map_err(|error| error.to_string())?
            .to_rgba8();
        self.render_rgba(image, false)
    }

    fn render_rgba(&mut self, mut image: image::RgbaImage, already_premultiplied: bool) -> Result<(), String> {
        let (source_width, source_height) = image.dimensions();
        if source_width == 0
            || source_height == 0
            || source_width > MAX_SOURCE_DIMENSION
            || source_height > MAX_SOURCE_DIMENSION
        {
            return Err("unsupported image dimensions".into());
        }
        // UpdateLayeredWindow with AC_SRC_ALPHA consumes premultiplied BGRA.
        // Premultiply before resizing so transparent RGB cannot bleed into
        // crisp edges or isolated transparent regions during filtering.
        if !already_premultiplied {
            premultiply_rgba(&mut image);
        }
        let (width, height) = scaled_avatar_dimensions(source_width, source_height);
        let mut image =
            image::imageops::resize(&image, width, height, image::imageops::FilterType::Lanczos3);
        // Lanczos can overshoot at a hard alpha boundary. Keep the DIB in a
        // valid premultiplied representation, including fully transparent
        // pixels, before handing it to UpdateLayeredWindow.
        clamp_premultiplied_rgba(&mut image);
        if !already_premultiplied {
            log_line("avatar alpha pipeline: premultiplied-before-resize, clamped-after-resize");
        }
        let bubble_height = if self.status_text.is_some() {
            BUBBLE_MAX_HEIGHT
        } else {
            0
        };
        let total_height = layout_height(height, bubble_height > 0);
        self.rendered_width = width;
        self.rendered_height = total_height;
        if !already_premultiplied {
            log_line(format!(
                "avatar layout: source={}x{}, rendered={}x{}, bubble={}px, total={}px, corner_radius={}px",
                source_width,
                source_height,
                width,
                height,
                bubble_height,
                total_height,
                BUBBLE_CORNER_RADIUS
            ));
        }
        let mut bgra = vec![0u8; (width * total_height * 4) as usize];
        self.hit_alpha = vec![0u8; (width * total_height) as usize];
        if bubble_height > 0 {
            for y in 0..bubble_height {
                for x in 0..width {
                    if !rounded_bubble_pixel_inside(x, y, width, bubble_height) {
                        continue;
                    }
                    let offset = ((y * width + x) * 4) as usize;
                    bgra[offset..offset + 4].copy_from_slice(&[
                        premultiply_channel(BUBBLE_BACKGROUND_BGRA[0], BUBBLE_BACKGROUND_BGRA[3]),
                        premultiply_channel(BUBBLE_BACKGROUND_BGRA[1], BUBBLE_BACKGROUND_BGRA[3]),
                        premultiply_channel(BUBBLE_BACKGROUND_BGRA[2], BUBBLE_BACKGROUND_BGRA[3]),
                        BUBBLE_BACKGROUND_BGRA[3],
                    ]);
                    self.hit_alpha[(y * width + x) as usize] = BUBBLE_BACKGROUND_BGRA[3];
                }
            }
        }
        for (index, pixel) in image.pixels().enumerate() {
            let x = (index as u32) % width;
            let y = (index as u32) / width + bubble_height;
            let offset = ((y * width + x) * 4) as usize;
            bgra[offset..offset + 4].copy_from_slice(&[pixel[2], pixel[1], pixel[0], pixel[3]]);
            self.hit_alpha[(y * width + x) as usize] = pixel[3];
        }
        unsafe {
            let mut info = BITMAPINFO {
                bmiHeader: BITMAPINFOHEADER {
                    biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
                    biWidth: width as i32,
                    biHeight: -(total_height as i32),
                    biPlanes: 1,
                    biBitCount: 32,
                    biCompression: BI_RGB.0,
                    ..Default::default()
                },
                ..Default::default()
            };
            let mut bits: *mut c_void = null_mut();
            let bitmap = CreateDIBSection(None, &mut info, DIB_RGB_COLORS, &mut bits, None, 0)
                .map_err(|_| "CreateDIBSection failed".to_string())?;
            if bits.is_null() {
                return Err("CreateDIBSection returned no pixels".into());
            }
            std::ptr::copy_nonoverlapping(bgra.as_ptr(), bits as *mut u8, bgra.len());
            let dc = CreateCompatibleDC(None);
            if dc.is_invalid() {
                let _ = DeleteObject(bitmap.into());
                return Err("CreateCompatibleDC failed".into());
            }
            let old = SelectObject(dc, bitmap.into());
            if let Some(status) = self.status_text.as_deref() {
                let font = CreateFontW(
                    -16,
                    0,
                    0,
                    0,
                    FW_NORMAL.0 as i32,
                    0,
                    0,
                    0,
                    DEFAULT_CHARSET,
                    OUT_DEFAULT_PRECIS,
                    CLIP_DEFAULT_PRECIS,
                    DEFAULT_QUALITY,
                    DEFAULT_PITCH.0 as u32 | FF_DONTCARE.0 as u32,
                    w!("Segoe UI"),
                );
                if !font.is_invalid() {
                    let old_font = SelectObject(dc, font.into());
                    let margin = BUBBLE_HORIZONTAL_PADDING.min(width / 2) as i32;
                    let mut text_rect = RECT {
                        left: margin,
                        top: 0,
                        right: (width as i32 - margin).max(margin + 1),
                        bottom: bubble_height as i32,
                    };
                    let mut text: Vec<u16> = status.encode_utf16().collect();
                    let _ = SetBkMode(dc, TRANSPARENT);
                    let _ = SetTextColor(dc, windows::Win32::Foundation::COLORREF(0x00F4F4EE));
                    let _ = DrawTextW(
                        dc,
                        &mut text,
                        &mut text_rect,
                        DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX,
                    );
                    let _ = SelectObject(dc, old_font);
                    let _ = DeleteObject(font.into());
                }
            }
            let source = POINT { x: 0, y: 0 };
            if !self.position_saved {
                let x = GetSystemMetrics(windows::Win32::UI::WindowsAndMessaging::SM_CXSCREEN)
                    - width as i32
                    - 32;
                let y = GetSystemMetrics(windows::Win32::UI::WindowsAndMessaging::SM_CYSCREEN)
                    - total_height as i32
                    - 64;
                self.apply_position(x, y, true);
            } else {
                self.apply_position(self.position.x, self.position.y, false);
            }
            let mut destination = POINT {
                x: self.position.x,
                y: self.position.y,
            };
            let size = windows::Win32::Foundation::SIZE {
                cx: width as i32,
                cy: total_height as i32,
            };
            let blend = BLENDFUNCTION {
                BlendOp: 0,
                BlendFlags: 0,
                SourceConstantAlpha: 255,
                AlphaFormat: AC_SRC_ALPHA as u8,
            };
            let ok = UpdateLayeredWindow(
                self.hwnd,
                None,
                Some(&mut destination),
                Some(&size),
                Some(dc),
                Some(&source),
                windows::Win32::Foundation::COLORREF(0),
                Some(&blend),
                ULW_ALPHA,
            )
            .is_ok();
            let _ = SelectObject(dc, old);
            let _ = DeleteObject(bitmap.into());
            let _ = DeleteDC(dc);
            if !ok {
                return Err("UpdateLayeredWindow failed".into());
            }
            let _ = ShowWindow(self.hwnd, SW_SHOWNOACTIVATE);
            if let Some(status) = self.status_text.as_deref() {
                log_line(format!(
                    "avatar bubble visible: state={}, chars={}, bubble_height={}px",
                    self.state,
                    status.chars().count(),
                    bubble_height
                ));
            } else {
                log_line(format!(
                    "avatar bubble hidden: state={}, reason=no-status",
                    self.state
                ));
            }
        }
        Ok(())
    }
}

fn position_path() -> PathBuf {
    env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
        .join("Ariadne\\avatar-position.json")
}

fn load_position() -> (AvatarPosition, bool) {
    let Some(position) = fs::read_to_string(position_path())
        .ok()
        .and_then(|text| serde_json::from_str::<AvatarPosition>(&text).ok()) else {
        return (AvatarPosition::default(), false);
    };
    (position, true)
}

fn clamp_position_to_work_area(x: i32, y: i32, width: u32, height: u32) -> (i32, i32) {
    unsafe {
        let point = POINT { x, y };
        let monitor = MonitorFromPoint(point, MONITOR_DEFAULTTONEAREST);
        let mut info = MONITORINFO {
            cbSize: std::mem::size_of::<MONITORINFO>() as u32,
            ..Default::default()
        };
        if !monitor.is_invalid() && GetMonitorInfoW(monitor, &mut info).as_bool() {
            let work = info.rcWork;
            let max_x = (work.right - width as i32).max(work.left);
            let max_y = (work.bottom - height as i32).max(work.top);
            return (x.clamp(work.left, max_x), y.clamp(work.top, max_y));
        }
    }
    (x.max(0), y.max(0))
}

fn save_position(position: &AvatarPosition) {
    let path = position_path();
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    if let Ok(text) = serde_json::to_string(position) {
        let _ = fs::write(path, text);
    }
}

unsafe extern "system" fn avatar_window_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    let overlay = GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *mut AvatarOverlay;
    match message {
        WM_NCHITTEST if !overlay.is_null() => {
            let screen_x = (lparam.0 as i16) as i32;
            let screen_y = ((lparam.0 >> 16) as i16) as i32;
            if (*overlay).hit_test(screen_x, screen_y) {
                LRESULT(HTCLIENT as isize)
            } else {
                LRESULT(HTTRANSPARENT as isize)
            }
        }
        WM_LBUTTONDOWN if !overlay.is_null() => {
            (*overlay).begin_drag();
            LRESULT(0)
        }
        WM_MOUSEMOVE if !overlay.is_null() && (wparam.0 & MK_LBUTTON.0 as usize) != 0 => {
            (*overlay).continue_drag();
            LRESULT(0)
        }
        WM_LBUTTONUP if !overlay.is_null() => {
            (*overlay).end_drag();
            LRESULT(0)
        }
        WM_CLOSE => {
            let _ = ShowWindow(hwnd, SW_HIDE);
            LRESULT(0)
        }
        WM_MOUSEACTIVATE => LRESULT(MA_NOACTIVATE as isize),
        WM_DESTROY => LRESULT(0),
        _ => DefWindowProcW(hwnd, message, wparam, lparam),
    }
}

unsafe extern "system" fn host_window_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    let tray_callback_message = TRAY_CALLBACK_MESSAGE.load(Ordering::Acquire);
    if tray_callback_message != 0 && message == tray_callback_message {
        let raw_event = lparam.0 as u32;
        let event = raw_event & 0xffff;
        let callback_icon = raw_event >> 16;
        tray_log_line(format!(
            "tray callback received in host_window_proc: message=0x{:08x}, wparam=0x{:x}, lparam=0x{:08x}, event_low=0x{:04x}, event_high=0x{:04x}",
            message,
            wparam.0,
            raw_event,
            event,
            callback_icon
        ));
        match classify_tray_event(event) {
            TrayAction::Open => {
                tray_log_line(format!(
                    "tray callback decoded: event={}, action=Open, icon_id={}",
                    tray_event_name(event), callback_icon
                ));
                if let Err(error) = PostMessageW(
                    Some(hwnd),
                    TRAY_OPEN_MESSAGE,
                    WPARAM(event as usize),
                    LPARAM(raw_event as isize),
                ) {
                    tray_log_line(format!(
                        "tray internal Open command post failed: event={}, error={}",
                        tray_event_name(event), error
                    ));
                }
            }
            TrayAction::Menu => {
                tray_log_line(format!(
                    "tray callback decoded: event={}, action=ContextMenu, icon_id={}",
                    tray_event_name(event), callback_icon
                ));
                if let Err(error) = PostMessageW(
                    Some(hwnd),
                    TRAY_MENU_MESSAGE,
                    WPARAM(event as usize),
                    LPARAM(raw_event as isize),
                ) {
                    tray_log_line(format!(
                        "tray internal ContextMenu command post failed: event={}, error={}",
                        tray_event_name(event), error
                    ));
                }
            }
            TrayAction::Ignore => tray_log_line(format!(
                "tray callback decoded: event=0x{:04x}, action=Ignore, icon_id={}",
                event, callback_icon
            )),
        }
        return LRESULT(0);
    }
    DefWindowProcW(hwnd, message, wparam, lparam)
}

unsafe fn register_windows(instance: HINSTANCE) -> windows::core::Result<()> {
    let cursor = LoadCursorW(None, IDC_ARROW).unwrap_or_default();
    let class = WNDCLASSW {
        hCursor: cursor,
        hInstance: instance,
        lpszClassName: w!("AriadneHostWindow"),
        lpfnWndProc: Some(host_window_proc),
        style: CS_HREDRAW | CS_VREDRAW,
        ..Default::default()
    };
    let _ = RegisterClassW(&class);
    let avatar_class = WNDCLASSW {
        hCursor: cursor,
        hInstance: instance,
        lpszClassName: w!("AriadneAvatarWindow"),
        lpfnWndProc: Some(avatar_window_proc),
        style: CS_HREDRAW | CS_VREDRAW,
        ..Default::default()
    };
    let _ = RegisterClassW(&avatar_class);
    Ok(())
}

unsafe fn tray_add(hwnd: HWND, instance: HINSTANCE, callback_message: u32) -> NOTIFYICONDATAW {
    let icon = LoadIconW(Some(instance), w!("ARIADNE_ICON"))
        .or_else(|_| LoadIconW(None, IDI_APPLICATION))
        .unwrap_or_default();
    let mut data = NOTIFYICONDATAW {
        cbSize: std::mem::size_of::<NOTIFYICONDATAW>() as u32,
        hWnd: hwnd,
        uID: 1,
        uFlags: NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_GUID,
        uCallbackMessage: callback_message,
        hIcon: icon,
        guidItem: TRAY_GUID,
        ..Default::default()
    };
    let tip: Vec<u16> = "Ariadne — Local AI Control Plane\0"
        .encode_utf16()
        .collect();
    for (target, value) in data.szTip.iter_mut().zip(tip) {
        *target = value;
    }
    data.Anonymous.uVersion = NOTIFYICON_VERSION_4;
    let added = Shell_NotifyIconW(NIM_ADD, &data);
    if added.as_bool() {
        tray_log_line(format!(
            "tray icon registration succeeded: hwnd={}, hwnd_alive={}, uid={}, guid={:?}, callback={}, flags=0x{:x}",
            hwnd.0 as usize, IsWindow(Some(hwnd)).as_bool(), data.uID, TRAY_GUID, callback_message, data.uFlags.0
        ));
    } else {
        tray_log_line(format!(
            "tray icon registration failed: hwnd={}, hwnd_alive={}, uid={}, guid={:?}, callback={}, flags=0x{:x}, error={:?}",
            hwnd.0 as usize,
            IsWindow(Some(hwnd)).as_bool(),
            data.uID,
            TRAY_GUID,
            callback_message,
            data.uFlags.0,
            GetLastError()
        ));
    }
    let versioned = Shell_NotifyIconW(NIM_SETVERSION, &data);
    if versioned.as_bool() {
        tray_log_line(format!(
            "tray icon version set: version={}, hwnd={}",
            NOTIFYICON_VERSION_4, hwnd.0 as usize
        ));
    } else {
        tray_log_line(format!(
            "tray icon version set failed: version={}, hwnd={}, error={:?}",
            NOTIFYICON_VERSION_4,
            hwnd.0 as usize,
            GetLastError()
        ));
    }
    data
}

unsafe fn tray_remove(data: &NOTIFYICONDATAW) {
    let removed = Shell_NotifyIconW(NIM_DELETE, data);
    if removed.as_bool() {
        tray_log_line(format!("tray icon removed: uid={}", data.uID));
    } else {
        tray_log_line(format!(
            "tray icon removal failed: uid={}, error={:?}",
            data.uID,
            GetLastError()
        ));
    }
}

unsafe fn show_tray_menu(hwnd: HWND) {
    let Ok(menu) = CreatePopupMenu() else {
        tray_log_line("could not create tray menu");
        return;
    };
    let _ = AppendMenuW(menu, MF_STRING, ID_OPEN, w!("Open Ariadne dashboard"));
    let _ = AppendMenuW(menu, MF_STRING, ID_RESTART, w!("Restart Ariadne core"));
    let _ = AppendMenuW(menu, MF_SEPARATOR, 0, PCWSTR::null());
    let _ = AppendMenuW(menu, MF_STRING, ID_SHOW_AVATAR, w!("Show avatar"));
    let _ = AppendMenuW(menu, MF_STRING, ID_HIDE_AVATAR, w!("Hide avatar"));
    let _ = AppendMenuW(menu, MF_SEPARATOR, 0, PCWSTR::null());
    let _ = AppendMenuW(menu, MF_STRING, ID_EXIT, w!("Exit Ariadne"));
    let mut point = POINT::default();
    if let Err(error) = GetCursorPos(&mut point) {
        tray_log_line(format!("tray popup cursor lookup failed: {}", error));
    }
    let foreground = SetForegroundWindow(hwnd);
    tray_log_line(format!(
        "tray popup requested: hwnd={}, point=({}, {}), foreground={}, menu={}",
        hwnd.0 as usize,
        point.x,
        point.y,
        foreground.as_bool(),
        menu.0 as usize
    ));
    let popup = TrackPopupMenu(menu, TPM_RIGHTBUTTON, point.x, point.y, Some(0), hwnd, None);
    if !popup.as_bool() {
        tray_log_line(format!("tray popup display failed: error={:?}", GetLastError()));
    }
    let _ = PostMessageW(Some(hwnd), WM_NULL, WPARAM(0), LPARAM(0));
    let _ = DestroyMenu(menu);
}

#[derive(Debug, PartialEq, Eq)]
enum TrayAction {
    Open,
    Menu,
    Ignore,
}

fn classify_tray_event(event: u32) -> TrayAction {
    match event {
        WM_LBUTTONUP | WM_LBUTTONDBLCLK | NIN_SELECT_EVENT | NIN_KEYSELECT_EVENT => {
            TrayAction::Open
        }
        WM_RBUTTONUP | WM_CONTEXTMENU => TrayAction::Menu,
        _ => TrayAction::Ignore,
    }
}

fn tray_event_name(event: u32) -> &'static str {
    match event {
        WM_LBUTTONUP => "WM_LBUTTONUP",
        WM_LBUTTONDBLCLK => "WM_LBUTTONDBLCLK",
        NIN_SELECT_EVENT => "NIN_SELECT",
        NIN_KEYSELECT_EVENT => "NIN_KEYSELECT",
        WM_RBUTTONUP => "WM_RBUTTONUP",
        WM_CONTEXTMENU => "WM_CONTEXTMENU",
        _ => "unrecognized",
    }
}

fn open_dashboard(trigger: &str) {
    if !health_check() {
        tray_log_line(format!(
            "dashboard-open skipped: trigger={}, url={}, Ariadne backend is not ready; no backend process was started",
            trigger, DASHBOARD_URL
        ));
        return;
    }

    unsafe {
        let result = ShellExecuteW(
            None,
            w!("open"),
            DASHBOARD_URL_W,
            PCWSTR::null(),
            PCWSTR::null(),
            SW_SHOWNORMAL,
        );
        let result_code = result.0 as isize;
        let last_error = GetLastError();
        if result_code > 32 {
            tray_log_line(format!(
                "dashboard-open succeeded: trigger={}, url={}, api=ShellExecuteW, result={}, last_error={:?}",
                trigger, DASHBOARD_URL, result_code, last_error
            ));
        } else {
            tray_log_line(format!(
                "dashboard-open failed: trigger={}, url={}, api=ShellExecuteW, result={}, windows_error={:?}",
                trigger, DASHBOARD_URL, result_code, last_error
            ));
        }
    }
}

fn process_events(
    queue: &UiQueue,
    avatar: &mut AvatarOverlay,
    supervisor: &CoreSupervisor,
    message_hwnd: HWND,
) {
    for event in queue.drain() {
        match event {
            UiEvent::Pipe(message) => match message.kind.as_str() {
                "state" => {
                    if let Some(state) = message.state.as_deref() {
                        log_line(format!("avatar state event received: {}", state));
                        avatar.set_state(state);
                    }
                }
                "say" => {
                    if let Some(text) = message.text {
                        log_line(format!(
                            "avatar say event received: state={}, chars={}",
                            avatar.state,
                            text.chars().count()
                        ));
                        avatar.set_status(Some(text.clone()));
                        if avatar.state == "speaking" && avatar.status_text.is_some() {
                            avatar.begin_final_hold(message_hwnd);
                        }
                        log_line(format!(
                            "IPC say event received ({} chars)",
                            text.chars().count()
                        ));
                    }
                }
                "show" => avatar.show(),
                "hide" => avatar.hide(),
                "reload_avatar" => avatar.reload_from_configuration(),
                "clear_status" => {
                    log_line(format!("avatar bubble clear requested: state={}", avatar.state));
                    avatar.set_status(None)
                }
                "move" => {
                    if let (Some(x), Some(y)) = (message.x, message.y) {
                        avatar.set_position(x, y);
                    }
                }
                _ => log_line(format!("ignored unknown IPC event type: {}", message.kind)),
            },
            UiEvent::CoreLaunched(command) => {
                avatar.set_state("loading_model");
                log_line(format!("core launch recorded: {}", command));
            }
            UiEvent::CoreAvailable => avatar.set_state("idle"),
            UiEvent::CoreUnavailable(reason) => {
                log_line(format!("core unavailable: {}", reason));
                avatar.set_state("offline");
            }
            UiEvent::CoreExited => avatar.set_state("offline"),
        }
    }
    let _ = supervisor;
}

fn run() -> Result<(), String> {
    unsafe {
        let mutex = windows::Win32::System::Threading::CreateMutexW(None, false, HOST_MUTEX)
            .map_err(|_| "could not create host mutex".to_string())?;
        if GetLastError() == ERROR_ALREADY_EXISTS {
            let _ = CloseHandle(mutex);
            return Ok(());
        }
        log_line("host start");
        let exe = env::current_exe().map_err(|error| error.to_string())?;
        let project_root = find_project_root(&exe);
        log_line(format!(
            "host executable: {}; project root: {}",
            exe.display(),
            project_root.display()
        ));
        let instance = HINSTANCE(GetModuleHandleW(None).map_err(|error| error.to_string())?.0);
        register_windows(instance).map_err(|error| error.to_string())?;
        let tray_message = RegisterWindowMessageW(w!("Ariadne.TrayCallback"));
        if tray_message == 0 {
            return Err(format!("could not register tray callback message: {:?}", GetLastError()));
        }
        TRAY_CALLBACK_MESSAGE.store(tray_message, Ordering::Release);
        let taskbar_created = RegisterWindowMessageW(w!("TaskbarCreated"));
        tray_log_line(format!(
            "registered tray callback message: tray={}, taskbar_created={}, internal_open={}, internal_menu={}",
            tray_message, taskbar_created, TRAY_OPEN_MESSAGE, TRAY_MENU_MESSAGE
        ));
        let message_hwnd = CreateWindowExW(
            WS_EX_TOOLWINDOW,
            w!("AriadneHostWindow"),
            w!("Ariadne Host"),
            WS_POPUP,
            CW_USEDEFAULT,
            CW_USEDEFAULT,
            0,
            0,
            None,
            None,
            Some(instance),
            None,
        )
        .map_err(|error| error.to_string())?;
        let queue = UiQueue {
            events: Arc::new(Mutex::new(VecDeque::new())),
            hwnd: message_hwnd.0 as isize,
        };
        let mut owner_pid = 0u32;
        let owner_thread = GetWindowThreadProcessId(message_hwnd, Some(&mut owner_pid));
        log_line(format!(
            "tray HWND ownership: hwnd={}, alive={}, window_thread={}, current_thread={}, window_pid={}, current_pid={}",
            message_hwnd.0 as usize,
            IsWindow(Some(message_hwnd)).as_bool(),
            owner_thread,
            GetCurrentThreadId(),
            owner_pid,
            GetCurrentProcessId()
        ));
        let stop_pipe = Arc::new(AtomicBool::new(false));
        let pipe_thread = pipe_receiver(queue.clone(), stop_pipe.clone());
        let supervisor = CoreSupervisor::start(project_root.clone(), queue.clone());
        let mut avatar = AvatarOverlay::new(instance, message_hwnd, exe, project_root)
            .map_err(|error| error.to_string())?;
        avatar.install_window_userdata();
        avatar.set_state("idle");
        if avatar.enabled {
            avatar.show();
        } else {
            avatar.hide();
        }
        let mut tray = tray_add(message_hwnd, instance, tray_message);
        tray_log_line(format!("tray message pump started: hwnd={}, callback={}, taskbar_created={}, hwnd_alive={}", message_hwnd.0 as usize, tray_message, taskbar_created, IsWindow(Some(message_hwnd)).as_bool()));
        let mut message = MSG::default();
        while GetMessageW(&mut message, None, 0, 0).0 > 0 {
            if message.message == taskbar_created {
                tray_log_line("Explorer TaskbarCreated received; recreating tray icon");
                tray_remove(&tray);
                tray = tray_add(message_hwnd, instance, tray_message);
            } else if message.hwnd == message_hwnd && message.message == TRAY_OPEN_MESSAGE {
                let raw_event = message.lParam.0 as u32;
                let event = message.wParam.0 as u32;
                let callback_icon = raw_event >> 16;
                tray_log_line(format!(
                    "tray internal Open command received: event={}, icon_id={}, expected_uid={}",
                    tray_event_name(event), callback_icon, tray.uID
                ));
                if callback_icon != 0 && callback_icon != tray.uID as u32 {
                    tray_log_line("tray internal Open command rejected: icon ID does not match registered icon");
                } else {
                    open_dashboard(tray_event_name(event));
                }
            } else if message.hwnd == message_hwnd && message.message == TRAY_MENU_MESSAGE {
                let raw_event = message.lParam.0 as u32;
                let event = message.wParam.0 as u32;
                let callback_icon = raw_event >> 16;
                tray_log_line(format!(
                    "tray internal ContextMenu command received: event={}, icon_id={}, expected_uid={}",
                    tray_event_name(event), callback_icon, tray.uID
                ));
                if callback_icon != 0 && callback_icon != tray.uID as u32 {
                    tray_log_line("tray internal ContextMenu command rejected: icon ID does not match registered icon");
                } else {
                    show_tray_menu(message_hwnd);
                }
            } else if message.hwnd == message_hwnd && message.message == PIPE_MESSAGE {
                process_events(&queue, &mut avatar, &supervisor, message_hwnd);
            } else if message.hwnd == message_hwnd && message.message == FINAL_IDLE_MESSAGE {
                avatar.final_hold_expired(message.wParam.0 as u64);
            } else if message.hwnd == message_hwnd && message.message == IDLE_WEBM_FRAME_MESSAGE {
                avatar.idle_webm_frame_tick();
            } else if message.hwnd == message_hwnd && message.message == WM_COMMAND {
                let command_id = message.wParam.0 & 0xffff;
                tray_log_line(format!("tray menu command selected: id={}", command_id));
                match command_id {
                    ID_OPEN => {
                        tray_log_line("tray menu Open Ariadne dashboard selected");
                        open_dashboard("menu Open Ariadne dashboard");
                    }
                    ID_RESTART => {
                        tray_log_line("tray menu Restart Ariadne core selected");
                        supervisor.restart();
                    }
                    ID_SHOW_AVATAR => {
                        tray_log_line("tray menu Show avatar selected");
                        avatar.show();
                    }
                    ID_HIDE_AVATAR => {
                        tray_log_line("tray menu Hide avatar selected");
                        avatar.hide();
                    }
                    ID_EXIT => {
                        tray_log_line("tray menu Exit Ariadne selected");
                        PostQuitMessage(0)
                    }
                    _ => {}
                }
            }
            let _ = TranslateMessage(&message);
            DispatchMessageW(&message);
        }
        process_events(&queue, &mut avatar, &supervisor, message_hwnd);
        stop_pipe.store(true, Ordering::Release);
        wake_pipe_receiver();
        let _ = pipe_thread.join();
        supervisor.stop();
        tray_remove(&tray);
        let _ = DestroyWindow(avatar.hwnd);
        let _ = DestroyWindow(message_hwnd);
        let _ = CloseHandle(mutex);
        log_line("clean shutdown");
    }
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        log_line(format!("host fatal error: {}", error));
    }
}

#[cfg(test)]
mod tests {
    use super::{
        classify_tray_event, clamp_premultiplied_rgba, layout_height, premultiply_channel,
        premultiply_rgba, TrayAction, NIN_KEYSELECT_EVENT, NIN_SELECT_EVENT,
        rounded_bubble_pixel_inside, scaled_avatar_dimensions, state_status, AVATAR_MAX_HEIGHT,
        BUBBLE_CORNER_RADIUS, BUBBLE_MAX_HEIGHT,
    };

    #[test]
    fn tray_left_activation_opens_dashboard() {
        assert_eq!(classify_tray_event(super::WM_LBUTTONUP), TrayAction::Open);
        assert_eq!(classify_tray_event(NIN_SELECT_EVENT), TrayAction::Open);
        assert_eq!(classify_tray_event(NIN_KEYSELECT_EVENT), TrayAction::Open);
        assert_eq!(
            classify_tray_event(super::WM_LBUTTONDBLCLK),
            TrayAction::Open
        );
    }

    #[test]
    fn tray_right_activation_opens_menu() {
        assert_eq!(classify_tray_event(super::WM_RBUTTONUP), TrayAction::Menu);
        assert_eq!(
            classify_tray_event(super::WM_CONTEXTMENU),
            TrayAction::Menu
        );
    }

    #[test]
    fn unrelated_tray_messages_are_ignored() {
        assert_eq!(classify_tray_event(super::WM_MOUSEMOVE), TrayAction::Ignore);
    }

    #[test]
    fn tall_portrait_assets_are_scaled_to_avatar_limit() {
        assert_eq!(
            scaled_avatar_dimensions(800, 1600),
            (150, AVATAR_MAX_HEIGHT)
        );
    }

    #[test]
    fn short_assets_keep_their_natural_dimensions() {
        assert_eq!(scaled_avatar_dimensions(240, 180), (240, 180));
    }

    #[test]
    fn compact_layout_caps_the_combined_stack() {
        assert_eq!(layout_height(AVATAR_MAX_HEIGHT, true), 350);
        assert_eq!(layout_height(AVATAR_MAX_HEIGHT, false), 300);
        assert_eq!(BUBBLE_MAX_HEIGHT, 50);
    }

    #[test]
    fn state_events_supply_native_bubble_statuses() {
        assert_eq!(state_status("thinking"), Some("Thinking..."));
        assert_eq!(state_status("searching_vault"), Some("Searching the Vault..."));
        assert_eq!(state_status("cross_referencing"), Some("Cross-referencing..."));
        assert_eq!(state_status("loading_model"), Some("Loading model..."));
        assert_eq!(state_status("working"), Some("Working..."));
        assert_eq!(state_status("recovering"), Some("Recovering..."));
        assert_eq!(state_status("error"), Some("An error occurred."));
    }

    #[test]
    fn idle_and_speaking_wait_for_no_automatic_bubble() {
        assert_eq!(state_status("idle"), None);
        assert_eq!(state_status("speaking"), None);
    }

    #[test]
    fn bubble_corners_are_transparent_with_a_six_pixel_radius() {
        assert_eq!(BUBBLE_CORNER_RADIUS, 6);
        assert!(!rounded_bubble_pixel_inside(0, 0, 200, BUBBLE_MAX_HEIGHT));
        assert!(!rounded_bubble_pixel_inside(5, 0, 200, BUBBLE_MAX_HEIGHT));
        assert!(rounded_bubble_pixel_inside(6, 0, 200, BUBBLE_MAX_HEIGHT));
        assert!(rounded_bubble_pixel_inside(100, 25, 200, BUBBLE_MAX_HEIGHT));
        assert!(!rounded_bubble_pixel_inside(199, 49, 200, BUBBLE_MAX_HEIGHT));
    }

    #[test]
    fn premultiply_channel_preserves_opaque_and_clears_transparent_rgb() {
        assert_eq!(premultiply_channel(231, 255), 231);
        assert_eq!(premultiply_channel(231, 0), 0);
        assert_eq!(premultiply_channel(128, 128), 64);
    }

    #[test]
    fn transparent_pixels_are_premultiplied_before_resize() {
        let mut image = image::RgbaImage::from_vec(
            2,
            1,
            vec![255, 0, 0, 0, 100, 50, 25, 128],
        )
        .expect("test image dimensions match pixel data");
        premultiply_rgba(&mut image);
        assert_eq!(image.get_pixel(0, 0).0, [0, 0, 0, 0]);
        assert_eq!(image.get_pixel(1, 0).0, [50, 25, 13, 128]);
    }

    #[test]
    fn resized_premultiplied_pixels_cannot_contain_hidden_rgb() {
        let mut image = image::RgbaImage::from_vec(2, 1, vec![200, 100, 50, 0, 100, 60, 20, 40])
            .expect("test image dimensions match pixel data");
        clamp_premultiplied_rgba(&mut image);
        assert_eq!(image.get_pixel(0, 0).0, [0, 0, 0, 0]);
        assert_eq!(image.get_pixel(1, 0).0, [40, 40, 20, 40]);
    }
}
