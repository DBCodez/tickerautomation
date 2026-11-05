"""
Trade-Ideas -> Bookmap & DilutionTracker Deep-Dive Automation Utility (v1.8)
============================================================================
A lightweight, always-on-top dark-mode Windows background utility that:
1. Exposes a native Win32 "Edit" control targetable by Trade-Ideas External Linking
   AND directly editable by hand (click the TI LINK box and type any ticker).
2. Silently stages tickers on single-click or manual typing (Staged: XXXX) without
   triggering automation and without any Windows system beep
   (via ES_MULTILINE | ES_WANTRETURN | ES_AUTOVSCROLL).
3. Triggers the Deep-Dive sequence when:
   - The global hotkey (Alt+D) is pressed at any time, OR
   - The green "🚀 Deep-Dive (Alt+D)" button in the GUI is clicked.
4. Step 1 (Bookmap):
   - Waits for any active Trade-Ideas External Linking macro (to our box and Thinkorswim)
     to finish settling, and waits for physical Alt / D / mouse button release.
   - Focuses the main Bookmap chart window WITHOUT tapping VK_MENU (Alt) so Bookmap's
     Java Swing menu bar (SC_KEYMENU) is never accidentally activated.
   - Verifies Bookmap holds uninterrupted foreground focus across bookmap_focus_delay_ms
     (re-focusing if Trade-Ideas/Thinkorswim steals focus).
   - Sends Ctrl+T using Win32 hardware scan codes and explicit key-down hold duration,
     verifies Bookmap retains foreground focus after Ctrl+T (retrying if stolen),
     copies the ticker immediately before Ctrl+A / Ctrl+V, and presses Enter.
5. Step 2 (DilutionTracker):
   - Reliably focuses the main browser window -> Ctrl+<1-8> (default Ctrl+6) ->
     Ctrl+L -> pastes https://dilutiontracker.com/app/search/{staged_ticker} -> Enter.
6. Persists all settings (tab index, delays, fallback coordinates) to config.json.
"""

import os
import re
import sys
import json
import time
import ctypes
from ctypes import wintypes
import traceback
import threading
import warnings
import tkinter as tk

import psutil
import pyperclip
import pyautogui
from pynput import keyboard

warnings.filterwarnings("ignore", category=FutureWarning)
from pywinauto import Application

APP_VERSION = "1.8"

# Disable PyAutoGUI's default 100ms post-action pause so we control exact millisecond delays
pyautogui.PAUSE = 0.01
pyautogui.FAILSAFE = False

# ==============================================================================
# 1. PATHS & DEFAULT CONFIGURATION
# ==============================================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
ERROR_LOG_PATH = os.path.join(SCRIPT_DIR, "error.log")


def log_error(context: str, exc: BaseException | None = None) -> None:
    try:
        with open(ERROR_LOG_PATH, "a", encoding="utf-8") as f:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{ts}] {context}\n")
            if exc is not None:
                f.write(
                    "".join(
                        traceback.format_exception(
                            type(exc), exc, exc.__traceback__
                        )
                    )
                )
            f.write("\n")
    except Exception:
        pass


DEFAULT_CONFIG = {
    "pinned_tab_index": 6,
    "hotkey": "<alt>+d",
    "ti_settle_guard_ms": 400,
    "bookmap_window_title": "Bookmap",
    "bookmap_focus_delay_ms": 220,
    "bookmap_dialog_delay_ms": 280,
    "bookmap_use_symbol_click": False,
    "bookmap_symbol_rel_x": 0,
    "bookmap_symbol_rel_y": 0,
    "bookmap_use_subscribe_click": False,
    "bookmap_subscribe_rel_x": 0,
    "bookmap_subscribe_rel_y": 0,
    "step_transition_delay_ms": 250,
    "browser_executables": [
        "firefox.exe",
        "chrome.exe",
        "msedge.exe",
        "brave.exe",
        "vivaldi.exe",
        "opera.exe",
    ],
    "browser_title_hint": "",
    "browser_focus_delay_ms": 120,
    "browser_tab_delay_ms": 120,
    "browser_url_delay_ms": 100,
    "dilution_tracker_url_template": "https://dilutiontracker.com/app/search/{ticker}",
    "always_on_top": True,
    "window_x": None,
    "window_y": None,
}

BROWSER_FRAME_CLASSES = {
    "mozillawindowclass",
    "chrome_widgetwin_1",
    "applicationframewindow",
}

THEME = {
    "bg_main": "#0D1117",
    "bg_card": "#161B22",
    "bg_input": "#1E2633",
    "bg_button": "#21262D",
    "bg_button_hover": "#30363D",
    "bg_accent": "#238636",
    "bg_accent_hover": "#2EA043",
    "fg_primary": "#F0F6FC",
    "fg_secondary": "#8B949E",
    "fg_ticker": "#58A6FF",
    "fg_success": "#3FB950",
    "fg_warning": "#D29922",
    "fg_error": "#F85149",
    "border": "#30363D",
    "border_focus": "#58A6FF",
}

# ==============================================================================
# 2. WIN32 API DEFINITIONS (64-BIT SAFE, NO PYTHON WNDPROC HOOKS)
# ==============================================================================
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
dwmapi = ctypes.windll.dwmapi

WNDENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HWND,
    wintypes.LPARAM,
)

# Window Styles & Messages
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_TABSTOP = 0x00010000
ES_LEFT = 0x0000
ES_MULTILINE = 0x0004
ES_UPPERCASE = 0x0008
ES_AUTOVSCROLL = 0x0040  # Required with ES_MULTILINE | ES_WANTRETURN so \r\n never triggers MessageBeep
ES_AUTOHSCROLL = 0x0080
ES_WANTRETURN = 0x1000

WM_SETTEXT = 0x000C
WM_CLOSE = 0x0010
WM_SETFONT = 0x0030
EM_SETSEL = 0x00B1

VK_LBUTTON = 0x01
VK_RETURN = 0x0D
VK_CONTROL = 0x11
VK_MENU = 0x12   # Alt key
VK_LMENU = 0xA4  # Left Alt
VK_RMENU = 0xA5  # Right Alt
VK_D = 0x44

KEYEVENTF_KEYUP = 0x0002
SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001

SW_RESTORE = 9
SW_SHOW = 5
GA_ROOT = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DWMWA_USE_IMMERSIVE_DARK_MODE = 20

# Explicit 64-bit ctypes signatures
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HWND,
    wintypes.HMENU,
    wintypes.HINSTANCE,
    wintypes.LPVOID,
]

user32.SendMessageW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.SendMessageW.restype = ctypes.c_ssize_t

user32.PostMessageW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.PostMessageW.restype = wintypes.BOOL

user32.MoveWindow.argtypes = [
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.BOOL,
]
user32.MoveWindow.restype = wintypes.BOOL

user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int

user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int

user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int

user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND

user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL

user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL

user32.SetFocus.argtypes = [wintypes.HWND]
user32.SetFocus.restype = wintypes.HWND

user32.GetFocus.argtypes = []
user32.GetFocus.restype = wintypes.HWND

user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL

user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL

user32.AttachThreadInput.argtypes = [
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.BOOL,
]
user32.AttachThreadInput.restype = wintypes.BOOL

user32.SystemParametersInfoW.argtypes = [
    wintypes.UINT,
    wintypes.UINT,
    wintypes.LPVOID,
    wintypes.UINT,
]
user32.SystemParametersInfoW.restype = wintypes.BOOL

user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT

user32.keybd_event.argtypes = [
    wintypes.BYTE,
    wintypes.BYTE,
    wintypes.DWORD,
    ctypes.c_ulonglong,
]
user32.keybd_event.restype = None

user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND

user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD),
]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL

user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL

user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL

user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL

kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

gdi32.CreateFontW.argtypes = [
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPCWSTR,
]
gdi32.CreateFontW.restype = wintypes.HFONT

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE

kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


def clean_ticker(raw_text: str) -> str:
    """Clean raw text from Trade-Ideas External Linking or manual typing into a valid ticker symbol."""
    if not raw_text:
        return ""
    text = raw_text.strip().upper()
    tokens = [t.strip() for t in re.split(r"[\s\r\n]+", text) if t.strip()]
    if not tokens:
        return ""
    token = tokens[-1].lstrip("$")
    if ":" in token:
        token = token.split(":")[-1]
    cleaned = re.sub(r"[^A-Z0-9.\-]", "", token)
    if len(cleaned) >= 6 and len(cleaned) % 2 == 0:
        half = len(cleaned) // 2
        if cleaned[:half] == cleaned[half:]:
            cleaned = cleaned[:half]
    return cleaned[:12]


def get_hwnd_process_info(hwnd: int) -> tuple[str, str, int]:
    """
    Fast Win32 lookup returning (exe_basename_lower, full_exe_path_lower, pid) for an HWND.
    """
    if not hwnd:
        return "", "", 0
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return "", "", 0

    hproc = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
    )
    if hproc:
        try:
            buf_len = wintypes.DWORD(512)
            buf = ctypes.create_unicode_buffer(512)
            if kernel32.QueryFullProcessImageNameW(
                hproc, 0, buf, ctypes.byref(buf_len)
            ):
                full_path = buf.value.lower()
                return os.path.basename(full_path), full_path, int(pid.value)
        finally:
            kernel32.CloseHandle(hproc)

    try:
        proc = psutil.Process(pid.value)
        name = (proc.name() or "").lower()
        exe = (proc.exe() or "").lower()
        return name, exe, int(pid.value)
    except Exception:
        return "", "", int(pid.value)


def get_hwnd_process_name(hwnd: int) -> str:
    name, _, _ = get_hwnd_process_info(hwnd)
    return name


def get_window_text(hwnd: int) -> str:
    """Read title/text of a Win32 HWND."""
    if not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 2)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def get_window_class(hwnd: int) -> str:
    """Read Win32 window class name of an HWND."""
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def set_window_text(hwnd: int, text: str) -> None:
    """Send WM_SETTEXT to a Win32 HWND."""
    if not hwnd:
        return
    buf = ctypes.create_unicode_buffer(text)
    user32.SendMessageW(
        hwnd, WM_SETTEXT, 0, ctypes.cast(buf, ctypes.c_void_p).value
    )


def apply_dark_titlebar(hwnd: int) -> None:
    """Enable Windows 10/11 immersive dark mode titlebar."""
    try:
        val = ctypes.c_int(1)
        dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(val),
            ctypes.sizeof(val),
        )
    except Exception:
        pass


def close_previous_instances() -> None:
    """
    Finds any previously running 'TI Ticker Deep-Dive' window from an older instance
    and sends WM_CLOSE so two instances never compete for Alt+D or Trade-Ideas links.
    """
    own_pid = os.getpid()

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == own_pid:
            return True
        title = get_window_text(hwnd)
        if title.startswith("TI Ticker Deep-Dive"):
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        return True

    try:
        user32.EnumWindows(WNDENUMPROC(cb), 0)
    except Exception:
        pass


def _vk_key_event(vk: int, key_up: bool = False) -> None:
    """Send a virtual key event with its hardware scan code via Win32 keybd_event."""
    scan = user32.MapVirtualKeyW(vk, 0) & 0xFF
    flags = KEYEVENTF_KEYUP if key_up else 0
    user32.keybd_event(vk & 0xFF, scan, flags, 0)


def send_ctrl_key_reliable(key_char: str, hold_sec: float = 0.04) -> None:
    """
    Sends Ctrl+<key_char> using Win32 virtual keys + hardware scan codes and explicit
    hold durations on BOTH Ctrl and the target key so Java Swing / OpenGL windows
    like Bookmap never drop synthetic key chords.
    """
    ch = key_char.upper()
    vk = ord(ch[0])
    _vk_key_event(VK_CONTROL, key_up=False)
    time.sleep(hold_sec)
    _vk_key_event(vk, key_up=False)
    time.sleep(hold_sec)
    _vk_key_event(vk, key_up=True)
    time.sleep(max(0.02, hold_sec * 0.6))
    _vk_key_event(VK_CONTROL, key_up=True)


def send_enter_reliable(hold_sec: float = 0.04) -> None:
    """Sends Enter (VK_RETURN) with hardware scan code and explicit hold duration."""
    _vk_key_event(VK_RETURN, key_up=False)
    time.sleep(hold_sec)
    _vk_key_event(VK_RETURN, key_up=True)


def wait_for_input_settled(
    release_alt: bool = False, max_wait_sec: float = 0.60
) -> None:
    """
    Waits until physical Alt, D, and Left Mouse Button are released so neither
    Alt+D nor clicking the GUI button interferes with Bookmap focus or Ctrl+T.
    """
    t0 = time.perf_counter()
    while (time.perf_counter() - t0) < max_wait_sec:
        alt_down = (
            (user32.GetAsyncKeyState(VK_MENU) & 0x8000) != 0
            or (user32.GetAsyncKeyState(VK_LMENU) & 0x8000) != 0
            or (user32.GetAsyncKeyState(VK_RMENU) & 0x8000) != 0
            or (user32.GetAsyncKeyState(VK_D) & 0x8000) != 0
        )
        mouse_down = (user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000) != 0
        if not alt_down and not mouse_down:
            break
        time.sleep(0.015)

    if release_alt:
        try:
            _vk_key_event(VK_LMENU, key_up=True)
            _vk_key_event(VK_RMENU, key_up=True)
            _vk_key_event(VK_MENU, key_up=True)
        except Exception:
            pass
    time.sleep(0.03)


# ==============================================================================
# 3. MAIN APPLICATION CLASS
# ==============================================================================
class TickerAutomationApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.config = self.load_config()
        self._own_pid = os.getpid()
        self._closing = False

        self.staged_ticker: str = ""
        self._settled_ticker: str = ""
        self._last_raw_entry_text: str = ""
        self._last_entry_change_ts: float = 0.0
        self._last_ti_burst_ts: float = 0.0
        self._entry_burst_armed_for_overwrite: bool = True

        # Manual typing & click focus state
        self._top_hwnd: int = 0
        self._manual_edit_active: bool = False
        self._was_our_window_fg: bool = False
        self._prev_mouse_down: bool = False

        self._automation_lock = threading.Lock()
        self._is_running_sequence = False

        # Remembered browser HWND for consistent targeting
        self._last_browser_hwnd: int = 0

        # Win32 Edit HWND & font handle
        self.edit_hwnd: int = 0
        self._edit_font: int = 0

        # Global Hotkey Listener
        self._hotkey_listener = None
        self._settings_expanded = False

        # Unlock Windows foreground activation lock timeout for our process
        try:
            user32.SystemParametersInfoW(
                SPI_SETFOREGROUNDLOCKTIMEOUT, 0, ctypes.c_void_p(0), 0
            )
        except Exception:
            pass

        self._setup_window()
        self._build_gui()
        self._create_win32_entry()
        self._start_listeners()
        self._schedule_entry_poll()

    # --------------------------------------------------------------------------
    # Configuration Persistence
    # --------------------------------------------------------------------------
    def load_config(self) -> dict:
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    cfg.update(loaded)
            except Exception as e:
                log_error("Failed to read config.json", e)
        if int(cfg.get("bookmap_focus_delay_ms", 220)) <= 100:
            cfg["bookmap_focus_delay_ms"] = 220
        if int(cfg.get("bookmap_dialog_delay_ms", 280)) <= 200:
            cfg["bookmap_dialog_delay_ms"] = 280
        self.save_config(cfg)
        return cfg

    def save_config(self, cfg: dict | None = None) -> None:
        target = cfg if cfg is not None else self.config
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(target, f, indent=4)
        except Exception as e:
            log_error("Failed to save config.json", e)

    # --------------------------------------------------------------------------
    # GUI Setup & Construction
    # --------------------------------------------------------------------------
    def _setup_window(self) -> None:
        self.root.title(f"TI Ticker Deep-Dive v{APP_VERSION}")
        self.root.configure(bg=THEME["bg_main"])
        self.root.resizable(False, False)

        always_top = bool(self.config.get("always_on_top", True))
        self.root.attributes("-topmost", always_top)

        win_x = self.config.get("window_x")
        win_y = self.config.get("window_y")
        if isinstance(win_x, int) and isinstance(win_y, int):
            self.root.geometry(f"+{win_x}+{win_y}")

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_gui(self) -> None:
        self.main_frame = tk.Frame(
            self.root, bg=THEME["bg_main"], padx=10, pady=8
        )
        self.main_frame.pack(fill="both", expand=True)

        # ---- ROW 1: Win32 Entry Target Box + Staged Status Label ----
        top_row = tk.Frame(self.main_frame, bg=THEME["bg_main"])
        top_row.pack(fill="x", pady=(0, 6))

        target_lbl = tk.Label(
            top_row,
            text="TI LINK:",
            font=("Segoe UI", 8, "bold"),
            fg=THEME["fg_secondary"],
            bg=THEME["bg_main"],
            cursor="xterm",
        )
        target_lbl.pack(side="left", padx=(0, 5))
        target_lbl.bind("<Button-1>", lambda e: self.focus_manual_entry())

        self.entry_border = tk.Frame(
            top_row,
            bg=THEME["border_focus"],
            padx=1,
            pady=1,
        )
        self.entry_border.pack(side="left", padx=(0, 10))

        self.entry_container = tk.Frame(
            self.entry_border,
            width=92,
            height=26,
            bg=THEME["bg_input"],
        )
        self.entry_container.pack_propagate(False)
        self.entry_container.pack()

        self.staged_var = tk.StringVar(value="Staged: —")
        self.staged_label = tk.Label(
            top_row,
            textvariable=self.staged_var,
            font=("Consolas", 12, "bold"),
            fg=THEME["fg_ticker"],
            bg=THEME["bg_card"],
            padx=10,
            pady=2,
            anchor="w",
            width=15,
            cursor="xterm",
        )
        self.staged_label.pack(side="left", fill="x", expand=True)
        self.staged_label.bind("<Button-1>", lambda e: self.focus_manual_entry())

        # ---- ROW 2: Pinned Tab Selector + Deep-Dive Button + Settings Toggle ----
        ctrl_row = tk.Frame(self.main_frame, bg=THEME["bg_main"])
        ctrl_row.pack(fill="x", pady=(0, 4))

        tab_lbl = tk.Label(
            ctrl_row,
            text="DT Tab (Ctrl+):",
            font=("Segoe UI", 9),
            fg=THEME["fg_secondary"],
            bg=THEME["bg_main"],
        )
        tab_lbl.pack(side="left", padx=(0, 4))

        self.tab_index_var = tk.IntVar(
            value=max(1, min(8, int(self.config.get("pinned_tab_index", 6))))
        )
        self.tab_spinbox = tk.Spinbox(
            ctrl_row,
            from_=1,
            to=8,
            width=3,
            textvariable=self.tab_index_var,
            font=("Consolas", 10, "bold"),
            bg=THEME["bg_input"],
            fg=THEME["fg_primary"],
            buttonbackground=THEME["bg_button"],
            insertbackground=THEME["fg_primary"],
            relief="flat",
            justify="center",
            command=self._on_quick_tab_change,
        )
        self.tab_spinbox.bind("<Return>", lambda e: self._on_quick_tab_change())
        self.tab_spinbox.bind("<FocusOut>", lambda e: self._on_quick_tab_change())
        self.tab_spinbox.pack(side="left", padx=(0, 8))

        self.launch_btn = tk.Button(
            ctrl_row,
            text="🚀 Deep-Dive (Alt+D)",
            font=("Segoe UI", 9, "bold"),
            bg=THEME["bg_accent"],
            fg=THEME["fg_primary"],
            activebackground=THEME["bg_accent_hover"],
            activeforeground=THEME["fg_primary"],
            relief="flat",
            padx=8,
            pady=2,
            cursor="hand2",
            command=lambda: self.trigger_deep_dive(source="GUI Button"),
        )
        self.launch_btn.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.settings_btn = tk.Button(
            ctrl_row,
            text="⚙",
            font=("Segoe UI", 10, "bold"),
            bg=THEME["bg_button"],
            fg=THEME["fg_primary"],
            activebackground=THEME["bg_button_hover"],
            activeforeground=THEME["fg_primary"],
            relief="flat",
            width=3,
            pady=1,
            cursor="hand2",
            command=self._toggle_settings_panel,
        )
        self.settings_btn.pack(side="right")

        # ---- ROW 3: Live Status Bar ----
        self.status_var = tk.StringVar(
            value=f"v{APP_VERSION} Ready • Click TI or type ticker | Alt+D or Deep-Dive launches"
        )
        self.status_bar = tk.Label(
            self.main_frame,
            textvariable=self.status_var,
            font=("Segoe UI", 8),
            fg=THEME["fg_secondary"],
            bg=THEME["bg_main"],
            anchor="w",
        )
        self.status_bar.pack(fill="x")

        # ---- COLLAPSIBLE SETTINGS PANEL ----
        self.settings_frame = tk.Frame(
            self.main_frame,
            bg=THEME["bg_card"],
            padx=8,
            pady=8,
            highlightbackground=THEME["border"],
            highlightthickness=1,
        )
        self._build_settings_panel()

        # Allow typing a ticker directly if the main window has focus outside settings inputs
        self.root.bind("<KeyPress>", self._on_root_keypress)

    def _build_settings_panel(self) -> None:
        """Build the collapsible configuration drawer for delays & fallback coordinates."""
        self.var_always_top = tk.BooleanVar(
            value=bool(self.config.get("always_on_top", True))
        )
        self.var_bm_focus_ms = tk.StringVar(
            value=str(self.config.get("bookmap_focus_delay_ms", 220))
        )
        self.var_bm_dialog_ms = tk.StringVar(
            value=str(self.config.get("bookmap_dialog_delay_ms", 280))
        )
        self.var_bm_title = tk.StringVar(
            value=str(self.config.get("bookmap_window_title", "Bookmap"))
        )

        self.var_use_sym_click = tk.BooleanVar(
            value=bool(self.config.get("bookmap_use_symbol_click", False))
        )
        self.var_sym_x = tk.StringVar(
            value=str(self.config.get("bookmap_symbol_rel_x", 0))
        )
        self.var_sym_y = tk.StringVar(
            value=str(self.config.get("bookmap_symbol_rel_y", 0))
        )

        self.var_use_sub_click = tk.BooleanVar(
            value=bool(self.config.get("bookmap_use_subscribe_click", False))
        )
        self.var_sub_x = tk.StringVar(
            value=str(self.config.get("bookmap_subscribe_rel_x", 0))
        )
        self.var_sub_y = tk.StringVar(
            value=str(self.config.get("bookmap_subscribe_rel_y", 0))
        )

        self.var_browser_tab_ms = tk.StringVar(
            value=str(self.config.get("browser_tab_delay_ms", 120))
        )
        self.var_browser_url_ms = tk.StringVar(
            value=str(self.config.get("browser_url_delay_ms", 100))
        )

        def make_label(parent, text, row, col):
            lbl = tk.Label(
                parent,
                text=text,
                font=("Segoe UI", 8),
                fg=THEME["fg_secondary"],
                bg=THEME["bg_card"],
                anchor="w",
            )
            lbl.grid(row=row, column=col, sticky="w", padx=(0, 6), pady=2)
            return lbl

        def make_entry(parent, var, width, row, col):
            ent = tk.Entry(
                parent,
                textvariable=var,
                width=width,
                font=("Consolas", 9),
                bg=THEME["bg_input"],
                fg=THEME["fg_primary"],
                insertbackground=THEME["fg_primary"],
                relief="flat",
            )
            ent.grid(row=row, column=col, sticky="w", padx=(0, 6), pady=2)
            return ent

        hdr1 = tk.Label(
            self.settings_frame,
            text="BOOKMAP & BROWSER TIMING",
            font=("Segoe UI", 8, "bold"),
            fg=THEME["fg_ticker"],
            bg=THEME["bg_card"],
        )
        hdr1.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))

        make_label(self.settings_frame, "BM Focus Wait (ms):", 1, 0)
        make_entry(self.settings_frame, self.var_bm_focus_ms, 6, 1, 1)
        make_label(self.settings_frame, "BM Ctrl+T Wait (ms):", 1, 2)
        make_entry(self.settings_frame, self.var_bm_dialog_ms, 6, 1, 3)

        make_label(self.settings_frame, "Bookmap Title:", 2, 0)
        make_entry(self.settings_frame, self.var_bm_title, 12, 2, 1)
        make_label(self.settings_frame, "Tab/URL Wait (ms):", 2, 2)
        tab_url_frame = tk.Frame(self.settings_frame, bg=THEME["bg_card"])
        tab_url_frame.grid(row=2, column=3, sticky="w")
        make_entry(tab_url_frame, self.var_browser_tab_ms, 4, 0, 0)
        make_entry(tab_url_frame, self.var_browser_url_ms, 4, 0, 1)

        hdr2 = tk.Label(
            self.settings_frame,
            text="BOOKMAP FALLBACK RELATIVE COORDS (X, Y)",
            font=("Segoe UI", 8, "bold"),
            fg=THEME["fg_ticker"],
            bg=THEME["bg_card"],
        )
        hdr2.grid(row=3, column=0, columnspan=4, sticky="w", pady=(6, 4))

        sym_cb = tk.Checkbutton(
            self.settings_frame,
            text="Click Symbol Box:",
            variable=self.var_use_sym_click,
            font=("Segoe UI", 8),
            fg=THEME["fg_primary"],
            bg=THEME["bg_card"],
            selectcolor=THEME["bg_input"],
            activebackground=THEME["bg_card"],
            activeforeground=THEME["fg_primary"],
        )
        sym_cb.grid(row=4, column=0, sticky="w")

        sym_coord_frame = tk.Frame(self.settings_frame, bg=THEME["bg_card"])
        sym_coord_frame.grid(row=4, column=1, columnspan=3, sticky="w")
        make_entry(sym_coord_frame, self.var_sym_x, 5, 0, 0)
        make_entry(sym_coord_frame, self.var_sym_y, 5, 0, 1)
        pick_sym_btn = tk.Button(
            sym_coord_frame,
            text="🎯 Pick (3s)",
            font=("Segoe UI", 8),
            bg=THEME["bg_button"],
            fg=THEME["fg_primary"],
            relief="flat",
            padx=5,
            command=lambda: self._pick_bookmap_relative_coords(
                self.var_sym_x, self.var_sym_y, self.var_use_sym_click, "Symbol Box"
            ),
        )
        pick_sym_btn.grid(row=0, column=2, padx=(4, 0))

        sub_cb = tk.Checkbutton(
            self.settings_frame,
            text="Click Subscribe Btn:",
            variable=self.var_use_sub_click,
            font=("Segoe UI", 8),
            fg=THEME["fg_primary"],
            bg=THEME["bg_card"],
            selectcolor=THEME["bg_input"],
            activebackground=THEME["bg_card"],
            activeforeground=THEME["fg_primary"],
        )
        sub_cb.grid(row=5, column=0, sticky="w")

        sub_coord_frame = tk.Frame(self.settings_frame, bg=THEME["bg_card"])
        sub_coord_frame.grid(row=5, column=1, columnspan=3, sticky="w")
        make_entry(sub_coord_frame, self.var_sub_x, 5, 0, 0)
        make_entry(sub_coord_frame, self.var_sub_y, 5, 0, 1)
        pick_sub_btn = tk.Button(
            sub_coord_frame,
            text="🎯 Pick (3s)",
            font=("Segoe UI", 8),
            bg=THEME["bg_button"],
            fg=THEME["fg_primary"],
            relief="flat",
            padx=5,
            command=lambda: self._pick_bookmap_relative_coords(
                self.var_sub_x, self.var_sub_y, self.var_use_sub_click, "Subscribe Button"
            ),
        )
        pick_sub_btn.grid(row=0, column=2, padx=(4, 0))

        bot_frame = tk.Frame(self.settings_frame, bg=THEME["bg_card"])
        bot_frame.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(6, 0))

        top_cb = tk.Checkbutton(
            bot_frame,
            text="Always on Top",
            variable=self.var_always_top,
            font=("Segoe UI", 8),
            fg=THEME["fg_primary"],
            bg=THEME["bg_card"],
            selectcolor=THEME["bg_input"],
            activebackground=THEME["bg_card"],
            activeforeground=THEME["fg_primary"],
        )
        top_cb.pack(side="left")

        save_btn = tk.Button(
            bot_frame,
            text="💾 Save to config.json",
            font=("Segoe UI", 8, "bold"),
            bg=THEME["bg_accent"],
            fg=THEME["fg_primary"],
            activebackground=THEME["bg_accent_hover"],
            activeforeground=THEME["fg_primary"],
            relief="flat",
            padx=10,
            pady=2,
            cursor="hand2",
            command=self._save_settings_from_ui,
        )
        save_btn.pack(side="right")

    def _toggle_settings_panel(self) -> None:
        if self._settings_expanded:
            self.settings_frame.pack_forget()
            self._settings_expanded = False
        else:
            self.settings_frame.pack(fill="x", pady=(6, 0))
            self._settings_expanded = True

    def _on_quick_tab_change(self) -> None:
        try:
            idx = int(self.tab_index_var.get())
            idx = max(1, min(8, idx))
            self.tab_index_var.set(idx)
            self.config["pinned_tab_index"] = idx
            self.save_config()
            self.set_status(
                f"Saved DilutionTracker tab: Ctrl+{idx}", THEME["fg_success"]
            )
        except Exception:
            pass

    def _save_settings_from_ui(self) -> None:
        try:
            self.config["pinned_tab_index"] = max(
                1, min(8, int(self.tab_index_var.get()))
            )
            self.config["always_on_top"] = bool(self.var_always_top.get())
            self.config["bookmap_focus_delay_ms"] = max(
                50, int(self.var_bm_focus_ms.get())
            )
            self.config["bookmap_dialog_delay_ms"] = max(
                50, int(self.var_bm_dialog_ms.get())
            )
            self.config["bookmap_window_title"] = (
                self.var_bm_title.get().strip() or "Bookmap"
            )

            self.config["bookmap_use_symbol_click"] = bool(
                self.var_use_sym_click.get()
            )
            self.config["bookmap_symbol_rel_x"] = int(self.var_sym_x.get() or 0)
            self.config["bookmap_symbol_rel_y"] = int(self.var_sym_y.get() or 0)

            self.config["bookmap_use_subscribe_click"] = bool(
                self.var_use_sub_click.get()
            )
            self.config["bookmap_subscribe_rel_x"] = int(
                self.var_sub_x.get() or 0
            )
            self.config["bookmap_subscribe_rel_y"] = int(
                self.var_sub_y.get() or 0
            )

            self.config["browser_tab_delay_ms"] = max(
                20, int(self.var_browser_tab_ms.get())
            )
            self.config["browser_url_delay_ms"] = max(
                20, int(self.var_browser_url_ms.get())
            )

            self.root.attributes("-topmost", self.config["always_on_top"])
            self.save_config()
            self.set_status(
                "All settings saved to config.json", THEME["fg_success"]
            )
        except ValueError as e:
            self.set_status(f"Invalid numeric setting: {e}", THEME["fg_error"])

    def _pick_bookmap_relative_coords(
        self,
        var_x: tk.StringVar,
        var_y: tk.StringVar,
        var_enable: tk.BooleanVar,
        label_name: str,
    ) -> None:
        """3-second countdown helper that records cursor coordinates relative to the Bookmap window."""

        def worker():
            for sec in (3, 2, 1):
                self.set_status(
                    f"Hover over Bookmap {label_name} in {sec}s...",
                    THEME["fg_warning"],
                )
                time.sleep(1.0)
            bm_hwnd = self._find_bookmap_hwnd()
            if not bm_hwnd:
                self.set_status(
                    "Bookmap window not found for relative coord pick!",
                    THEME["fg_error"],
                )
                return
            rect = wintypes.RECT()
            user32.GetWindowRect(bm_hwnd, ctypes.byref(rect))
            cur_x, cur_y = pyautogui.position()
            rel_x = int(cur_x - rect.left)
            rel_y = int(cur_y - rect.top)

            def apply_ui():
                var_x.set(str(rel_x))
                var_y.set(str(rel_y))
                var_enable.set(True)
                self._save_settings_from_ui()
                self.set_status(
                    f"Captured {label_name} relative coords: ({rel_x}, {rel_y})",
                    THEME["fg_success"],
                )

            self.root.after(0, apply_ui)

        threading.Thread(target=worker, daemon=True).start()

    # --------------------------------------------------------------------------
    # Native Win32 "Edit" Entry Control (Targetable by TI & Manually Editable)
    # --------------------------------------------------------------------------
    def _create_win32_entry(self) -> None:
        self.root.update_idletasks()
        self.root.update()

        self._top_hwnd = (
            user32.GetAncestor(self.root.winfo_id(), GA_ROOT)
            or self.root.winfo_id()
        )
        apply_dark_titlebar(self._top_hwnd)

        parent_hwnd = self.entry_container.winfo_id()
        width = max(80, self.entry_container.winfo_width())
        height = max(24, self.entry_container.winfo_height())

        # ES_MULTILINE | ES_WANTRETURN | ES_AUTOVSCROLL ensures that when Trade-Ideas
        # sends VK_RETURN ('\r'), user32.dll accepts the newline into the buffer
        # instead of rejecting it and calling MessageBeep(0)!
        style = (
            WS_CHILD
            | WS_VISIBLE
            | WS_TABSTOP
            | ES_LEFT
            | ES_MULTILINE
            | ES_WANTRETURN
            | ES_AUTOVSCROLL
            | ES_AUTOHSCROLL
            | ES_UPPERCASE
        )
        self.edit_hwnd = user32.CreateWindowExW(
            0,
            "Edit",
            "",
            style,
            2,
            2,
            width - 4,
            height - 4,
            parent_hwnd,
            None,
            None,
            None,
        )

        self._edit_font = gdi32.CreateFontW(
            -15, 0, 0, 0, 700, 0, 0, 0, 0, 0, 0, 4, 0, "Consolas"
        )
        if self._edit_font:
            user32.SendMessageW(self.edit_hwnd, WM_SETFONT, self._edit_font, 1)

        def on_container_resize(event):
            if self.edit_hwnd:
                w = max(40, event.width - 4)
                h = max(18, event.height - 4)
                user32.MoveWindow(self.edit_hwnd, 2, 2, w, h, True)

        self.entry_container.bind("<Configure>", on_container_resize)

    def focus_manual_entry(self, select_all: bool = True) -> None:
        """
        Gives Win32 keyboard focus directly to self.edit_hwnd (overriding Tkinter's
        WM_ACTIVATE focus grab on self.root) so the user can manually type a ticker.
        """
        if not self.edit_hwnd:
            return
        self._manual_edit_active = True
        self._settled_ticker = ""

        if self._top_hwnd and user32.GetForegroundWindow() != self._top_hwnd:
            user32.SetForegroundWindow(self._top_hwnd)
        user32.SetFocus(self.edit_hwnd)
        if select_all:
            user32.SendMessageW(self.edit_hwnd, EM_SETSEL, 0, -1)

        def _ensure_focus_kept():
            if self.edit_hwnd and self._manual_edit_active:
                if user32.GetFocus() != self.edit_hwnd:
                    user32.SetFocus(self.edit_hwnd)

        self.root.after(20, _ensure_focus_kept)
        self.root.after(65, _ensure_focus_kept)

    def _on_root_keypress(self, event) -> None:
        """
        If the user clicks on the TI Ticker Deep-Dive window and starts typing
        a ticker while Tkinter holds focus on self.root (instead of a Spinbox/Entry),
        automatically route the keystroke into self.edit_hwnd!
        """
        focused_widget = self.root.focus_get()
        if isinstance(focused_widget, (tk.Entry, tk.Spinbox)):
            return
        if event.state & 0x0004 or event.state & 0x20000:
            # Ignore Ctrl or Alt shortcuts (e.g. Alt+D)
            return

        ch = event.char or ""
        if ch and re.match(r"^[A-Za-z0-9.\-]$", ch):
            up_ch = ch.upper()
            if not self._manual_edit_active:
                self._manual_edit_active = True
                new_text = up_ch
            else:
                cur = clean_ticker(self.get_entry_text())
                new_text = (cur + up_ch)[:12]
            set_window_text(self.edit_hwnd, new_text)
            self.focus_manual_entry(select_all=False)
            n = len(new_text)
            user32.SendMessageW(self.edit_hwnd, EM_SETSEL, n, n)
            self._sync_entry_to_staged_ticker()
        elif event.keysym == "BackSpace":
            self._manual_edit_active = True
            cur = clean_ticker(self.get_entry_text())
            new_text = cur[:-1]
            set_window_text(self.edit_hwnd, new_text)
            self.focus_manual_entry(select_all=False)
            n = len(new_text)
            user32.SendMessageW(self.edit_hwnd, EM_SETSEL, n, n)
            self._sync_entry_to_staged_ticker()

    def _check_manual_edit_click(self) -> None:
        """
        Detects when the user clicks inside self.edit_hwnd and ensures Tkinter's
        top-level WM_ACTIVATE handler doesn't steal keyboard focus away from it.
        Also re-arms EM_SETSEL(0, -1) when manual editing ends so Trade-Ideas
        External Linking always overwrites cleanly.
        """
        if not self.edit_hwnd:
            return

        fg_hwnd = user32.GetForegroundWindow()
        is_our_fg = bool(self._top_hwnd and fg_hwnd == self._top_hwnd)
        if self._was_our_window_fg and not is_our_fg:
            # User just switched away from our window -> re-arm select-all for Trade-Ideas
            self._manual_edit_active = False
            user32.SendMessageW(self.edit_hwnd, EM_SETSEL, 0, -1)
        self._was_our_window_fg = is_our_fg

        mouse_down = (user32.GetAsyncKeyState(VK_LBUTTON) & 0x8000) != 0
        just_clicked = mouse_down and not self._prev_mouse_down
        self._prev_mouse_down = mouse_down

        if not just_clicked:
            return

        pt = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(pt)):
            return

        edit_rect = wintypes.RECT()
        user32.GetWindowRect(self.edit_hwnd, ctypes.byref(edit_rect))
        in_edit = (
            edit_rect.left - 4 <= pt.x <= edit_rect.right + 4
            and edit_rect.top - 4 <= pt.y <= edit_rect.bottom + 4
        )

        if in_edit:
            was_already_editing = (
                self._manual_edit_active and user32.GetFocus() == self.edit_hwnd
            )
            self.focus_manual_entry(select_all=not was_already_editing)
        elif self._manual_edit_active:
            # Clicked outside the edit box -> exit manual edit mode and select all
            self._manual_edit_active = False
            user32.SendMessageW(self.edit_hwnd, EM_SETSEL, 0, -1)

    def get_entry_text(self) -> str:
        """Read current text directly from the Win32 Edit HWND (thread-safe)."""
        if not self.edit_hwnd:
            return ""
        return get_window_text(self.edit_hwnd)

    def set_entry_text(self, text: str) -> None:
        """Programmatically set text on the Win32 Edit HWND."""
        if self.edit_hwnd:
            set_window_text(self.edit_hwnd, text)
            self._sync_entry_to_staged_ticker()

    def _sync_entry_to_staged_ticker(self) -> str:
        """
        Reads the Win32 Edit box, cleans the symbol, silently updates staged_ticker
        and the 'Staged: XXXX' label, and NEVER triggers automation on single-click.
        Supports both Trade-Ideas External Linking bursts and manual human typing.
        """
        now = time.perf_counter()
        raw = self.get_entry_text()
        has_newline = "\r" in raw or "\n" in raw

        if raw != self._last_raw_entry_text:
            self._last_raw_entry_text = raw
            self._last_entry_change_ts = now
            if not self._manual_edit_active or has_newline:
                self._last_ti_burst_ts = now
            self._entry_burst_armed_for_overwrite = False

            cleaned = clean_ticker(raw)
            if (
                has_newline
                and not self._manual_edit_active
                and self._settled_ticker
                and cleaned.startswith(self._settled_ticker)
                and len(cleaned) > len(self._settled_ticker)
            ):
                cleaned = cleaned[len(self._settled_ticker) :]

            if cleaned:
                self.staged_ticker = cleaned
                self.root.after(0, self._update_staged_ui, cleaned)
            elif not raw.strip():
                self.staged_ticker = ""
                self._settled_ticker = ""
                self.root.after(0, self._update_staged_ui, "")
        else:
            if (
                not self._manual_edit_active
                and raw
                and (now - self._last_entry_change_ts) >= 0.30
            ):
                self._settled_ticker = self.staged_ticker

        # When Trade-Ideas sends VK_RETURN ('\r\n') OR when the user presses Enter in
        # the box, immediately strip the newline and select all so the next ticker
        # overwrites cleanly. Never auto-select-all mid-typing when there is no newline!
        if has_newline:
            self._entry_burst_armed_for_overwrite = True
            self._manual_edit_active = False
            if self.edit_hwnd:
                cleaned = self.staged_ticker or clean_ticker(raw)
                if cleaned and raw != cleaned:
                    set_window_text(self.edit_hwnd, cleaned)
                    self._last_raw_entry_text = cleaned
                self._settled_ticker = cleaned
                user32.SendMessageW(self.edit_hwnd, EM_SETSEL, 0, -1)

        return self.staged_ticker

    def _update_staged_ui(self, ticker: str) -> None:
        if ticker:
            self.staged_var.set(f"Staged: {ticker}")
            if not self._is_running_sequence:
                self.set_status(
                    f"Staged {ticker} • Press Alt+D or click Deep-Dive",
                    THEME["fg_ticker"],
                )
        else:
            self.staged_var.set("Staged: —")

    def _schedule_entry_poll(self) -> None:
        """25ms poll of the Win32 Edit HWND on the main Tkinter thread."""
        try:
            self._check_manual_edit_click()
            self._sync_entry_to_staged_ticker()
        except Exception as e:
            log_error("Entry poll error", e)
        if not self._closing:
            self.root.after(25, self._schedule_entry_poll)

    def set_status(
        self, message: str, color: str = THEME["fg_secondary"]
    ) -> None:
        def _apply():
            self.status_var.set(message)
            self.status_bar.configure(fg=color)

        if threading.current_thread() is threading.main_thread():
            _apply()
        else:
            self.root.after(0, _apply)

    # --------------------------------------------------------------------------
    # Global Hotkey Listener (Alt+D)
    # --------------------------------------------------------------------------
    def _start_listeners(self) -> None:
        hotkey_str = self.config.get("hotkey", "<alt>+d") or "<alt>+d"
        try:
            self._hotkey_listener = keyboard.GlobalHotKeys(
                {hotkey_str: self._on_hotkey_pressed}
            )
            self._hotkey_listener.daemon = True
            self._hotkey_listener.start()
        except Exception as e:
            log_error(f"Failed to bind hotkey {hotkey_str}", e)

    def _on_hotkey_pressed(self) -> None:
        """Global hotkey (Alt+D) handler."""
        threading.Thread(
            target=self.trigger_deep_dive,
            kwargs={"source": "Hotkey (Alt+D)", "release_alt": True},
            daemon=True,
        ).start()

    # --------------------------------------------------------------------------
    # Window Discovery & Verified Focus Helpers (Bookmap & Browser)
    # --------------------------------------------------------------------------
    def _enum_visible_windows(
        self,
    ) -> list[tuple[int, str, str, str, int]]:
        """
        Returns list of (hwnd, title, exe_name_lower, class_name_lower, area)
        for all visible top-level windows.
        """
        results = []
        own_pid = self._own_pid

        def callback(hwnd, lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            title = get_window_text(hwnd).strip()
            if not title:
                return True
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == own_pid:
                return True
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w = max(0, rect.right - rect.left)
            h = max(0, rect.bottom - rect.top)
            if w < 150 or h < 100:
                return True
            exe_name = get_hwnd_process_name(hwnd).lower()
            cls_name = get_window_class(hwnd).lower()
            results.append((hwnd, title, exe_name, cls_name, w * h))
            return True

        enum_proc = WNDENUMPROC(callback)
        user32.EnumWindows(enum_proc, 0)
        return results

    def _find_bookmap_hwnd(self) -> int:
        """
        Finds the main Bookmap chart window. Excludes browser processes and
        auxiliary/launcher Bookmap windows.
        """
        target_title = (
            self.config.get("bookmap_window_title", "Bookmap").strip().lower()
        )
        browser_exes = {
            b.lower() for b in self.config.get("browser_executables", [])
        }
        candidates = []
        for (
            hwnd,
            title,
            exe_name,
            cls_name,
            area,
        ) in self._enum_visible_windows():
            if exe_name in browser_exes:
                continue
            t_low = title.lower()
            if target_title in t_low or "bookmap" in exe_name:
                # Prefer main SunAwtFrame chart window over launcher/control panel or small dialogs
                is_aux = any(
                    k in t_low
                    for k in ("control panel", "run options", "launcher", "subscribe")
                ) or ("dialog" in cls_name)
                priority = 0 if not is_aux else 1
                candidates.append((priority, -area, hwnd, title))

        if not candidates:
            return 0
        candidates.sort()
        return candidates[0][2]

    def _find_browser_hwnd(self) -> int:
        """
        Finds the user's main browser window, filtering strictly to real top-level
        browser frame classes (MozillaWindowClass / Chrome_WidgetWin_1) so background
        or shadow HWNDs are ignored.
        """
        browser_exes = {
            b.lower()
            for b in self.config.get(
                "browser_executables",
                ["firefox.exe", "chrome.exe", "msedge.exe", "brave.exe"],
            )
        }
        title_hint = self.config.get("browser_title_hint", "").strip().lower()

        windows = self._enum_visible_windows()
        browser_wins = [
            (hwnd, title, exe_name, cls_name, area)
            for (hwnd, title, exe_name, cls_name, area) in windows
            if exe_name in browser_exes
            and (
                cls_name in BROWSER_FRAME_CLASSES
                or "mozilla" in cls_name
                or "chrome" in cls_name
            )
        ]
        if not browser_wins:
            browser_wins = [w for w in windows if w[2] in browser_exes]
        if not browser_wins:
            return 0

        if title_hint:
            hinted = [w for w in browser_wins if title_hint in w[1].lower()]
            if hinted:
                self._last_browser_hwnd = hinted[0][0]
                return self._last_browser_hwnd

        dt_matches = [
            w for w in browser_wins if "dilutiontracker" in w[1].lower()
        ]
        if dt_matches:
            self._last_browser_hwnd = dt_matches[0][0]
            return self._last_browser_hwnd

        if self._last_browser_hwnd:
            for hwnd, _, _, _, _ in browser_wins:
                if hwnd == self._last_browser_hwnd:
                    return hwnd

        browser_wins.sort(key=lambda w: -w[4])
        self._last_browser_hwnd = browser_wins[0][0]
        return self._last_browser_hwnd

    def _force_focus_single_attempt(self, hwnd: int) -> None:
        """
        Single low-level attempt to bring hwnd to the foreground WITHOUT ever
        sending VK_MENU (Alt), which would otherwise activate Java Swing's
        menu bar (SC_KEYMENU) in Bookmap and swallow Ctrl+T!
        """
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
            time.sleep(0.04)

        fg_hwnd = user32.GetForegroundWindow()
        fg_tid = (
            user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0
        )
        target_tid = user32.GetWindowThreadProcessId(hwnd, None)
        cur_tid = kernel32.GetCurrentThreadId()

        attached_fg = False
        attached_target = False
        try:
            if fg_tid and fg_tid != cur_tid:
                attached_fg = bool(
                    user32.AttachThreadInput(cur_tid, fg_tid, True)
                )
            if target_tid and target_tid != cur_tid:
                attached_target = bool(
                    user32.AttachThreadInput(cur_tid, target_tid, True)
                )

            # Send unassigned VK 0x0E key-up ONLY (never Alt/VK_MENU!) to satisfy
            # Windows' foreground input lock check without triggering any menu bar
            user32.keybd_event(0x0E, 0, KEYEVENTF_KEYUP, 0)
            user32.ShowWindow(hwnd, SW_SHOW)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(hwnd)
        finally:
            if attached_target:
                user32.AttachThreadInput(cur_tid, target_tid, False)
            if attached_fg:
                user32.AttachThreadInput(cur_tid, fg_tid, False)

        if user32.GetForegroundWindow() != hwnd:
            try:
                app = Application(backend="win32").connect(handle=hwnd)
                app.window(handle=hwnd).set_focus()
            except Exception:
                pass

    def _focus_window(self, hwnd: int, max_attempts: int = 15) -> bool:
        """
        Reliably brings target HWND to the foreground and VERIFIES that
        GetForegroundWindow() == hwnd across up to max_attempts retries.
        """
        if not hwnd:
            return False
        for attempt in range(max_attempts):
            if (
                user32.GetForegroundWindow() == hwnd
                and not user32.IsIconic(hwnd)
            ):
                return True
            try:
                self._force_focus_single_attempt(hwnd)
            except Exception as e:
                log_error(f"Focus attempt {attempt + 1} failed for {hwnd}", e)
            time.sleep(0.04)
            if user32.GetForegroundWindow() == hwnd:
                return True
        return user32.GetForegroundWindow() == hwnd

    def _wait_for_stable_foreground(
        self, hwnd: int, settle_ms: int, max_total_ms: int = 900
    ) -> bool:
        """
        Ensures `hwnd` holds uninterrupted foreground focus for at least `settle_ms`
        milliseconds. If Trade-Ideas or Thinkorswim steals focus during this window,
        immediately re-focuses `hwnd` and resets the settle timer.
        """
        settle_sec = max(0.06, settle_ms / 1000.0)
        deadline = time.perf_counter() + (max_total_ms / 1000.0)
        stable_since = time.perf_counter()

        while time.perf_counter() < deadline:
            if user32.GetForegroundWindow() != hwnd:
                self._focus_window(hwnd, max_attempts=8)
                stable_since = time.perf_counter()
            elif (time.perf_counter() - stable_since) >= settle_sec:
                return True
            time.sleep(0.025)

        return user32.GetForegroundWindow() == hwnd

    # --------------------------------------------------------------------------
    # Deep-Dive Automation Sequence (Step 1: Bookmap -> Step 2: DilutionTracker)
    # --------------------------------------------------------------------------
    def trigger_deep_dive(
        self, source: str = "Manual", release_alt: bool = False
    ) -> None:
        """
        Executes the Deep-Dive sequence for staged_ticker on a background thread.
        """
        if threading.current_thread() is threading.main_thread():
            threading.Thread(
                target=self.trigger_deep_dive,
                kwargs={"source": source, "release_alt": release_alt},
                daemon=True,
            ).start()
            return

        ticker = self._sync_entry_to_staged_ticker()
        if not ticker:
            self.set_status(
                f"{source}: No staged ticker yet! Click a ticker in TI or type one.",
                THEME["fg_warning"],
            )
            return

        # Re-arm select-all in the Edit box so the next TI click or manual click overwrites cleanly
        self._manual_edit_active = False
        if self.edit_hwnd:
            set_window_text(self.edit_hwnd, ticker)
            self._last_raw_entry_text = ticker
            user32.SendMessageW(self.edit_hwnd, EM_SETSEL, 0, -1)

        if not self._automation_lock.acquire(blocking=False):
            return

        self._is_running_sequence = True
        try:
            # 1) If a ticker was JUST staged by Trade-Ideas External Linking (<400ms ago),
            #    wait until 400ms has elapsed since the TI burst so Trade-Ideas
            #    External Linking to Thinkorswim and our box is 100% finished!
            settle_guard_sec = (
                max(0, int(self.config.get("ti_settle_guard_ms", 400)))
                / 1000.0
            )
            elapsed_since_ti = time.perf_counter() - self._last_ti_burst_ts
            if 0.0 <= elapsed_since_ti < settle_guard_sec:
                time.sleep(settle_guard_sec - elapsed_since_ti)
                ticker = self._sync_entry_to_staged_ticker() or ticker

            # 2) Wait until physical Alt, D, and Left Mouse Button are fully released
            wait_for_input_settled(release_alt=release_alt, max_wait_sec=0.60)

            # ==================================================================
            # STEP 1: BOOKMAP AUTOMATION
            # ==================================================================
            self.set_status(
                f"[{ticker}] Step 1/2: Focusing Bookmap...", THEME["fg_warning"]
            )
            bm_hwnd = self._find_bookmap_hwnd()
            if bm_hwnd and self._focus_window(bm_hwnd, max_attempts=15):
                bm_focus_ms = int(
                    self.config.get("bookmap_focus_delay_ms", 220)
                )
                # Ensure Bookmap holds uninterrupted foreground focus for the full bm_focus_ms
                self._wait_for_stable_foreground(
                    bm_hwnd, settle_ms=bm_focus_ms, max_total_ms=900
                )

                wait_for_input_settled(
                    release_alt=release_alt, max_wait_sec=0.15
                )

                # Send Ctrl+T with hardware scan codes and 40ms hold to open Subscribe dialog
                send_ctrl_key_reliable("t", hold_sec=0.04)

                bm_dialog_ms = int(
                    self.config.get("bookmap_dialog_delay_ms", 280)
                )
                time.sleep(max(100, bm_dialog_ms) / 1000.0)

                # Verify a Bookmap window (main chart or Subscribe dialog) is still foreground;
                # if another process stole focus during Ctrl+T, re-focus Bookmap and retry Ctrl+T!
                fg_now = user32.GetForegroundWindow()
                if "bookmap" not in get_hwnd_process_name(fg_now).lower():
                    self._focus_window(bm_hwnd, max_attempts=12)
                    self._wait_for_stable_foreground(
                        bm_hwnd, settle_ms=180, max_total_ms=600
                    )
                    send_ctrl_key_reliable("t", hold_sec=0.04)
                    time.sleep(max(100, bm_dialog_ms) / 1000.0)

                rect = wintypes.RECT()
                user32.GetWindowRect(bm_hwnd, ctypes.byref(rect))
                if self.config.get("bookmap_use_symbol_click", False):
                    rel_x = int(self.config.get("bookmap_symbol_rel_x", 0))
                    rel_y = int(self.config.get("bookmap_symbol_rel_y", 0))
                    if rel_x > 0 or rel_y > 0:
                        pyautogui.click(rect.left + rel_x, rect.top + rel_y)
                        time.sleep(0.05)

                # Copy ticker right before pasting so nothing can overwrite the clipboard
                pyperclip.copy(ticker)
                time.sleep(0.025)

                send_ctrl_key_reliable("a", hold_sec=0.03)
                time.sleep(0.035)
                send_ctrl_key_reliable("v", hold_sec=0.035)
                time.sleep(0.07)

                if self.config.get("bookmap_use_subscribe_click", False):
                    sub_x = int(self.config.get("bookmap_subscribe_rel_x", 0))
                    sub_y = int(self.config.get("bookmap_subscribe_rel_y", 0))
                    if sub_x > 0 or sub_y > 0:
                        pyautogui.click(rect.left + sub_x, rect.top + sub_y)
                        time.sleep(0.04)

                send_enter_reliable(hold_sec=0.04)
                time.sleep(0.10)
            else:
                self.set_status(
                    f"[{ticker}] Bookmap window not found — skipping to Step 2...",
                    THEME["fg_warning"],
                )
                time.sleep(0.10)

            # Wait for Bookmap's Subscribe dialog to finish closing and releasing focus
            trans_ms = int(self.config.get("step_transition_delay_ms", 250))
            time.sleep(max(100, trans_ms) / 1000.0)

            # ==================================================================
            # STEP 2: DILUTIONTRACKER PINNED TAB AUTOMATION
            # ==================================================================
            tab_idx = max(
                1, min(8, int(self.config.get("pinned_tab_index", 6)))
            )
            self.set_status(
                f"[{ticker}] Step 2/2: Browser Ctrl+{tab_idx} -> DilutionTracker...",
                THEME["fg_warning"],
            )

            browser_hwnd = self._find_browser_hwnd()
            if browser_hwnd and self._focus_window(browser_hwnd, max_attempts=15):
                br_focus_ms = int(
                    self.config.get("browser_focus_delay_ms", 120)
                )
                time.sleep(max(40, br_focus_ms) / 1000.0)

                if user32.GetForegroundWindow() != browser_hwnd:
                    self._focus_window(browser_hwnd, max_attempts=8)
                    time.sleep(0.05)

                send_ctrl_key_reliable(str(tab_idx), hold_sec=0.03)

                br_tab_ms = int(self.config.get("browser_tab_delay_ms", 120))
                time.sleep(max(40, br_tab_ms) / 1000.0)

                if user32.GetForegroundWindow() != browser_hwnd:
                    self._focus_window(browser_hwnd, max_attempts=6)
                    time.sleep(0.04)

                url_tmpl = self.config.get(
                    "dilution_tracker_url_template",
                    "https://dilutiontracker.com/app/search/{ticker}",
                )
                target_url = url_tmpl.replace("{ticker}", ticker).replace(
                    "{staged_ticker}", ticker
                )
                pyperclip.copy(target_url)

                send_ctrl_key_reliable("l", hold_sec=0.03)
                br_url_ms = int(self.config.get("browser_url_delay_ms", 100))
                time.sleep(max(40, br_url_ms) / 1000.0)

                send_ctrl_key_reliable("v", hold_sec=0.03)
                time.sleep(0.05)
                send_enter_reliable(hold_sec=0.035)

                time.sleep(0.12)
                pyperclip.copy(ticker)

                self.set_status(
                    f"Deep-Dive Complete: {ticker} ({source})",
                    THEME["fg_success"],
                )
            else:
                self.set_status(
                    f"[{ticker}] Browser window not found or could not focus!",
                    THEME["fg_error"],
                )

        except Exception as e:
            log_error("Deep-Dive execution error", e)
            self.set_status(f"Deep-Dive Error: {e}", THEME["fg_error"])
        finally:
            self._is_running_sequence = False
            self._automation_lock.release()

    # --------------------------------------------------------------------------
    # Clean Shutdown
    # --------------------------------------------------------------------------
    def on_close(self) -> None:
        self._closing = True
        try:
            self.config["window_x"] = self.root.winfo_x()
            self.config["window_y"] = self.root.winfo_y()
            self.save_config()
        except Exception:
            pass

        try:
            if self._hotkey_listener:
                self._hotkey_listener.stop()
        except Exception:
            pass

        self.root.destroy()


def main() -> None:
    sys.excepthook = lambda exc_type, exc_val, exc_tb: log_error(
        "Uncaught exception", exc_val
    )
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass

    close_previous_instances()

    root = tk.Tk()
    app = TickerAutomationApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
