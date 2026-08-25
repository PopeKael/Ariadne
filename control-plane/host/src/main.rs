#![cfg_attr(windows, windows_subsystem = "windows")]

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
use std::process::Command;
use std::ptr::null_mut;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use windows::core::{w, PCWSTR};
use windows::Win32::Foundation::{
    CloseHandle, GetLastError, ERROR_ALREADY_EXISTS, ERROR_PIPE_CONNECTED, GENERIC_WRITE, HANDLE,
    HINSTANCE, HWND, LPARAM, LRESULT, POINT, WPARAM,
};
use windows::Win32::Graphics::Gdi::{
    CreateCompatibleDC, CreateDIBSection, DeleteDC, DeleteObject, SelectObject, AC_SRC_ALPHA,
    BITMAPINFO, BITMAPINFOHEADER, BI_RGB, BLENDFUNCTION, DIB_RGB_COLORS,
};
use windows::Win32::Storage::FileSystem::{CreateFileW, ReadFile, WriteFile, PIPE_ACCESS_INBOUND};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::System::Pipes::{
    ConnectNamedPipe, CreateNamedPipeW, DisconnectNamedPipe, PIPE_READMODE_MESSAGE,
    PIPE_TYPE_MESSAGE, PIPE_WAIT,
};
use windows::Win32::System::Threading::TerminateProcess;
use windows::Win32::UI::Shell::{
    Shell_NotifyIconW, NIF_ICON, NIF_MESSAGE, NIF_TIP, NIM_ADD, NIM_DELETE, NIM_SETVERSION,
    NOTIFYICONDATAW, NOTIFYICON_VERSION_4,
};
use windows::Win32::UI::WindowsAndMessaging::{
    AppendMenuW, CreatePopupMenu, CreateWindowExW, DefWindowProcW, DestroyMenu, DestroyWindow,
    DispatchMessageW, GetCursorPos, GetMessageW, GetSystemMetrics, LoadCursorW, LoadIconW,
    PostMessageW, PostQuitMessage, RegisterClassW, SetForegroundWindow, SetWindowPos, ShowWindow,
    TrackPopupMenu, TranslateMessage, UpdateLayeredWindow, CS_HREDRAW, CS_VREDRAW, CW_USEDEFAULT,
    HTTRANSPARENT, IDC_ARROW, IDI_APPLICATION, MA_NOACTIVATE, MF_SEPARATOR, MF_STRING, MSG,
    SWP_NOACTIVATE, SWP_NOSIZE, SW_HIDE, SW_SHOWNOACTIVATE, TPM_RIGHTBUTTON, ULW_ALPHA, WM_APP,
    WM_CLOSE, WM_COMMAND, WM_DESTROY, WM_MOUSEACTIVATE, WM_NCHITTEST, WM_NULL, WNDCLASSW,
    WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_POPUP,
};

const PIPE_NAME: &str = r"\\.\pipe\ariadne-control";
const PIPE_NAME_W: PCWSTR = w!(r"\\.\pipe\ariadne-control");
const TRAY_MESSAGE: u32 = WM_APP + 10;
const PIPE_MESSAGE: u32 = WM_APP + 11;
const ID_OPEN: usize = 1001;
const ID_RESTART: usize = 1002;
const ID_SHOW_AVATAR: usize = 1003;
const ID_HIDE_AVATAR: usize = 1004;
const ID_EXIT: usize = 1005;
const HOST_MUTEX: PCWSTR = w!("Local\\AriadneHost");
const CREATE_NO_WINDOW_FLAGS: u32 = 0x0800_0000;

static LOG_LOCK: OnceLock<Mutex<()>> = OnceLock::new();

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
}

struct AvatarSettings {
    enabled: bool,
    asset_root: PathBuf,
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

fn find_project_root(exe: &Path) -> PathBuf {
    let mut current = exe.parent().unwrap_or_else(|| Path::new(".")).to_path_buf();
    for _ in 0..8 {
        if current.join("control-plane").join("server.py").is_file() {
            return current;
        }
        if !current.pop() {
            break;
        }
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
    handles: Arc<Mutex<Option<isize>>>,
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
    let Ok(child) = command.spawn() else {
        log_line("Python core failed to launch; core is offline");
        ui.push(UiEvent::CoreUnavailable(
            "Python core failed to launch.".into(),
        ));
        return None;
    };
    let handle = child.as_raw_handle() as isize;
    if let Ok(mut stored) = handles.lock() {
        *stored = Some(handle);
    }
    let wait_events = ui.clone();
    let wait_handles = handles.clone();
    let (wait_tx, wait_rx) = std::sync::mpsc::channel();
    thread::spawn(move || {
        let mut owned_child = child;
        let status = owned_child.wait().ok().and_then(|value| value.code());
        if let Ok(mut stored) = wait_handles.lock() {
            *stored = None;
        }
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
    Some(ProcessState { wait_rx })
}

struct ProcessState {
    wait_rx: std::sync::mpsc::Receiver<()>,
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
        let handles = Arc::new(Mutex::new(None));
        let worker_handles = handles.clone();
        let join = thread::spawn(move || {
            let mut process = spawn_core(&project_root, ui.clone(), worker_handles.clone());
            loop {
                if let Some(current) = process.as_ref() {
                    if current.wait_rx.try_recv().is_ok() {
                        process = None;
                    }
                }
                match rx.recv_timeout(Duration::from_millis(500)) {
                    Ok(SupervisorCommand::Restart) => {
                        log_line("restart requested");
                        terminate_core(&worker_handles);
                        if let Some(current) = process.take() {
                            let _ = current.wait_rx.recv_timeout(Duration::from_secs(10));
                        }
                        process = spawn_core(&project_root, ui.clone(), worker_handles.clone());
                    }
                    Ok(SupervisorCommand::Stop)
                    | Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {
                        terminate_core(&worker_handles);
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

fn terminate_core(handles: &Arc<Mutex<Option<isize>>>) {
    let handle = handles.lock().ok().and_then(|stored| *stored);
    if let Some(handle) = handle {
        unsafe {
            let _ = TerminateProcess(HANDLE(handle as *mut c_void), 0);
        }
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

struct AvatarOverlay {
    hwnd: HWND,
    executable: PathBuf,
    project_root: PathBuf,
    asset_root: PathBuf,
    manifest: AvatarManifest,
    position: AvatarPosition,
    state: String,
    enabled: bool,
    logged_missing: HashSet<String>,
}

impl AvatarOverlay {
    unsafe fn new(
        instance: HINSTANCE,
        executable: PathBuf,
        project_root: PathBuf,
    ) -> windows::core::Result<Self> {
        let hwnd = CreateWindowExW(
            WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT,
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
        let position = load_position();
        let settings = load_avatar_settings(&executable, &project_root);
        Ok(Self {
            hwnd,
            executable,
            project_root,
            asset_root: settings.asset_root.clone(),
            manifest: load_manifest(&settings.asset_root),
            position,
            state: "idle".into(),
            enabled: settings.enabled,
            logged_missing: HashSet::new(),
        })
    }

    fn show(&self) {
        if !self.enabled {
            return;
        }
        unsafe {
            let _ = ShowWindow(self.hwnd, SW_SHOWNOACTIVATE);
        }
    }
    fn hide(&self) {
        unsafe {
            let _ = ShowWindow(self.hwnd, SW_HIDE);
        }
    }

    fn set_position(&mut self, x: i32, y: i32) {
        self.position = AvatarPosition { x, y };
        save_position(&self.position);
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

    fn reload_from_configuration(&mut self) {
        let settings = load_avatar_settings(&self.executable, &self.project_root);
        self.enabled = settings.enabled;
        self.asset_root = settings.asset_root;
        self.manifest = load_manifest(&self.asset_root);
        self.logged_missing.clear();
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
        self.state = state.to_string();
        if !self.enabled {
            self.hide();
            return;
        }
        if self.render_state_asset(state) {
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
    }

    fn render_png(&mut self, path: &Path) -> Result<(), String> {
        let image = image::open(path)
            .map_err(|error| error.to_string())?
            .to_rgba8();
        let (width, height) = image.dimensions();
        if width == 0 || height == 0 || width > 1600 || height > 1600 {
            return Err("unsupported image dimensions".into());
        }
        let mut bgra = Vec::with_capacity((width * height * 4) as usize);
        for pixel in image.pixels() {
            bgra.extend_from_slice(&[pixel[2], pixel[1], pixel[0], pixel[3]]);
        }
        unsafe {
            let mut info = BITMAPINFO {
                bmiHeader: BITMAPINFOHEADER {
                    biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
                    biWidth: width as i32,
                    biHeight: -(height as i32),
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
            let source = POINT { x: 0, y: 0 };
            let mut destination = POINT {
                x: self.position.x,
                y: self.position.y,
            };
            if self.position.x == 0 && self.position.y == 0 {
                destination.x =
                    (GetSystemMetrics(windows::Win32::UI::WindowsAndMessaging::SM_CXSCREEN)
                        - width as i32
                        - 32)
                        .max(0);
                destination.y =
                    (GetSystemMetrics(windows::Win32::UI::WindowsAndMessaging::SM_CYSCREEN)
                        - height as i32
                        - 64)
                        .max(0);
                self.position = AvatarPosition {
                    x: destination.x,
                    y: destination.y,
                };
                save_position(&self.position);
            }
            let size = windows::Win32::Foundation::SIZE {
                cx: width as i32,
                cy: height as i32,
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

fn load_position() -> AvatarPosition {
    fs::read_to_string(position_path())
        .ok()
        .and_then(|text| serde_json::from_str(&text).ok())
        .unwrap_or_default()
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
    _wparam: WPARAM,
    _lparam: LPARAM,
) -> LRESULT {
    match message {
        WM_CLOSE => {
            let _ = ShowWindow(hwnd, SW_HIDE);
            LRESULT(0)
        }
        WM_MOUSEACTIVATE => LRESULT(MA_NOACTIVATE as isize),
        WM_NCHITTEST => LRESULT(HTTRANSPARENT as isize),
        WM_DESTROY => LRESULT(0),
        _ => DefWindowProcW(hwnd, message, _wparam, _lparam),
    }
}

unsafe extern "system" fn host_window_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
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

unsafe fn tray_add(hwnd: HWND) -> NOTIFYICONDATAW {
    let icon = LoadIconW(None, IDI_APPLICATION).unwrap_or_default();
    let mut data = NOTIFYICONDATAW {
        cbSize: std::mem::size_of::<NOTIFYICONDATAW>() as u32,
        hWnd: hwnd,
        uID: 1,
        uFlags: NIF_MESSAGE | NIF_ICON | NIF_TIP,
        uCallbackMessage: TRAY_MESSAGE,
        hIcon: icon,
        ..Default::default()
    };
    let tip: Vec<u16> = "Ariadne — Local AI Control Plane\0"
        .encode_utf16()
        .collect();
    for (target, value) in data.szTip.iter_mut().zip(tip) {
        *target = value;
    }
    let _ = Shell_NotifyIconW(NIM_ADD, &data);
    data.Anonymous.uVersion = NOTIFYICON_VERSION_4;
    let _ = Shell_NotifyIconW(NIM_SETVERSION, &data);
    data
}

unsafe fn tray_remove(data: &NOTIFYICONDATAW) {
    let _ = Shell_NotifyIconW(NIM_DELETE, data);
}

unsafe fn show_tray_menu(hwnd: HWND) {
    let Ok(menu) = CreatePopupMenu() else {
        log_line("could not create tray menu");
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
    let _ = GetCursorPos(&mut point);
    let _ = SetForegroundWindow(hwnd);
    let _ = TrackPopupMenu(menu, TPM_RIGHTBUTTON, point.x, point.y, Some(0), hwnd, None);
    let _ = PostMessageW(Some(hwnd), WM_NULL, WPARAM(0), LPARAM(0));
    let _ = DestroyMenu(menu);
}

fn open_dashboard() {
    let _ = Command::new("cmd")
        .args(["/C", "start", "", "http://127.0.0.1:8765/"])
        .spawn();
}

fn process_events(queue: &UiQueue, avatar: &mut AvatarOverlay, supervisor: &CoreSupervisor) {
    for event in queue.drain() {
        match event {
            UiEvent::Pipe(message) => match message.kind.as_str() {
                "state" => {
                    if let Some(state) = message.state.as_deref() {
                        avatar.set_state(state);
                    }
                }
                "say" => {
                    if let Some(text) = message.text {
                        log_line(format!(
                            "IPC say event received ({} chars)",
                            text.chars().count()
                        ));
                    }
                }
                "show" => avatar.show(),
                "hide" => avatar.hide(),
                "reload_avatar" => avatar.reload_from_configuration(),
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
        let instance = HINSTANCE(GetModuleHandleW(None).map_err(|error| error.to_string())?.0);
        register_windows(instance).map_err(|error| error.to_string())?;
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
        let stop_pipe = Arc::new(AtomicBool::new(false));
        let pipe_thread = pipe_receiver(queue.clone(), stop_pipe.clone());
        let supervisor = CoreSupervisor::start(project_root.clone(), queue.clone());
        let mut avatar =
            AvatarOverlay::new(instance, exe, project_root).map_err(|error| error.to_string())?;
        avatar.set_state("idle");
        if avatar.enabled {
            avatar.show();
        } else {
            avatar.hide();
        }
        let tray = tray_add(message_hwnd);
        let mut message = MSG::default();
        while GetMessageW(&mut message, None, 0, 0).0 > 0 {
            if message.hwnd == message_hwnd && message.message == TRAY_MESSAGE {
                match message.lParam.0 as u32 {
                    windows::Win32::UI::WindowsAndMessaging::WM_RBUTTONUP
                    | windows::Win32::UI::WindowsAndMessaging::WM_CONTEXTMENU => {
                        show_tray_menu(message_hwnd)
                    }
                    windows::Win32::UI::WindowsAndMessaging::WM_LBUTTONDBLCLK => open_dashboard(),
                    _ => {}
                }
            } else if message.hwnd == message_hwnd && message.message == PIPE_MESSAGE {
                process_events(&queue, &mut avatar, &supervisor);
            } else if message.hwnd == message_hwnd && message.message == WM_COMMAND {
                match message.wParam.0 & 0xffff {
                    ID_OPEN => open_dashboard(),
                    ID_RESTART => supervisor.restart(),
                    ID_SHOW_AVATAR => avatar.show(),
                    ID_HIDE_AVATAR => avatar.hide(),
                    ID_EXIT => PostQuitMessage(0),
                    _ => {}
                }
            }
            let _ = TranslateMessage(&message);
            DispatchMessageW(&message);
        }
        process_events(&queue, &mut avatar, &supervisor);
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
