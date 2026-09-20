# Jarvis desktop

This is the Windows-first Tauri 2 client for the existing Python backend. It keeps the
backend in `../jarvis/` and launches `python ../main.py --ipc-stdio` as a child process.
No network port is opened.

Development prerequisites follow the official Tauri Windows setup: Node.js, the Rust
MSVC toolchain, Microsoft C++ Build Tools, and WebView2.

```powershell
cd desktop
npm.cmd install
npm.cmd run tauri dev
```

PowerShell may block the `npm.ps1` shim under a restrictive execution policy; using
`npm.cmd` invokes the same installed npm CLI without changing that policy.

The microphone button is click-to-start/click-to-stop push-to-talk. The backend loads the
local Whisper and Kokoro models while the app shows `Starting backend`; the microphone is
enabled only when they are ready and then starts capture immediately. Stopping capture
transcribes and normalizes the utterance, adds it to the same persistent desktop thread,
streams the answer into the transcript, and plays it through Kokoro. Install the optional
voice dependencies if the microphone is disabled:

```powershell
python -m pip install -e ".[voice]"
```

Closing the Jarvis window hides it in the system tray without stopping the Python backend
or unloading the voice models. Left-click the tray icon, or choose **Show Jarvis**, to
restore and focus the existing window. Choose **Quit** from the tray menu to shut down the
Python child gracefully and exit the desktop application.

`Ctrl+Alt+J` globally shows or hides the existing Jarvis window. If Jarvis is visible but
another application has focus, the shortcut brings Jarvis forward rather than hiding it.
Use the gear in the application header to inspect backend/model/voice availability and
change the shortcut. A conflicting or invalid replacement leaves the previous shortcut
active. Successful changes are stored in
`%APPDATA%\com.jarvis.desktop\preferences.json`; that file contains only a version and
shortcut. It takes precedence over the optional `JARVIS_DESKTOP_HOTKEY` launch default:

```powershell
$env:JARVIS_DESKTOP_HOTKEY = "Ctrl+Shift+J"
npm.cmd run tauri dev
```

An invalid shortcut or a combination already owned by another application is reported
without stopping Jarvis; the previous shortcut and tray controls continue to work. The
settings panel displays Gmail-send and Calendar-write locks but cannot change them.

The child process uses `python` by default. Set `JARVIS_PYTHON_EXECUTABLE` to an explicit
Python executable if the command is not on `PATH`. Set `JARVIS_PROJECT_ROOT` only when
running the desktop binary away from the repository during development. Packaging the
Python backend as a Tauri sidecar is intentionally deferred until the development
lifecycle is stable.

The Tauri child process forces UTF-8 for the JSONL protocol independently of the Windows
system locale. The Rust reader also drops an invalid protocol line without closing the
backend output pipe, preventing one encoding fault from leaving the UI indefinitely busy.
