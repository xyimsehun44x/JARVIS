use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::env;
use std::fs;
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager, State};
use tauri_plugin_global_shortcut::{
    GlobalShortcutExt, Shortcut, ShortcutState as GlobalShortcutState,
};

const PROTOCOL_VERSION: u64 = 1;
const MAIN_WINDOW_LABEL: &str = "main";
const TRAY_ID: &str = "jarvis-tray";
const TRAY_SHOW_ID: &str = "tray-show";
const TRAY_HIDE_ID: &str = "tray-hide";
const TRAY_QUIT_ID: &str = "tray-quit";
const DEFAULT_TOGGLE_SHORTCUT: &str = "Ctrl+Alt+J";
const PREFERENCES_VERSION: u32 = 1;
const PREFERENCES_FILE_NAME: &str = "preferences.json";

#[derive(Clone, Serialize)]
struct BackendExit {
    code: Option<i32>,
}

struct BackendState {
    child: Arc<Mutex<Option<Child>>>,
    input: Arc<Mutex<Option<BufWriter<ChildStdin>>>>,
}

#[derive(Default)]
struct AppLifecycle {
    quitting: AtomicBool,
}

#[derive(Clone, Serialize)]
struct DesktopHotkeyStatus {
    shortcut: String,
    registered: bool,
    error: Option<String>,
    source: String,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
struct DesktopPreferences {
    version: u32,
    toggle_shortcut: String,
}

impl DesktopPreferences {
    fn new(toggle_shortcut: String) -> Self {
        Self {
            version: PREFERENCES_VERSION,
            toggle_shortcut,
        }
    }
}

struct HotkeyState {
    status: Mutex<DesktopHotkeyStatus>,
    update: Mutex<()>,
    preferences_path: PathBuf,
    pressed: AtomicBool,
    registered_id: AtomicU32,
}

impl HotkeyState {
    fn new(shortcut: String, source: String, preferences_path: PathBuf) -> Self {
        Self {
            status: Mutex::new(DesktopHotkeyStatus {
                shortcut,
                registered: false,
                error: None,
                source,
            }),
            update: Mutex::new(()),
            preferences_path,
            pressed: AtomicBool::new(false),
            registered_id: AtomicU32::new(0),
        }
    }

    fn status(&self) -> DesktopHotkeyStatus {
        self.status
            .lock()
            .map(|status| status.clone())
            .unwrap_or_else(|_| DesktopHotkeyStatus {
                shortcut: DEFAULT_TOGGLE_SHORTCUT.to_string(),
                registered: false,
                error: Some("Global shortcut status is unavailable.".to_string()),
                source: "default".to_string(),
            })
    }

    fn registration_succeeded(&self, shortcut: String, shortcut_id: u32, source: &str) {
        self.registered_id.store(shortcut_id, Ordering::SeqCst);
        if let Ok(mut status) = self.status.lock() {
            status.shortcut = shortcut;
            status.registered = true;
            status.error = None;
            status.source = source.to_string();
        }
    }

    fn registration_failed(&self, error: String) {
        if let Ok(mut status) = self.status.lock() {
            status.registered = false;
            status.error = Some(error);
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum TrayAction {
    Show,
    Hide,
    Quit,
}

impl TrayAction {
    fn from_menu_id(id: &str) -> Option<Self> {
        match id {
            TRAY_SHOW_ID => Some(Self::Show),
            TRAY_HIDE_ID => Some(Self::Hide),
            TRAY_QUIT_ID => Some(Self::Quit),
            _ => None,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum CloseAction {
    Hide,
    Exit,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum WindowToggleAction {
    Show,
    Hide,
}

fn close_action(quitting: bool) -> CloseAction {
    if quitting {
        CloseAction::Exit
    } else {
        CloseAction::Hide
    }
}

fn window_toggle_action(visible: bool, minimized: bool, focused: bool) -> WindowToggleAction {
    if visible && !minimized && focused {
        WindowToggleAction::Hide
    } else {
        WindowToggleAction::Show
    }
}

impl BackendState {
    fn spawn(app: AppHandle) -> Result<Self, String> {
        let project_root = project_root()?;
        let python = env::var_os("JARVIS_PYTHON_EXECUTABLE")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("python"));
        let mut command = Command::new(python);
        command
            .arg("main.py")
            .arg("--ipc-stdio")
            .current_dir(project_root)
            .env("PYTHONUTF8", "1")
            .env("PYTHONIOENCODING", "utf-8")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            command.creation_flags(CREATE_NO_WINDOW);
        }

        let mut child = command
            .spawn()
            .map_err(|error| format!("Could not start the Jarvis backend: {error}"))?;
        let input = child
            .stdin
            .take()
            .ok_or_else(|| "Jarvis backend stdin was unavailable.".to_string())?;
        let output = child
            .stdout
            .take()
            .ok_or_else(|| "Jarvis backend stdout was unavailable.".to_string())?;
        let errors = child
            .stderr
            .take()
            .ok_or_else(|| "Jarvis backend stderr was unavailable.".to_string())?;

        let child = Arc::new(Mutex::new(Some(child)));
        let input = Arc::new(Mutex::new(Some(BufWriter::new(input))));

        let output_app = app.clone();
        thread::spawn(move || {
            let mut reader = BufReader::new(output);
            let mut line = Vec::new();
            loop {
                line.clear();
                match reader.read_until(b'\n', &mut line) {
                    Ok(0) => break,
                    Ok(_) => match serde_json::from_slice::<Value>(&line) {
                        Ok(message) => {
                            let _ = output_app.emit("jarvis-ipc", message);
                        }
                        Err(_) => eprintln!("Jarvis backend emitted a non-protocol stdout line."),
                    },
                    Err(error) => {
                        eprintln!("Could not read Jarvis backend output: {error}");
                        break;
                    }
                }
            }
        });

        thread::spawn(move || {
            let mut reader = BufReader::new(errors);
            let mut line = Vec::new();
            loop {
                line.clear();
                match reader.read_until(b'\n', &mut line) {
                    Ok(0) => break,
                    Ok(_) => eprint!("[jarvis-backend] {}", String::from_utf8_lossy(&line)),
                    Err(error) => {
                        eprintln!("Could not read Jarvis backend diagnostics: {error}");
                        break;
                    }
                }
            }
        });

        let monitored_child = Arc::clone(&child);
        thread::spawn(move || loop {
            let exit = {
                let mut guard = match monitored_child.lock() {
                    Ok(guard) => guard,
                    Err(_) => return,
                };
                let Some(process) = guard.as_mut() else {
                    return;
                };
                match process.try_wait() {
                    Ok(status) => status,
                    Err(error) => {
                        eprintln!("Could not monitor Jarvis backend: {error}");
                        return;
                    }
                }
            };
            if let Some(status) = exit {
                let _ = app.emit(
                    "jarvis-backend-exit",
                    BackendExit {
                        code: status.code(),
                    },
                );
                return;
            }
            thread::sleep(Duration::from_millis(250));
        });

        Ok(Self { child, input })
    }

    fn send(&self, request: Value) -> Result<(), String> {
        validate_request_envelope(&request)?;
        let mut input = self
            .input
            .lock()
            .map_err(|_| "Jarvis backend input lock failed.".to_string())?;
        let writer = input
            .as_mut()
            .ok_or_else(|| "Jarvis backend is not running.".to_string())?;
        serde_json::to_writer(&mut *writer, &request)
            .map_err(|error| format!("Could not serialize backend request: {error}"))?;
        writer
            .write_all(b"\n")
            .and_then(|_| writer.flush())
            .map_err(|error| format!("Could not write to Jarvis backend: {error}"))
    }

    fn stop(&self) {
        let _ = self.send(json!({
            "protocol_version": PROTOCOL_VERSION,
            "request_id": "desktop-exit",
            "method": "shutdown",
            "params": {}
        }));
        if let Ok(mut input) = self.input.lock() {
            input.take();
        }

        for _ in 0..10 {
            let exited = self
                .child
                .lock()
                .ok()
                .and_then(|mut guard| guard.as_mut().and_then(|child| child.try_wait().ok()))
                .flatten()
                .is_some();
            if exited {
                return;
            }
            thread::sleep(Duration::from_millis(50));
        }
        if let Ok(mut guard) = self.child.lock() {
            if let Some(child) = guard.as_mut() {
                let _ = child.kill();
                let _ = child.wait();
            }
            guard.take();
        }
    }
}

impl Drop for BackendState {
    fn drop(&mut self) {
        self.stop();
    }
}

fn project_root() -> Result<PathBuf, String> {
    let path = env::var_os("JARVIS_PROJECT_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../.."));
    path.canonicalize()
        .map_err(|error| format!("Could not resolve Jarvis project root: {error}"))
}

fn validate_request_envelope(request: &Value) -> Result<(), String> {
    let object = request
        .as_object()
        .ok_or_else(|| "Backend request must be a JSON object.".to_string())?;
    if object.get("protocol_version").and_then(Value::as_u64) != Some(PROTOCOL_VERSION) {
        return Err("Backend request must use protocol version 1.".to_string());
    }
    if object.get("request_id").and_then(Value::as_str).is_none() {
        return Err("Backend request requires a request_id.".to_string());
    }
    if object.get("method").and_then(Value::as_str).is_none() {
        return Err("Backend request requires a method.".to_string());
    }
    Ok(())
}

#[tauri::command]
fn backend_send(request: Value, state: State<'_, BackendState>) -> Result<(), String> {
    state.send(request)
}

#[tauri::command]
fn desktop_hotkey_status(state: State<'_, HotkeyState>) -> DesktopHotkeyStatus {
    state.status()
}

#[tauri::command]
fn set_desktop_hotkey(
    app: AppHandle,
    shortcut: String,
    state: State<'_, HotkeyState>,
) -> Result<DesktopHotkeyStatus, String> {
    update_toggle_shortcut(&app, &shortcut, &state)
}

fn show_main_window<R: tauri::Runtime>(app: &AppHandle<R>) -> Result<(), String> {
    let window = app
        .get_webview_window(MAIN_WINDOW_LABEL)
        .ok_or_else(|| "Jarvis main window is unavailable.".to_string())?;
    if window
        .is_minimized()
        .map_err(|error| format!("Could not inspect the Jarvis window: {error}"))?
    {
        window
            .unminimize()
            .map_err(|error| format!("Could not restore the Jarvis window: {error}"))?;
    }
    window
        .show()
        .map_err(|error| format!("Could not show the Jarvis window: {error}"))?;
    window
        .set_focus()
        .map_err(|error| format!("Could not focus the Jarvis window: {error}"))
}

fn hide_main_window<R: tauri::Runtime>(app: &AppHandle<R>) -> Result<(), String> {
    app.get_webview_window(MAIN_WINDOW_LABEL)
        .ok_or_else(|| "Jarvis main window is unavailable.".to_string())?
        .hide()
        .map_err(|error| format!("Could not hide the Jarvis window: {error}"))
}

fn toggle_main_window<R: tauri::Runtime>(app: &AppHandle<R>) -> Result<(), String> {
    let window = app
        .get_webview_window(MAIN_WINDOW_LABEL)
        .ok_or_else(|| "Jarvis main window is unavailable.".to_string())?;
    let visible = window
        .is_visible()
        .map_err(|error| format!("Could not inspect the Jarvis window: {error}"))?;
    let minimized = window
        .is_minimized()
        .map_err(|error| format!("Could not inspect the Jarvis window: {error}"))?;
    let focused = window
        .is_focused()
        .map_err(|error| format!("Could not inspect the Jarvis window: {error}"))?;
    match window_toggle_action(visible, minimized, focused) {
        WindowToggleAction::Show => show_main_window(app),
        WindowToggleAction::Hide => hide_main_window(app),
    }
}

fn preferences_path<R: tauri::Runtime>(app: &tauri::App<R>) -> Result<PathBuf, String> {
    app.path()
        .app_config_dir()
        .map(|directory| directory.join(PREFERENCES_FILE_NAME))
        .map_err(|error| format!("Could not resolve the Jarvis preferences directory: {error}"))
}

fn load_preferences(path: &Path) -> Result<Option<DesktopPreferences>, String> {
    if !path.exists() {
        return Ok(None);
    }
    let contents = fs::read_to_string(path)
        .map_err(|error| format!("Could not read desktop preferences: {error}"))?;
    let preferences: DesktopPreferences = serde_json::from_str(&contents)
        .map_err(|error| format!("Desktop preferences are invalid: {error}"))?;
    if preferences.version != PREFERENCES_VERSION {
        return Err(format!(
            "Desktop preferences version {} is unsupported",
            preferences.version
        ));
    }
    if preferences.toggle_shortcut.trim().is_empty() {
        return Err("Desktop preferences contain an empty shortcut".to_string());
    }
    parse_shortcut(&preferences.toggle_shortcut)
        .map_err(|error| format!("Desktop preferences contain an invalid shortcut: {error}"))?;
    Ok(Some(preferences))
}

fn save_preferences(path: &Path, shortcut: &str) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| "Desktop preferences path has no parent directory".to_string())?;
    fs::create_dir_all(parent)
        .map_err(|error| format!("Could not create the desktop preferences directory: {error}"))?;
    let contents = serde_json::to_vec_pretty(&DesktopPreferences::new(shortcut.to_string()))
        .map_err(|error| format!("Could not serialize desktop preferences: {error}"))?;
    fs::write(path, contents)
        .map_err(|error| format!("Could not save desktop preferences: {error}"))
}

fn configured_shortcut(path: &Path) -> (String, String) {
    match load_preferences(path) {
        Ok(Some(preferences)) => return (preferences.toggle_shortcut, "saved".to_string()),
        Ok(None) => {}
        Err(error) => eprintln!("{error}. Using a safe fallback shortcut."),
    }
    if let Ok(shortcut) = env::var("JARVIS_DESKTOP_HOTKEY") {
        if !shortcut.trim().is_empty() {
            return (shortcut.trim().to_string(), "environment".to_string());
        }
    }
    (DEFAULT_TOGGLE_SHORTCUT.to_string(), "default".to_string())
}

fn parse_shortcut(value: &str) -> Result<Shortcut, String> {
    let shortcut = value
        .parse::<Shortcut>()
        .map_err(|error| format!("Invalid shortcut {value}: {error}"))?;
    if shortcut.mods.is_empty() {
        return Err("A global shortcut must include Ctrl, Alt, Shift, or Super.".to_string());
    }
    Ok(shortcut)
}

fn update_toggle_shortcut<R: tauri::Runtime>(
    app: &AppHandle<R>,
    requested: &str,
    state: &HotkeyState,
) -> Result<DesktopHotkeyStatus, String> {
    let _update = state
        .update
        .lock()
        .map_err(|_| "Global shortcut update is already unavailable".to_string())?;
    let requested = requested.trim();
    if requested.is_empty() {
        return Err("Enter a shortcut such as Ctrl+Alt+J.".to_string());
    }
    if requested.len() > 64 {
        return Err("The shortcut must be 64 characters or fewer.".to_string());
    }
    let candidate = parse_shortcut(requested)?;
    let current = state.status();
    let current_id = state.registered_id.load(Ordering::SeqCst);

    if current.registered && candidate.id() == current_id {
        save_preferences(&state.preferences_path, requested)?;
        state.registration_succeeded(requested.to_string(), candidate.id(), "saved");
        return Ok(state.status());
    }

    app.global_shortcut()
        .register(candidate)
        .map_err(|error| format!("Could not register {requested}: {error}"))?;

    if let Err(error) = save_preferences(&state.preferences_path, requested) {
        let _ = app.global_shortcut().unregister(candidate);
        return Err(error);
    }

    if current.registered {
        let previous = current.shortcut.parse::<Shortcut>().map_err(|error| {
            let _ = app.global_shortcut().unregister(candidate);
            format!("Could not preserve the previous shortcut: {error}")
        })?;
        if let Err(error) = app.global_shortcut().unregister(previous) {
            let _ = app.global_shortcut().unregister(candidate);
            let _ = save_preferences(&state.preferences_path, &current.shortcut);
            return Err(format!(
                "Could not replace {}: {error}. The previous shortcut remains active.",
                current.shortcut
            ));
        }
    }

    state.pressed.store(false, Ordering::SeqCst);
    state.registration_succeeded(requested.to_string(), candidate.id(), "saved");
    Ok(state.status())
}

fn register_toggle_shortcut<R: tauri::Runtime>(app: &tauri::App<R>) {
    let preferences_path = match preferences_path(app) {
        Ok(path) => path,
        Err(error) => {
            eprintln!("{error}. Shortcut changes will not be persisted.");
            PathBuf::new()
        }
    };
    let (configured, source) = configured_shortcut(&preferences_path);
    let state = HotkeyState::new(configured.clone(), source.clone(), preferences_path);
    let shortcut = parse_shortcut(&configured);
    app.manage(state);

    let result = shortcut.and_then(|shortcut| {
        app.global_shortcut()
            .register(shortcut)
            .map(|_| shortcut.id())
            .map_err(|error| format!("Could not register {configured}: {error}"))
    });
    let state = app.state::<HotkeyState>();
    match result {
        Ok(shortcut_id) => state.registration_succeeded(configured, shortcut_id, &source),
        Err(error) => {
            eprintln!("{error}. Tray controls remain available.");
            state.registration_failed(error);
        }
    }
}

fn quit<R: tauri::Runtime>(app: &AppHandle<R>) {
    let lifecycle = app.state::<AppLifecycle>();
    if lifecycle.quitting.swap(true, Ordering::SeqCst) {
        return;
    }
    if let Err(error) = app.global_shortcut().unregister_all() {
        eprintln!("Could not unregister the Jarvis global shortcut: {error}");
    }
    app.state::<BackendState>().stop();
    app.exit(0);
}

fn handle_tray_action<R: tauri::Runtime>(app: &AppHandle<R>, action: TrayAction) {
    let result = match action {
        TrayAction::Show => show_main_window(app),
        TrayAction::Hide => hide_main_window(app),
        TrayAction::Quit => {
            quit(app);
            Ok(())
        }
    };
    if let Err(error) = result {
        eprintln!("{error}");
    }
}

fn setup_tray<R: tauri::Runtime>(app: &tauri::App<R>) -> tauri::Result<()> {
    let show = MenuItem::with_id(app, TRAY_SHOW_ID, "Show Jarvis", true, None::<&str>)?;
    let hide = MenuItem::with_id(app, TRAY_HIDE_ID, "Hide Jarvis", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let quit_item = MenuItem::with_id(app, TRAY_QUIT_ID, "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show, &hide, &separator, &quit_item])?;

    let mut tray = TrayIconBuilder::with_id(TRAY_ID)
        .menu(&menu)
        .show_menu_on_left_click(false)
        .tooltip("Jarvis")
        .on_menu_event(|app, event| {
            if let Some(action) = TrayAction::from_menu_id(event.id().as_ref()) {
                handle_tray_action(app, action);
            }
        })
        .on_tray_icon_event(|tray, event| {
            let should_show = matches!(
                event,
                TrayIconEvent::Click {
                    button: MouseButton::Left,
                    button_state: MouseButtonState::Up,
                    ..
                } | TrayIconEvent::DoubleClick {
                    button: MouseButton::Left,
                    ..
                }
            );
            if should_show {
                handle_tray_action(tray.app_handle(), TrayAction::Show);
            }
        });
    if let Some(icon) = app.default_window_icon() {
        tray = tray.icon(icon.clone());
    }
    tray.build(app)?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, _shortcut, event| {
                    let state = app.state::<HotkeyState>();
                    if event.id != state.registered_id.load(Ordering::SeqCst) {
                        return;
                    }
                    match event.state {
                        GlobalShortcutState::Pressed => {
                            if !state.pressed.swap(true, Ordering::SeqCst) {
                                if let Err(error) = toggle_main_window(app) {
                                    eprintln!("{error}");
                                }
                            }
                        }
                        GlobalShortcutState::Released => {
                            state.pressed.store(false, Ordering::SeqCst);
                        }
                    }
                })
                .build(),
        )
        .setup(|app| {
            app.manage(AppLifecycle::default());
            let backend = BackendState::spawn(app.handle().clone())?;
            app.manage(backend);
            setup_tray(app)?;
            register_toggle_shortcut(app);
            Ok(())
        })
        .on_window_event(|window, event| {
            if window.label() != MAIN_WINDOW_LABEL {
                return;
            }
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                let quitting = window
                    .state::<AppLifecycle>()
                    .quitting
                    .load(Ordering::SeqCst);
                if close_action(quitting) == CloseAction::Hide {
                    api.prevent_close();
                    if let Err(error) = window.hide() {
                        eprintln!("Could not hide Jarvis in the system tray: {error}");
                    }
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            backend_send,
            desktop_hotkey_status,
            set_desktop_hotkey
        ])
        .run(tauri::generate_context!())
        .expect("error while running Jarvis desktop");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validates_protocol_request_envelopes() {
        assert!(validate_request_envelope(&json!({
            "protocol_version": 1,
            "request_id": "request-1",
            "method": "health",
            "params": {}
        }))
        .is_ok());
        assert!(validate_request_envelope(&json!({
            "protocol_version": 2,
            "request_id": "request-1",
            "method": "health"
        }))
        .is_err());
    }

    #[test]
    fn maps_only_known_tray_menu_items() {
        assert_eq!(
            TrayAction::from_menu_id(TRAY_SHOW_ID),
            Some(TrayAction::Show)
        );
        assert_eq!(
            TrayAction::from_menu_id(TRAY_HIDE_ID),
            Some(TrayAction::Hide)
        );
        assert_eq!(
            TrayAction::from_menu_id(TRAY_QUIT_ID),
            Some(TrayAction::Quit)
        );
        assert_eq!(TrayAction::from_menu_id("unknown"), None);
    }

    #[test]
    fn ordinary_close_hides_but_explicit_quit_exits() {
        assert_eq!(close_action(false), CloseAction::Hide);
        assert_eq!(close_action(true), CloseAction::Exit);
    }

    #[test]
    fn toggle_hides_only_the_visible_focused_window() {
        assert_eq!(
            window_toggle_action(true, false, true),
            WindowToggleAction::Hide
        );
        assert_eq!(
            window_toggle_action(true, false, false),
            WindowToggleAction::Show
        );
        assert_eq!(
            window_toggle_action(true, true, false),
            WindowToggleAction::Show
        );
        assert_eq!(
            window_toggle_action(false, false, false),
            WindowToggleAction::Show
        );
    }

    #[test]
    fn default_shortcut_has_a_valid_global_shortcut_shape() {
        assert!(parse_shortcut(DEFAULT_TOGGLE_SHORTCUT).is_ok());
        assert!(parse_shortcut("Ctrl+Alt").is_err());
        assert!(parse_shortcut("J").is_err());
    }

    #[test]
    fn desktop_preferences_store_only_the_version_and_shortcut() {
        let preferences = DesktopPreferences::new("Ctrl+Shift+J".to_string());
        let serialized = serde_json::to_string(&preferences).expect("serialize preferences");
        let restored: DesktopPreferences =
            serde_json::from_str(&serialized).expect("deserialize preferences");

        assert_eq!(restored, preferences);
        assert!(serialized.contains("toggle_shortcut"));
        assert!(!serialized.contains("api_key"));
        assert!(!serialized.contains("transcript"));
        assert!(!serialized.contains("approval"));
    }
}
