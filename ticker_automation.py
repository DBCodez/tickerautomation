"""
Trade-Ideas <-> Thinkorswim, Bookmap & DilutionTracker Deep-Dive Utility (v3.0)
===============================================================================
A lightweight, always-on-top dark-mode Windows utility (`339x109` collapsed bar) that:
1. Exposes a native Win32 `"Edit"` control targetable by Trade-Ideas External Linking
   AND directly editable by hand (`TI LINK:`).
2. Silently stages tickers on single-click or manual typing (`Staged: XXXX`) without
   Windows system beeps (`ES_MULTILINE | ES_WANTRETURN | ES_AUTOVSCROLL`). Right-clicking
   `Staged: XXXX` opens the 10-ticker Recent History menu for 1-click re-launch.
3. Zero-Click TOS Thumbnail Hover Deep-Dive (`Alt+Q` / Macro Keys `F13`–`F24`):
   - Resolves hovered Thinkorswim Flexible Grid tiles or detached charts with ZERO
     clicks and ZERO keystrokes sent to Thinkorswim.
4. Move to Main TOS (`Alt+M` / Macro Keys `F13`–`F24`):
   - Promotes a hovered thumbnail (or staged ticker when outside grids) onto
     calibrated `Main@thinkorswim` (ding-free, zero `Ctrl+<letter>`), optionally
     chained with Bookmap or Full Deep-Dive.
5. Reverse Thinkorswim -> Deep-Dive Grab (`📥 TOS` or `Alt+G`):
   - Zero-click grid resolution on Flexible Grids + 2-Pass live-trading-safe pull.
6. Pure-Win32 `RegisterHotKey` 7-Slot Matrix (`F13`–`F24` + modifier combos), `0xE8`
   `MenuMaskKey` (never `VK_MENU`), latest-wins job queue, DT wrong-tab guard,
   off-lock async clipboard finalizer (`Win+V` clean), event-driven Bookmap waits,
   detached `⚙` settings window, Hold-Still `🎯` picker, optional Webull Desktop
   integration (100% disabled by default), and scrubbed `🔍 Copy Diagnostics`.
"""

from __future__ import annotations

import calendar
import copy
import ctypes
from ctypes import wintypes
import gzip
import json
import math
import os
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox
import traceback

import psutil

from deepdive_engine import (
    user32,
    gdi32,
    WNDENUMPROC,
    WINDOWPLACEMENT,
    INPUT,
    KEYBDINPUT,
    INPUT_KEYBOARD,
    WS_CHILD,
    WS_VISIBLE,
    WS_TABSTOP,
    ES_LEFT,
    ES_MULTILINE,
    ES_UPPERCASE,
    ES_AUTOVSCROLL,
    ES_AUTOHSCROLL,
    ES_WANTRETURN,
    WM_CLOSE,
    WM_SETFONT,
    WM_SYSCOMMAND,
    SC_RESTORE,
    EM_GETSEL,
    EM_SETSEL,
    VK_TAB,
    KEYEVENTF_KEYUP,
    SPI_SETFOREGROUNDLOCKTIMEOUT,
    SW_RESTORE,
    GA_ROOT,
    set_error_log_callback,
    record_trace,
    enable_per_monitor_dpi_awareness,
    per_monitor_dpi_context,
    get_window_dpi,
    apply_dark_titlebar,
    get_monitor_work_area,
    get_monitor_work_area_for_hwnd,
    is_position_on_any_monitor,
    resolve_tk_toplevel_hwnd,
    make_hwnd_noactivate_transparent,
    show_hwnd_noactivate,
    hide_hwnd_noactivate,
    is_non_us_altgr_layout,
    get_primary_mouse_vk,
    clean_ti_or_manual_ticker,
    parse_valid_ticker,
    parse_tos_ticker,
    normalize_bookmap_symbol,
    matches_dilution_tracker_title_ticker,
    redact_sensitive_text,
    read_shared_file_from_offset,
    get_own_pid,
    get_hwnd_pid,
    get_hwnd_process_name,
    get_window_text,
    get_window_class,
    set_window_text,
    is_tos_process_hwnd,
    is_tos_frame_hwnd,
    normalize_tos_title,
    WindowInfo,
    classify_exe_and_class,
    probe_cursor_target_fast,
    enum_visible_top_windows,
    find_target_window,
    get_enabled_popup_info,
    has_modal_or_popup,
    focus_window_no_alt,
    wait_for_stable_foreground,
    wait_for_input_settled,
    FocusLostError,
    send_ctrl_vk_guarded,
    send_ctrl_key_guarded,
    send_enter_reliable,
    send_escape_reliable,
    click_screen_point_safe,
    flash_highlight_box,
    read_clipboard_unicode,
    write_clipboard_unicode,
    UiaOcrSidecar,
    push_ticker_to_tos_main,
    push_ticker_to_webull,
    grab_ticker_from_webull_ex,
    extract_tos_title_tickers,
    TosGridCacheAndTracker,
    grab_tos_2pass,
    explain_hotkey_rejection,
    parse_win32_hotkey,
    format_hotkey_display,
    tk_keypress_to_hotkey_spec,
    HotkeyManager,
    build_ai_diagnostics_report,
)


APP_VERSION = "3.0"
CONFIG_SCHEMA_VERSION = 3

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
ERROR_LOG_PATH = os.path.join(SCRIPT_DIR, "error.log")
SIDECAR_PS1_PATH = os.path.join(SCRIPT_DIR, "uia_ocr_bridge.ps1")

HOTKEY_ID_DEEP_DIVE = 101
HOTKEY_ID_HOVER_DD = 102
HOTKEY_ID_PROMOTE_TOS = 103
HOTKEY_ID_TOS_GRAB = 104
HOTKEY_ID_BM_ONLY = 105
HOTKEY_ID_DT_ONLY = 106
HOTKEY_ID_WEBULL_ONLY = 107

COACH_HOLD_SEC = 1.20
COACH_TIMEOUT_SEC = 30.0
COACH_ANCHOR_RADIUS_PX = 6.0


def log_error(context: str, exc: BaseException | None = None) -> None:
    try:
        with open(ERROR_LOG_PATH, "a", encoding="utf-8") as f:
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{ts}] {context}\n")
            if exc is not None:
                f.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
            f.write("\n")
    except Exception:
        pass


set_error_log_callback(log_error)


def _safe_int(val, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        res = int(float(str(val).strip()))
    except Exception:
        res = default
    if minimum is not None:
        res = max(minimum, res)
    if maximum is not None:
        res = min(maximum, res)
    return res


DEFAULT_CONFIG = {
    "config_schema_version": CONFIG_SCHEMA_VERSION,
    "pinned_tab_index": 6,
    "hotkey": "<alt>+d",
    "hover_deep_dive_hotkey": "<alt>+q",
    "promote_tos_hotkey": "<alt>+m",
    "promote_tos_mode": "main_only",
    "tos_grab_hotkey": "<alt>+g",
    "bm_only_hotkey": "",
    "dt_only_hotkey": "",
    "tos_symbol_rel_x": 0,
    "tos_symbol_rel_y": 0,
    "tos_window_title": "",
    "tos_window_rect": None,
    "tos_window_maximized": None,
    "tos_window_dpi": 0,
    "ti_settle_guard_ms": 400,
    "bookmap_window_title": "Bookmap",
    "bookmap_focus_delay_ms": 220,
    "bookmap_dialog_delay_ms": 280,
    "bookmap_switch_existing_tab": True,
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
    "dt_tab_title_hint": "dilution",
    "dt_open_new_tab_if_missing": False,
    "dt_skip_if_same_ticker": True,
    "clipboard_after_deep_dive": "keep_ticker",
    "recent_tickers": [],
    "webull_enabled": False,
    "webull_include_in_deep_dive": True,
    "webull_allow_hover_source": True,
    "webull_sync_hotkey": "",
    "webull_window_title": "Webull Desktop",
    "webull_window_rect": None,
    "webull_window_maximized": None,
    "webull_window_dpi": 0,
    "webull_symbol_rel_x": 0,
    "webull_symbol_rel_y": 0,
    "webull_focus_settle_ms": 85,
    "webull_dropdown_delay_ms": 260,
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
    "bg_tos_btn": "#0E7490",
    "bg_tos_btn_hover": "#0891B2",
    "bg_bm_btn": "#8957E5",
    "bg_bm_btn_hover": "#A371F7",
    "bg_dt_btn": "#1F6FEB",
    "bg_dt_btn_hover": "#388BFD",
    "bg_coach": "#11161D",
    "fg_primary": "#F0F6FC",
    "fg_secondary": "#8B949E",
    "fg_ticker": "#58A6FF",
    "fg_success": "#3FB950",
    "fg_warning": "#D29922",
    "fg_error": "#F85149",
    "border": "#30363D",
    "border_focus": "#58A6FF",
}

_BM_COMMON_LOG_RE = re.compile(
    r"^(log_(\d{8})_(\d{6})_(\d{3}))-common-(\d+)\.txt(\.gz)?$",
    re.IGNORECASE,
)
_BM_ADD_SYM_RE = re.compile(r"Adding instrument symbol=([^,\s]+)")
_BM_REM_TAB_RE = re.compile(r"Removing tab symbol=([^,\s]+)")

_PROMOTE_MODES = {
    "main_only": "Main TOS Only",
    "main_and_bm": "Main TOS + BM Only",
    "main_and_deep_dive": "Main TOS + Full Deep-Dive",
}
_PROMOTE_LABELS_REV = {v: k for k, v in _PROMOTE_MODES.items()}

_CLIP_MODES = {
    "keep_ticker": "Keep Ticker",
    "restore_previous": "Restore Previous Text",
}
_CLIP_LABELS_REV = {v: k for k, v in _CLIP_MODES.items()}

_SAFE_BM_POPUP_ALLOW = ("subscribe", "instrument", "add symbol", "unsubscribe")
_DANGEROUS_BM_POPUP_DENY = ("order", "confirm", "login", "log in", "connect", "update", "exit", "close", "license")


def close_previous_instances() -> None:
    own_pid = os.getpid()

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or get_hwnd_pid(hwnd) == own_pid:
            return True
        title = get_window_text(hwnd)
        cls_name = get_window_class(hwnd).lower()
        if title.startswith("TI Ticker Deep-Dive") and "tk" in cls_name:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        return True

    try:
        user32.EnumWindows(WNDENUMPROC(cb), 0)
    except Exception:
        pass


# ==============================================================================
# FLOATING CLICK-THROUGH CURSOR COACH OVERLAY ("HOLD STILL 1.2s")
# ==============================================================================
class CursorCoachOverlay:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._visible = False
        self._last_pos = (-99999, -99999)
        self._top = tk.Toplevel(root)
        self._top.withdraw()
        self._top.overrideredirect(True)
        self._top.configure(bg=THEME["bg_coach"], highlightbackground=THEME["fg_warning"], highlightthickness=2)
        inner = tk.Frame(self._top, bg=THEME["bg_coach"], padx=10, pady=7)
        inner.pack()
        self._lbl_title = tk.Label(
            inner, text="", font=("Segoe UI", 9, "bold"), fg=THEME["fg_primary"], bg=THEME["bg_coach"], anchor="w"
        )
        self._lbl_title.pack(anchor="w")
        self._lbl_sub = tk.Label(
            inner, text="", font=("Segoe UI", 8), fg=THEME["fg_secondary"], bg=THEME["bg_coach"], anchor="w"
        )
        self._lbl_sub.pack(anchor="w", pady=(2, 0))
        self._top.update_idletasks()
        self._hwnd = resolve_tk_toplevel_hwnd(int(self._top.winfo_id()))
        make_hwnd_noactivate_transparent(self._hwnd)

    def update_at_cursor(self, cx: int, cy: int, headline: str, subtitle: str, border_color: str) -> None:
        self._top.configure(highlightbackground=border_color)
        self._lbl_title.configure(text=headline)
        self._lbl_sub.configure(text=subtitle)
        self._top.update_idletasks()
        req_w = int(self._top.winfo_reqwidth())
        req_h = int(self._top.winfo_reqheight())
        wl, wt, wr, wb = get_monitor_work_area(cx, cy)
        bx = cx + 24
        by = cy + 28
        if bx + req_w > wr - 8:
            bx = max(wl + 8, cx - req_w - 20)
        if by + req_h > wb - 8:
            by = max(wt + 8, cy - req_h - 20)
        if not self._visible or abs(bx - self._last_pos[0]) > 3 or abs(by - self._last_pos[1]) > 3:
            self._last_pos = (bx, by)
            show_hwnd_noactivate(self._hwnd, bx, by, req_w, req_h)
            self._visible = True

    def hide(self) -> None:
        if self._visible:
            hide_hwnd_noactivate(self._hwnd)
            self._visible = False
        self._last_pos = (-99999, -99999)


# ==============================================================================
# MAIN APPLICATION CLASS
# ==============================================================================
class TickerAutomationApp:
    def __init__(self, root: tk.Tk, dpi_mode: str = "PerMonitorV2") -> None:
        self.root = root
        self.dpi_mode = dpi_mode
        self.config = self.load_config()
        self._own_pid = get_own_pid()
        self._closing = False

        # Core staging state (mutated ONLY on Tk main thread!)
        self.staged_ticker: str = ""
        self._unselected_idle_prefix: str = ""
        self._last_raw_entry_text: str = ""
        self._last_entry_change_ts: float = 0.0
        self._last_ti_burst_ts: float = 0.0
        self._entry_burst_armed_for_overwrite: bool = True

        # Recent tickers (MRU cap 10, synced with config["recent_tickers"])
        self._recent_tickers: list[str] = [
            sym for sym in (clean_ti_or_manual_ticker(s) for s in self.config.get("recent_tickers", [])) if sym
        ][:10]

        # Manual typing & click focus state
        self._top_hwnd: int = 0
        self._manual_edit_active: bool = False
        self._was_our_window_fg: bool = False
        self._prev_mouse_down: bool = False

        # Single-worker + 1-slot latest-wins job queue
        self._job_lock = threading.Lock()
        self._queue_lock = threading.Lock()
        self._worker_busy = False
        self._pending_job: dict | None = None
        self._is_running_sequence = False

        # Off-lock clipboard finalization generation state
        self._clip_lock = threading.Lock()
        self._clip_gen: int = 0
        self._clip_restore_pending: bool = False
        self._clip_saved_ok: bool = False
        self._clip_saved_txt: str | None = None

        # Remembered HWNDs & DT refresh timing
        self._last_browser_hwnd: int = 0
        self._calibrated_tos_hwnd: int = 0
        self._last_tos_clicked_hwnd: int = 0
        self._last_tos_click_ts: float = 0.0
        self._last_dt_nav_ts: dict[str, float] = {}

        # Bookmap session log tracker
        self._bm_logs_dir_cache: tuple[int, str] = (0, "")
        self._bm_log_session_key: tuple[int, str] = (0, "")
        self._bm_log_offsets: dict[int, int] = {}
        self._bm_log_gz_done: set[int] = set()
        self._bm_subscribed_tabs: list[str] = []
        self._bm_add_seq: int = 0
        self._bm_last_added_sym: str = ""
        self._bm_already_sub_seq: int = 0
        self._bm_last_already_sub_sym: str = ""

        # Win32 Edit HWND & font
        self.edit_hwnd: int = 0
        self._edit_font: int = 0

        # TOS Grid Tracker & Lazy UIA/OCR Sidecar
        self.grid_tracker = TosGridCacheAndTracker()
        self.sidecar = UiaOcrSidecar(SIDECAR_PS1_PATH)

        # Detached settings window & interactive Bind / Hold-Still Picker states
        self._settings_win: tk.Toplevel | None = None
        self._settings_hwnd: int = 0
        self._hotkey_vars: dict[str, tk.StringVar] = {}
        self._hotkey_status_vars: dict[str, tk.StringVar] = {}
        self._hotkey_status_lbls: dict[str, tk.Label] = {}
        self._webull_child_widgets: list[tk.Widget] = []
        self._pick_buttons: list[tk.Button] = []
        self._bind_target_key: str | None = None
        self._bind_after_id: str | None = None
        self._coach_active: bool = False
        self._coach_after_id: str | None = None
        self._coach_pid_cache: dict[int, tuple[str, str]] = {}
        self.hotkey_statuses: dict[int, tuple[bool, str]] = {}

        try:
            user32.SystemParametersInfoW(SPI_SETFOREGROUNDLOCKTIMEOUT, 0, ctypes.c_void_p(0), 0)
        except Exception:
            pass

        self._setup_window()
        self._build_gui()
        self._create_win32_entry()
        self.coach_overlay = CursorCoachOverlay(self.root)
        self.hotkeys = HotkeyManager(snapshot_fn=self._capture_press_snapshot)
        self._register_all_hotkeys()
        self._schedule_entry_poll()
        self.root.after(1100, self._schedule_background_grid_warmup)

    # --------------------------------------------------------------------------
    # Configuration Persistence
    # --------------------------------------------------------------------------
    def load_config(self) -> dict:
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        loaded_schema = 0
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    loaded_schema = _safe_int(loaded.get("config_schema_version", 0), 0)
                    cfg.update(loaded)
            except Exception as e:
                log_error("Failed to read config.json", e)

        cfg["pinned_tab_index"] = _safe_int(cfg.get("pinned_tab_index"), 6, 1, 8)
        cfg["tos_symbol_rel_x"] = _safe_int(cfg.get("tos_symbol_rel_x"), 0, -10000, 30000)
        cfg["tos_symbol_rel_y"] = _safe_int(cfg.get("tos_symbol_rel_y"), 0, -10000, 30000)
        cfg["tos_window_dpi"] = _safe_int(cfg.get("tos_window_dpi"), 0, 0, 960)
        cfg["ti_settle_guard_ms"] = _safe_int(cfg.get("ti_settle_guard_ms"), 400, 0, 5000)
        cfg["bookmap_focus_delay_ms"] = _safe_int(cfg.get("bookmap_focus_delay_ms"), 220, 50, 5000)
        cfg["bookmap_dialog_delay_ms"] = _safe_int(cfg.get("bookmap_dialog_delay_ms"), 280, 50, 5000)
        cfg["bookmap_switch_existing_tab"] = bool(cfg.get("bookmap_switch_existing_tab", True))
        cfg["step_transition_delay_ms"] = _safe_int(cfg.get("step_transition_delay_ms"), 250, 50, 5000)
        cfg["browser_focus_delay_ms"] = _safe_int(cfg.get("browser_focus_delay_ms"), 120, 20, 5000)
        cfg["browser_tab_delay_ms"] = _safe_int(cfg.get("browser_tab_delay_ms"), 120, 20, 5000)
        cfg["browser_url_delay_ms"] = _safe_int(cfg.get("browser_url_delay_ms"), 100, 20, 5000)
        if cfg.get("promote_tos_mode") not in _PROMOTE_MODES:
            cfg["promote_tos_mode"] = "main_only"
        if cfg.get("clipboard_after_deep_dive") not in _CLIP_MODES:
            cfg["clipboard_after_deep_dive"] = "keep_ticker"
        for hk_k in (
            "hotkey", "hover_deep_dive_hotkey", "promote_tos_hotkey",
            "tos_grab_hotkey", "bm_only_hotkey", "dt_only_hotkey", "webull_sync_hotkey",
        ):
            if not isinstance(cfg.get(hk_k), str):
                cfg[hk_k] = DEFAULT_CONFIG[hk_k]

        cfg["webull_enabled"] = bool(cfg.get("webull_enabled", False))
        cfg["webull_include_in_deep_dive"] = bool(cfg.get("webull_include_in_deep_dive", True))
        cfg["webull_allow_hover_source"] = bool(cfg.get("webull_allow_hover_source", True))
        cfg["webull_symbol_rel_x"] = _safe_int(cfg.get("webull_symbol_rel_x"), 0, -10000, 30000)
        cfg["webull_symbol_rel_y"] = _safe_int(cfg.get("webull_symbol_rel_y"), 0, -10000, 30000)
        cfg["webull_focus_settle_ms"] = _safe_int(cfg.get("webull_focus_settle_ms"), 85, 20, 2000)
        cfg["webull_dropdown_delay_ms"] = _safe_int(cfg.get("webull_dropdown_delay_ms"), 260, 40, 2000)

        if loaded_schema < CONFIG_SCHEMA_VERSION:
            cfg["config_schema_version"] = CONFIG_SCHEMA_VERSION
        self.save_config(cfg)
        return cfg

    def save_config(self, cfg: dict | None = None) -> None:
        target = cfg if cfg is not None else self.config
        tmp_path = CONFIG_PATH + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(target, f, indent=4)
            os.replace(tmp_path, CONFIG_PATH)
        except Exception as e:
            log_error("Failed to save config.json", e)
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

    # --------------------------------------------------------------------------
    # Compact Main Bar GUI (`339x109` exact footprint)
    # --------------------------------------------------------------------------
    def _setup_window(self) -> None:
        self.root.title(f"TI Ticker Deep-Dive v{APP_VERSION}")
        self.root.configure(bg=THEME["bg_main"])
        self.root.resizable(False, False)
        always_top = bool(self.config.get("always_on_top", True))
        self.root.attributes("-topmost", always_top)
        wx, wy = self.config.get("window_x"), self.config.get("window_y")
        if isinstance(wx, int) and isinstance(wy, int) and is_position_on_any_monitor(wx, wy):
            self.root.geometry(f"+{wx}+{wy}")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_gui(self) -> None:
        self.main_frame = tk.Frame(self.root, bg=THEME["bg_main"], padx=10, pady=8)
        self.main_frame.pack(fill="both", expand=True)

        # ---- ROW 1: TI LINK Win32 Edit + Staged Label (Right-Click Recent Tickers) ----
        top_row = tk.Frame(self.main_frame, bg=THEME["bg_main"])
        top_row.pack(fill="x", pady=(0, 6))

        target_lbl = tk.Label(
            top_row, text="TI LINK:", font=("Segoe UI", 8, "bold"),
            fg=THEME["fg_secondary"], bg=THEME["bg_main"], cursor="xterm",
        )
        target_lbl.pack(side="left", padx=(0, 5))
        target_lbl.bind("<Button-1>", lambda _e: self.focus_manual_entry())

        self.entry_border = tk.Frame(top_row, bg=THEME["border_focus"], padx=1, pady=1)
        self.entry_border.pack(side="left", padx=(0, 10))
        self.entry_container = tk.Frame(self.entry_border, width=92, height=26, bg=THEME["bg_input"])
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
        self.staged_label.bind("<Button-1>", lambda _e: self.focus_manual_entry())
        self.staged_label.bind("<Button-3>", self._show_recent_tickers_menu)

        # ---- ROW 2: Pinned Tab Selector + Main Deep-Dive Button + Detached Settings ----
        ctrl_row = tk.Frame(self.main_frame, bg=THEME["bg_main"])
        ctrl_row.pack(fill="x", pady=(0, 4))

        tk.Label(
            ctrl_row, text="DT Tab (Ctrl+):", font=("Segoe UI", 9),
            fg=THEME["fg_secondary"], bg=THEME["bg_main"],
        ).pack(side="left", padx=(0, 4))

        self.tab_index_var = tk.IntVar(value=_safe_int(self.config.get("pinned_tab_index", 6), 6, 1, 8))
        self.tab_spinbox = tk.Spinbox(
            ctrl_row, from_=1, to=8, width=3, textvariable=self.tab_index_var,
            font=("Consolas", 10, "bold"), bg=THEME["bg_input"], fg=THEME["fg_primary"],
            buttonbackground=THEME["bg_button"], insertbackground=THEME["fg_primary"],
            relief="flat", justify="center", command=self._on_quick_tab_change,
        )
        self.tab_spinbox.bind("<Return>", lambda _e: self._on_quick_tab_change())
        self.tab_spinbox.bind("<FocusOut>", lambda _e: self._on_quick_tab_change())
        self.tab_spinbox.pack(side="left", padx=(0, 8))

        self.launch_btn = tk.Button(
            ctrl_row,
            text=self._launch_button_text(),
            font=("Segoe UI", 9, "bold"),
            bg=THEME["bg_accent"],
            fg=THEME["fg_primary"],
            activebackground=THEME["bg_accent_hover"],
            activeforeground=THEME["fg_primary"],
            relief="flat",
            padx=8,
            pady=2,
            cursor="hand2",
            command=lambda: self._enqueue_action("deep_dive", "GUI Button"),
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

        # ---- ROW 3: Live Status Bar + TOS / BM Only / DT Only ----
        bottom_row = tk.Frame(self.main_frame, bg=THEME["bg_main"])
        bottom_row.pack(fill="x", pady=(2, 0))

        self.status_var = tk.StringVar(value=f"v{APP_VERSION} Ready • TI / TOS")
        self.status_bar = tk.Label(
            bottom_row, textvariable=self.status_var, font=("Segoe UI", 8),
            fg=THEME["fg_secondary"], bg=THEME["bg_main"], anchor="w", width=13,
        )
        self.status_bar.pack(side="left", fill="x", expand=True)

        self.dt_only_btn = tk.Button(
            bottom_row, text="🌐 DT", font=("Segoe UI", 8, "bold"),
            bg=THEME["bg_dt_btn"], fg=THEME["fg_primary"],
            activebackground=THEME["bg_dt_btn_hover"], activeforeground=THEME["fg_primary"],
            relief="flat", padx=6, pady=1, cursor="hand2",
            command=lambda: self._enqueue_action("dt_only", "DT Only"),
        )
        self.dt_only_btn.pack(side="right", padx=(4, 0))

        self.bm_only_btn = tk.Button(
            bottom_row, text="📊 BM", font=("Segoe UI", 8, "bold"),
            bg=THEME["bg_bm_btn"], fg=THEME["fg_primary"],
            activebackground=THEME["bg_bm_btn_hover"], activeforeground=THEME["fg_primary"],
            relief="flat", padx=6, pady=1, cursor="hand2",
            command=lambda: self._enqueue_action("bm_only", "BM Only"),
        )
        self.bm_only_btn.pack(side="right", padx=(4, 0))

        self.tos_grab_btn = tk.Button(
            bottom_row, text="📥 TOS", font=("Segoe UI", 8, "bold"),
            bg=THEME["bg_tos_btn"], fg=THEME["fg_primary"],
            activebackground=THEME["bg_tos_btn_hover"], activeforeground=THEME["fg_primary"],
            relief="flat", padx=5, pady=1, cursor="hand2",
            command=lambda: self._enqueue_action("tos_grab", "TOS Button"),
        )
        self.tos_grab_btn.pack(side="right", padx=(4, 0))

        self.root.bind("<KeyPress>", self._on_root_keypress)

    def _launch_button_text(self) -> str:
        disp = format_hotkey_display(self.config.get("hotkey") or "<alt>+d")
        if disp and len(disp) <= 7:
            return f"🚀 Deep-Dive ({disp})"
        return "🚀 Deep-Dive"

    def _on_quick_tab_change(self) -> None:
        try:
            idx = _safe_int(self.tab_index_var.get(), 6, 1, 8)
            self.tab_index_var.set(idx)
            self.config["pinned_tab_index"] = idx
            self.save_config()
            self.set_status(f"Saved DilutionTracker tab: Ctrl+{idx}", THEME["fg_success"])
        except Exception:
            pass

    # --------------------------------------------------------------------------
    # Right-Click Recent Tickers Back-Stack on `Staged: XXXX`
    # --------------------------------------------------------------------------
    def _record_recent_ticker(self, ticker: str) -> None:
        sym = parse_valid_ticker(ticker) or clean_ti_or_manual_ticker(ticker)
        if not sym:
            return

        def _apply():
            if sym in self._recent_tickers:
                self._recent_tickers.remove(sym)
            self._recent_tickers.insert(0, sym)
            del self._recent_tickers[10:]
            self.config["recent_tickers"] = list(self._recent_tickers)
            self.save_config()

        if threading.current_thread() is threading.main_thread():
            _apply()
        else:
            self.root.after(0, _apply)

    def _show_recent_tickers_menu(self, event: tk.Event) -> None:
        menu = tk.Menu(
            self.root, tearoff=0,
            bg=THEME["bg_card"], fg=THEME["fg_primary"],
            activebackground=THEME["bg_accent"], activeforeground=THEME["fg_primary"],
            disabledforeground=THEME["fg_secondary"],
            font=("Consolas", 9),
        )
        if not self._recent_tickers:
            menu.add_command(label="No recent tickers yet", state="disabled")
        else:
            for sym in self._recent_tickers[:10]:
                menu.add_command(
                    label=f"🚀 Deep-Dive {sym}",
                    command=lambda s=sym: self._run_recent_action("deep_dive", s),
                )
            menu.add_separator()
            sub_stage = tk.Menu(menu, tearoff=0, bg=THEME["bg_card"], fg=THEME["fg_primary"],
                                activebackground=THEME["bg_button_hover"], activeforeground=THEME["fg_primary"],
                                font=("Consolas", 9))
            sub_bm = tk.Menu(menu, tearoff=0, bg=THEME["bg_card"], fg=THEME["fg_primary"],
                             activebackground=THEME["bg_bm_btn"], activeforeground=THEME["fg_primary"],
                             font=("Consolas", 9))
            sub_main = tk.Menu(menu, tearoff=0, bg=THEME["bg_card"], fg=THEME["fg_primary"],
                               activebackground=THEME["bg_tos_btn"], activeforeground=THEME["fg_primary"],
                               font=("Consolas", 9))
            for sym in self._recent_tickers[:10]:
                sub_stage.add_command(
                    label=sym,
                    command=lambda s=sym: self._stage_ticker_atomic(s, f"Staged {s} • Ready"),
                )
                sub_bm.add_command(
                    label=sym,
                    command=lambda s=sym: self._run_recent_action("bm_only", s),
                )
                sub_main.add_command(
                    label=sym,
                    command=lambda s=sym: self._run_recent_action("promote_tos", s),
                )
            menu.add_cascade(label="📌 Stage Only", menu=sub_stage)
            menu.add_cascade(label="📊 Bookmap Only", menu=sub_bm)
            menu.add_cascade(label="🎯 Move to Main TOS", menu=sub_main)
            menu.add_separator()
            menu.add_command(label="🧹 Clear Recent", command=self._clear_recent_tickers)

        try:
            menu.tk_popup(int(event.x_root), int(event.y_root))
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _clear_recent_tickers(self) -> None:
        self._recent_tickers.clear()
        self.config["recent_tickers"] = []
        self.save_config()
        self.set_status("Cleared recent tickers", THEME["fg_secondary"])

    def _run_recent_action(self, action: str, sym: str) -> None:
        self._stage_ticker_atomic(sym, f"Staged {sym} • Running...")
        self._enqueue_action(action, f"Recent ({sym})", explicit_ticker=sym)

    # --------------------------------------------------------------------------
    # Detached Settings Window (`⚙`) — Single-Instance, Monitor-Clamped Toplevel
    # --------------------------------------------------------------------------
    def _toggle_settings_panel(self) -> None:
        if self._settings_win is not None and self._settings_win.winfo_exists():
            self._close_settings_window()
            return
        self._open_settings_window()

    def _close_settings_window(self) -> None:
        self._cancel_bind_mode(resume_hotkeys=True)
        w = self._settings_win
        self._settings_win = None
        self._settings_hwnd = 0
        self._hotkey_vars.clear()
        self._hotkey_status_vars.clear()
        self._hotkey_status_lbls.clear()
        self._webull_child_widgets.clear()
        self._pick_buttons.clear()
        if w is not None:
            try:
                w.destroy()
            except Exception:
                pass

    def _open_settings_window(self) -> None:
        win = tk.Toplevel(self.root)
        self._settings_win = win
        win.title(f"TI Ticker Deep-Dive v{APP_VERSION} — Settings & Macro Keys")
        win.configure(bg=THEME["bg_main"])
        win.resizable(False, False)
        win.transient(self.root)
        win.attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", self._close_settings_window)
        win.bind("<KeyPress>", self._on_root_keypress)
        win.bind("<Escape>", lambda _e: self._on_settings_escape())

        self._init_settings_vars()

        outer = tk.Frame(win, bg=THEME["bg_main"], padx=10, pady=8)
        outer.pack(fill="both", expand=True)

        def make_card(title: str) -> tk.Frame:
            card = tk.Frame(
                outer, bg=THEME["bg_card"], padx=8, pady=6,
                highlightbackground=THEME["border"], highlightthickness=1,
            )
            card.pack(fill="x", pady=(0, 6))
            tk.Label(
                card, text=title, font=("Segoe UI", 8, "bold"),
                fg=THEME["fg_ticker"], bg=THEME["bg_card"],
            ).grid(row=0, column=0, columnspan=5, sticky="w", pady=(0, 4))
            return card

        def make_lbl(p, text, r, c):
            lbl = tk.Label(p, text=text, font=("Segoe UI", 8), fg=THEME["fg_secondary"], bg=THEME["bg_card"], anchor="w")
            lbl.grid(row=r, column=c, sticky="w", padx=(0, 6), pady=2)
            return lbl

        def make_ent(p, var, width, r, c, **kw):
            ent = tk.Entry(
                p, textvariable=var, width=width, font=("Consolas", 9),
                bg=THEME["bg_input"], fg=THEME["fg_primary"],
                insertbackground=THEME["fg_primary"], relief="flat", **kw,
            )
            ent.grid(row=r, column=c, sticky="w", padx=(0, 6), pady=2)
            return ent

        # ---- CARD 1: GLOBAL HOTKEYS & MACRO KEYS (F13-F24 READY) ----
        c_hk = make_card("GLOBAL HOTKEYS & MACRO KEYS (F13–F24 OR MODIFIER COMBOS)")
        slot_rows = [
            ("Staged Deep-Dive:", "hotkey", HOTKEY_ID_DEEP_DIVE),
            ("Hover Chart → Deep-Dive:", "hover_deep_dive_hotkey", HOTKEY_ID_HOVER_DD),
            ("Move to Main TOS:", "promote_tos_hotkey", HOTKEY_ID_PROMOTE_TOS),
            ("TOS Grab (Stage Only):", "tos_grab_hotkey", HOTKEY_ID_TOS_GRAB),
            ("Bookmap Only:", "bm_only_hotkey", HOTKEY_ID_BM_ONLY),
            ("DilutionTracker Only:", "dt_only_hotkey", HOTKEY_ID_DT_ONLY),
        ]
        for row_idx, (label_txt, cfg_key, hk_id) in enumerate(slot_rows, start=1):
            make_lbl(c_hk, label_txt, row_idx, 0)
            svar = tk.StringVar(value=str(self.config.get(cfg_key, "")))
            self._hotkey_vars[cfg_key] = svar
            make_ent(c_hk, svar, 14, row_idx, 1)
            tk.Button(
                c_hk, text="⌨ Bind", font=("Segoe UI", 8),
                bg=THEME["bg_button"], fg=THEME["fg_primary"],
                activebackground=THEME["bg_button_hover"], activeforeground=THEME["fg_primary"],
                relief="flat", padx=6, cursor="hand2",
                command=lambda k=cfg_key, hid=hk_id: self._start_hotkey_bind(k, hid),
            ).grid(row=row_idx, column=2, padx=(0, 4), pady=2)
            tk.Button(
                c_hk, text="✕", font=("Segoe UI", 8),
                bg=THEME["bg_button"], fg=THEME["fg_secondary"],
                activebackground=THEME["bg_button_hover"], activeforeground=THEME["fg_primary"],
                relief="flat", padx=5, cursor="hand2",
                command=lambda k=cfg_key: self._clear_hotkey_slot(k),
            ).grid(row=row_idx, column=3, padx=(0, 6), pady=2)
            st_var = tk.StringVar(value="")
            st_lbl = tk.Label(c_hk, textvariable=st_var, font=("Consolas", 8), fg=THEME["fg_secondary"], bg=THEME["bg_card"], anchor="w", width=18)
            st_lbl.grid(row=row_idx, column=4, sticky="w", pady=2)
            self._hotkey_status_vars[cfg_key] = st_var
            self._hotkey_status_lbls[cfg_key] = st_lbl

        make_lbl(c_hk, "Move to Main Mode:", 7, 0)
        om_promote = tk.OptionMenu(c_hk, self.var_promote_mode_lbl, *_PROMOTE_MODES.values())
        om_promote.configure(
            font=("Segoe UI", 8), bg=THEME["bg_button"], fg=THEME["fg_primary"],
            activebackground=THEME["bg_button_hover"], activeforeground=THEME["fg_primary"],
            highlightthickness=0, relief="flat", anchor="w",
        )
        om_promote["menu"].configure(bg=THEME["bg_card"], fg=THEME["fg_primary"], font=("Segoe UI", 8))
        om_promote.grid(row=7, column=1, columnspan=4, sticky="w", pady=(2, 2))

        tk.Label(
            c_hk,
            text="Tip: Standalone F13–F24 work too (map mouse/StreamDeck buttons to F13–F24). Check TOS Setup → Hotkeys so Alt combos don't overlap.",
            font=("Segoe UI", 7), fg=THEME["fg_secondary"], bg=THEME["bg_card"], anchor="w", wraplength=445, justify="left",
        ).grid(row=8, column=0, columnspan=5, sticky="w", pady=(3, 0))

        # ---- CARD 2: THINKORSWIM MAIN CHART ----
        c_tos = make_card("THINKORSWIM MAIN CHART (PASS 2 & MOVE-TO-MAIN TARGET)")
        make_lbl(c_tos, "Main TOS Window:", 1, 0)
        tk.Label(c_tos, textvariable=self.var_tos_win_lbl, font=("Consolas", 8), fg=THEME["fg_primary"], bg=THEME["bg_card"]).grid(row=1, column=1, columnspan=3, sticky="w", pady=2)
        make_lbl(c_tos, "Main Symbol Box (X, Y):", 2, 0)
        tos_cf = tk.Frame(c_tos, bg=THEME["bg_card"])
        tos_cf.grid(row=2, column=1, columnspan=4, sticky="w")
        make_ent(tos_cf, self.var_tos_x, 5, 0, 0)
        make_ent(tos_cf, self.var_tos_y, 5, 0, 1)
        b_pick_tos = tk.Button(
            tos_cf, text="🎯 Pick (Hold 1.2s)", font=("Segoe UI", 8),
            bg=THEME["bg_button"], fg=THEME["fg_primary"],
            activebackground=THEME["bg_button_hover"], relief="flat", padx=7, cursor="hand2",
            command=lambda: self._start_hold_still_picker("tos"),
        )
        b_pick_tos.grid(row=0, column=2, padx=(4, 0))
        self._pick_buttons.append(b_pick_tos)

        # ---- CARD 3: BOOKMAP & DILUTIONTRACKER BROWSER ----
        c_bm = make_card("BOOKMAP & DILUTIONTRACKER BROWSER")
        make_lbl(c_bm, "BM Focus / Ctrl+T Cap (ms):", 1, 0)
        bm_ms_f = tk.Frame(c_bm, bg=THEME["bg_card"])
        bm_ms_f.grid(row=1, column=1, sticky="w")
        make_ent(bm_ms_f, self.var_bm_focus_ms, 5, 0, 0)
        make_ent(bm_ms_f, self.var_bm_dialog_ms, 5, 0, 1)
        make_lbl(c_bm, "BM Title / TI Guard (ms):", 1, 2)
        bm_ti_f = tk.Frame(c_bm, bg=THEME["bg_card"])
        bm_ti_f.grid(row=1, column=3, columnspan=2, sticky="w")
        make_ent(bm_ti_f, self.var_bm_title, 10, 0, 0)
        make_ent(bm_ti_f, self.var_ti_guard_ms, 5, 0, 1)

        make_lbl(c_bm, "Browser Tab / URL (ms):", 2, 0)
        br_ms_f = tk.Frame(c_bm, bg=THEME["bg_card"])
        br_ms_f.grid(row=2, column=1, sticky="w")
        make_ent(br_ms_f, self.var_browser_tab_ms, 5, 0, 0)
        make_ent(br_ms_f, self.var_browser_url_ms, 5, 0, 1)
        make_lbl(c_bm, "DT Title Hint:", 2, 2)
        make_ent(c_bm, self.var_dt_title_hint, 13, 2, 3)

        bm_chk_f = tk.Frame(c_bm, bg=THEME["bg_card"])
        bm_chk_f.grid(row=3, column=0, columnspan=5, sticky="w", pady=(2, 2))
        for txt, bvar in (
            ("Auto-switch existing BM tab", self.var_bm_switch_tab),
            ("Skip DT reload if same ticker", self.var_dt_skip_same),
            ("New tab if Ctrl+N isn't DT", self.var_dt_open_new),
        ):
            tk.Checkbutton(
                bm_chk_f, text=txt, variable=bvar, font=("Segoe UI", 8),
                fg=THEME["fg_primary"], bg=THEME["bg_card"], selectcolor=THEME["bg_input"],
                activebackground=THEME["bg_card"], activeforeground=THEME["fg_primary"],
            ).pack(side="left", padx=(0, 8))

        make_lbl(c_bm, "Clipboard After Run:", 4, 0)
        om_clip = tk.OptionMenu(c_bm, self.var_clip_mode_lbl, *_CLIP_MODES.values())
        om_clip.configure(
            font=("Segoe UI", 8), bg=THEME["bg_button"], fg=THEME["fg_primary"],
            activebackground=THEME["bg_button_hover"], activeforeground=THEME["fg_primary"],
            highlightthickness=0, relief="flat", anchor="w",
        )
        om_clip["menu"].configure(bg=THEME["bg_card"], fg=THEME["fg_primary"], font=("Segoe UI", 8))
        om_clip.grid(row=4, column=1, sticky="w", pady=2)

        fb_f = tk.Frame(c_bm, bg=THEME["bg_card"])
        fb_f.grid(row=5, column=0, columnspan=5, sticky="w", pady=(3, 0))
        tk.Checkbutton(
            fb_f, text="BM Sym Click:", variable=self.var_use_sym_click, font=("Segoe UI", 8),
            fg=THEME["fg_secondary"], bg=THEME["bg_card"], selectcolor=THEME["bg_input"],
            activebackground=THEME["bg_card"], activeforeground=THEME["fg_primary"],
        ).pack(side="left")
        tk.Entry(
            fb_f, textvariable=self.var_sym_x, width=4, font=("Consolas", 8),
            bg=THEME["bg_input"], fg=THEME["fg_primary"], insertbackground=THEME["fg_primary"], relief="flat",
        ).pack(side="left", padx=2)
        tk.Entry(
            fb_f, textvariable=self.var_sym_y, width=4, font=("Consolas", 8),
            bg=THEME["bg_input"], fg=THEME["fg_primary"], insertbackground=THEME["fg_primary"], relief="flat",
        ).pack(side="left", padx=(0, 3))
        b_bm_sym = tk.Button(fb_f, text="🎯", font=("Segoe UI", 8), bg=THEME["bg_button"], fg=THEME["fg_primary"], relief="flat", padx=4, cursor="hand2", command=lambda: self._start_hold_still_picker("bm_symbol"))
        b_bm_sym.pack(side="left", padx=(0, 8))
        self._pick_buttons.append(b_bm_sym)

        tk.Checkbutton(
            fb_f, text="BM Sub Click:", variable=self.var_use_sub_click, font=("Segoe UI", 8),
            fg=THEME["fg_secondary"], bg=THEME["bg_card"], selectcolor=THEME["bg_input"],
            activebackground=THEME["bg_card"], activeforeground=THEME["fg_primary"],
        ).pack(side="left")
        tk.Entry(
            fb_f, textvariable=self.var_sub_x, width=4, font=("Consolas", 8),
            bg=THEME["bg_input"], fg=THEME["fg_primary"], insertbackground=THEME["fg_primary"], relief="flat",
        ).pack(side="left", padx=2)
        tk.Entry(
            fb_f, textvariable=self.var_sub_y, width=4, font=("Consolas", 8),
            bg=THEME["bg_input"], fg=THEME["fg_primary"], insertbackground=THEME["fg_primary"], relief="flat",
        ).pack(side="left", padx=(0, 3))
        b_bm_sub = tk.Button(fb_f, text="🎯", font=("Segoe UI", 8), bg=THEME["bg_button"], fg=THEME["fg_primary"], relief="flat", padx=4, cursor="hand2", command=lambda: self._start_hold_still_picker("bm_subscribe"))
        b_bm_sub.pack(side="left")
        self._pick_buttons.append(b_bm_sub)

        # ---- CARD 4: OPTIONAL WEBULL DESKTOP (HARD KILL SWITCH) ----
        c_wb = make_card("OPTIONAL WEBULL DESKTOP (OFF BY DEFAULT)")
        tk.Checkbutton(
            c_wb,
            text="Enable Webull Integration (Unchecked = 100% disabled, zero Webull scans)",
            variable=self.var_wb_enabled,
            font=("Segoe UI", 8, "bold"),
            fg=THEME["fg_primary"], bg=THEME["bg_card"], selectcolor=THEME["bg_input"],
            activebackground=THEME["bg_card"], activeforeground=THEME["fg_primary"],
            command=self._on_webull_master_toggle,
        ).grid(row=1, column=0, columnspan=5, sticky="w", pady=(0, 2))

        wb_opts = tk.Frame(c_wb, bg=THEME["bg_card"])
        wb_opts.grid(row=2, column=0, columnspan=5, sticky="w")
        cb_wb_dd = tk.Checkbutton(
            wb_opts, text="Include Webull in Full Deep-Dive", variable=self.var_wb_in_dd,
            font=("Segoe UI", 8), fg=THEME["fg_primary"], bg=THEME["bg_card"], selectcolor=THEME["bg_input"],
            activebackground=THEME["bg_card"], activeforeground=THEME["fg_primary"],
        )
        cb_wb_dd.pack(side="left", padx=(0, 10))
        cb_wb_hv = tk.Checkbutton(
            wb_opts, text="Allow Hovering Webull Charts", variable=self.var_wb_hover,
            font=("Segoe UI", 8), fg=THEME["fg_primary"], bg=THEME["bg_card"], selectcolor=THEME["bg_input"],
            activebackground=THEME["bg_card"], activeforeground=THEME["fg_primary"],
        )
        cb_wb_hv.pack(side="left")
        self._webull_child_widgets.extend([cb_wb_dd, cb_wb_hv])

        make_lbl(c_wb, "Webull Only Hotkey:", 3, 0)
        wb_hk_var = tk.StringVar(value=str(self.config.get("webull_sync_hotkey", "")))
        self._hotkey_vars["webull_sync_hotkey"] = wb_hk_var
        e_wb_hk = make_ent(c_wb, wb_hk_var, 14, 3, 1)
        b_wb_bind = tk.Button(
            c_wb, text="⌨ Bind", font=("Segoe UI", 8), bg=THEME["bg_button"], fg=THEME["fg_primary"],
            relief="flat", padx=6, cursor="hand2",
            command=lambda: self._start_hotkey_bind("webull_sync_hotkey", HOTKEY_ID_WEBULL_ONLY),
        )
        b_wb_bind.grid(row=3, column=2, padx=(0, 4), pady=2)
        b_wb_clr = tk.Button(
            c_wb, text="✕", font=("Segoe UI", 8), bg=THEME["bg_button"], fg=THEME["fg_secondary"],
            relief="flat", padx=5, cursor="hand2",
            command=lambda: self._clear_hotkey_slot("webull_sync_hotkey"),
        )
        b_wb_clr.grid(row=3, column=3, padx=(0, 6), pady=2)
        wb_st_var = tk.StringVar(value="")
        wb_st_lbl = tk.Label(c_wb, textvariable=wb_st_var, font=("Consolas", 8), fg=THEME["fg_secondary"], bg=THEME["bg_card"], anchor="w", width=18)
        wb_st_lbl.grid(row=3, column=4, sticky="w", pady=2)
        self._hotkey_status_vars["webull_sync_hotkey"] = wb_st_var
        self._hotkey_status_lbls["webull_sync_hotkey"] = wb_st_lbl

        make_lbl(c_wb, "Webull Symbol Box (X, Y):", 4, 0)
        wb_cf = tk.Frame(c_wb, bg=THEME["bg_card"])
        wb_cf.grid(row=4, column=1, columnspan=4, sticky="w")
        e_wb_x = make_ent(wb_cf, self.var_wb_x, 5, 0, 0)
        e_wb_y = make_ent(wb_cf, self.var_wb_y, 5, 0, 1)
        b_pick_wb = tk.Button(
            wb_cf, text="🎯 Pick (Hold 1.2s)", font=("Segoe UI", 8), bg=THEME["bg_button"], fg=THEME["fg_primary"],
            relief="flat", padx=6, cursor="hand2", command=lambda: self._start_hold_still_picker("webull"),
        )
        b_pick_wb.grid(row=0, column=2, padx=(4, 6))
        b_test_wb = tk.Button(
            wb_cf, text="⚡ Send Staged", font=("Segoe UI", 8), bg=THEME["bg_button"], fg=THEME["fg_primary"],
            relief="flat", padx=6, cursor="hand2", command=lambda: self._enqueue_action("webull_only", "Webull Button"),
        )
        b_test_wb.grid(row=0, column=3)
        self._pick_buttons.append(b_pick_wb)
        self._webull_child_widgets.extend([e_wb_hk, b_wb_bind, b_wb_clr, e_wb_x, e_wb_y, b_pick_wb, b_test_wb])

        # ---- FOOTER ----
        foot = tk.Frame(outer, bg=THEME["bg_main"])
        foot.pack(fill="x", pady=(2, 0))
        tk.Checkbutton(
            foot, text="Always on Top", variable=self.var_always_top, font=("Segoe UI", 8),
            fg=THEME["fg_primary"], bg=THEME["bg_main"], selectcolor=THEME["bg_input"],
            activebackground=THEME["bg_main"], activeforeground=THEME["fg_primary"],
        ).pack(side="left")

        tk.Button(
            foot, text="💾 Save Settings", font=("Segoe UI", 8, "bold"),
            bg=THEME["bg_accent"], fg=THEME["fg_primary"],
            activebackground=THEME["bg_accent_hover"], activeforeground=THEME["fg_primary"],
            relief="flat", padx=10, pady=3, cursor="hand2", command=self._save_settings_from_ui,
        ).pack(side="right")

        tk.Button(
            foot, text="🔍 Copy Diagnostics", font=("Segoe UI", 8),
            bg=THEME["bg_button"], fg=THEME["fg_primary"],
            activebackground=THEME["bg_button_hover"], activeforeground=THEME["fg_primary"],
            relief="flat", padx=8, pady=3, cursor="hand2", command=self._copy_diagnostics_to_clipboard,
        ).pack(side="right", padx=(0, 8))

        self._sync_webull_widget_states()
        self._refresh_hotkey_badges()

        # Position safely inside monitor work area (crucial when main bar sits near monitor border!)
        win.update_idletasks()
        sw, sh = int(win.winfo_reqwidth()), int(win.winfo_reqheight())
        rx, ry = int(self.root.winfo_x()), int(self.root.winfo_y())
        rw, rh = int(self.root.winfo_width()), int(self.root.winfo_height())
        wl, wt, wr, wb = get_monitor_work_area_for_hwnd(self._top_hwnd, rx + rw // 2, ry + rh // 2)
        px = min(max(wl + 8, rx), max(wl + 8, wr - sw - 8))
        py = ry + rh + 6
        if py + sh > wb - 8:
            py = max(wt + 8, ry - sh - 6)
        win.geometry(f"+{px}+{py}")
        self._settings_hwnd = resolve_tk_toplevel_hwnd(int(win.winfo_id()))
        apply_dark_titlebar(self._settings_hwnd)

    def _init_settings_vars(self) -> None:
        self.var_always_top = tk.BooleanVar(value=bool(self.config.get("always_on_top", True)))
        self.var_promote_mode_lbl = tk.StringVar(
            value=_PROMOTE_MODES.get(self.config.get("promote_tos_mode", "main_only"), "Main TOS Only")
        )
        self.var_clip_mode_lbl = tk.StringVar(
            value=_CLIP_MODES.get(self.config.get("clipboard_after_deep_dive", "keep_ticker"), "Keep Ticker")
        )
        self.var_tos_x = tk.StringVar(value=str(self.config.get("tos_symbol_rel_x", 0)))
        self.var_tos_y = tk.StringVar(value=str(self.config.get("tos_symbol_rel_y", 0)))
        saved_tos_t = (self.config.get("tos_window_title") or "").strip()
        self.var_tos_win_lbl = tk.StringVar(value=saved_tos_t or "Not picked")

        self.var_bm_focus_ms = tk.StringVar(value=str(self.config.get("bookmap_focus_delay_ms", 220)))
        self.var_bm_dialog_ms = tk.StringVar(value=str(self.config.get("bookmap_dialog_delay_ms", 280)))
        self.var_bm_title = tk.StringVar(value=str(self.config.get("bookmap_window_title", "Bookmap")))
        self.var_ti_guard_ms = tk.StringVar(value=str(self.config.get("ti_settle_guard_ms", 400)))
        self.var_bm_switch_tab = tk.BooleanVar(value=bool(self.config.get("bookmap_switch_existing_tab", True)))
        self.var_use_sym_click = tk.BooleanVar(value=bool(self.config.get("bookmap_use_symbol_click", False)))
        self.var_sym_x = tk.StringVar(value=str(self.config.get("bookmap_symbol_rel_x", 0)))
        self.var_sym_y = tk.StringVar(value=str(self.config.get("bookmap_symbol_rel_y", 0)))
        self.var_use_sub_click = tk.BooleanVar(value=bool(self.config.get("bookmap_use_subscribe_click", False)))
        self.var_sub_x = tk.StringVar(value=str(self.config.get("bookmap_subscribe_rel_x", 0)))
        self.var_sub_y = tk.StringVar(value=str(self.config.get("bookmap_subscribe_rel_y", 0)))

        self.var_browser_tab_ms = tk.StringVar(value=str(self.config.get("browser_tab_delay_ms", 120)))
        self.var_browser_url_ms = tk.StringVar(value=str(self.config.get("browser_url_delay_ms", 100)))
        self.var_dt_title_hint = tk.StringVar(value=str(self.config.get("dt_tab_title_hint", "dilution")))
        self.var_dt_skip_same = tk.BooleanVar(value=bool(self.config.get("dt_skip_if_same_ticker", True)))
        self.var_dt_open_new = tk.BooleanVar(value=bool(self.config.get("dt_open_new_tab_if_missing", False)))

        self.var_wb_enabled = tk.BooleanVar(value=bool(self.config.get("webull_enabled", False)))
        self.var_wb_in_dd = tk.BooleanVar(value=bool(self.config.get("webull_include_in_deep_dive", True)))
        self.var_wb_hover = tk.BooleanVar(value=bool(self.config.get("webull_allow_hover_source", True)))
        self.var_wb_x = tk.StringVar(value=str(self.config.get("webull_symbol_rel_x", 0)))
        self.var_wb_y = tk.StringVar(value=str(self.config.get("webull_symbol_rel_y", 0)))

    def _on_settings_escape(self) -> None:
        if self._bind_target_key is not None:
            return
        self._close_settings_window()

    def _on_webull_master_toggle(self) -> None:
        enabled = bool(self.var_wb_enabled.get())
        self.config["webull_enabled"] = enabled
        self._sync_webull_widget_states()
        self.save_config()
        self._register_all_hotkeys()
        self.set_status("Webull enabled" if enabled else "Webull completely disabled", THEME["fg_success" if enabled else "fg_secondary"])

    def _sync_webull_widget_states(self) -> None:
        en = bool(self.config.get("webull_enabled", False))
        st = "normal" if en else "disabled"
        for w in self._webull_child_widgets:
            try:
                w.configure(state=st)
            except Exception:
                pass

    def _save_settings_from_ui(self) -> None:
        try:
            self._cancel_bind_mode(resume_hotkeys=True)
            for cfg_k, svar in self._hotkey_vars.items():
                val = svar.get().strip()
                if val:
                    err_r = explain_hotkey_rejection(val)
                    if err_r:
                        raise ValueError(err_r)
                self.config[cfg_k] = val

            new_tos_x = int(self.var_tos_x.get() or 0)
            new_tos_y = int(self.var_tos_y.get() or 0)
            self.config["tos_symbol_rel_x"] = new_tos_x
            self.config["tos_symbol_rel_y"] = new_tos_y
            if new_tos_x <= 0 and new_tos_y <= 0:
                self.config["tos_window_title"] = ""
                self.config["tos_window_rect"] = None
                self.config["tos_window_maximized"] = None
                self.config["tos_window_dpi"] = 0
                self._calibrated_tos_hwnd = 0
                self.var_tos_win_lbl.set("Not picked")

            self.config["promote_tos_mode"] = _PROMOTE_LABELS_REV.get(self.var_promote_mode_lbl.get(), "main_only")
            self.config["clipboard_after_deep_dive"] = _CLIP_LABELS_REV.get(self.var_clip_mode_lbl.get(), "keep_ticker")
            self.config["pinned_tab_index"] = _safe_int(self.tab_index_var.get(), 6, 1, 8)
            self.config["bookmap_focus_delay_ms"] = _safe_int(self.var_bm_focus_ms.get(), 220, 50, 5000)
            self.config["bookmap_dialog_delay_ms"] = _safe_int(self.var_bm_dialog_ms.get(), 280, 50, 5000)
            self.config["bookmap_window_title"] = self.var_bm_title.get().strip() or "Bookmap"
            self.config["ti_settle_guard_ms"] = _safe_int(self.var_ti_guard_ms.get(), 400, 0, 5000)
            self.config["bookmap_switch_existing_tab"] = bool(self.var_bm_switch_tab.get())
            self.config["bookmap_use_symbol_click"] = bool(self.var_use_sym_click.get())
            self.config["bookmap_symbol_rel_x"] = int(self.var_sym_x.get() or 0)
            self.config["bookmap_symbol_rel_y"] = int(self.var_sym_y.get() or 0)
            self.config["bookmap_use_subscribe_click"] = bool(self.var_use_sub_click.get())
            self.config["bookmap_subscribe_rel_x"] = int(self.var_sub_x.get() or 0)
            self.config["bookmap_subscribe_rel_y"] = int(self.var_sub_y.get() or 0)
            self.config["browser_tab_delay_ms"] = _safe_int(self.var_browser_tab_ms.get(), 120, 20, 5000)
            self.config["browser_url_delay_ms"] = _safe_int(self.var_browser_url_ms.get(), 100, 20, 5000)
            self.config["dt_tab_title_hint"] = self.var_dt_title_hint.get().strip()
            self.config["dt_skip_if_same_ticker"] = bool(self.var_dt_skip_same.get())
            self.config["dt_open_new_tab_if_missing"] = bool(self.var_dt_open_new.get())

            self.config["webull_enabled"] = bool(self.var_wb_enabled.get())
            self.config["webull_include_in_deep_dive"] = bool(self.var_wb_in_dd.get())
            self.config["webull_allow_hover_source"] = bool(self.var_wb_hover.get())
            self.config["webull_symbol_rel_x"] = int(self.var_wb_x.get() or 0)
            self.config["webull_symbol_rel_y"] = int(self.var_wb_y.get() or 0)

            self.config["always_on_top"] = bool(self.var_always_top.get())
            self.root.attributes("-topmost", self.config["always_on_top"])
            self.save_config()
            self._register_all_hotkeys()
            self.launch_btn.configure(text=self._launch_button_text())
            self._sync_webull_widget_states()
            self.set_status("Settings saved ✓", THEME["fg_success"])
        except Exception as e:
            self.set_status(f"Invalid setting: {e}", THEME["fg_error"])

    # --------------------------------------------------------------------------
    # Interactive Bind Mode & Hold-Still 1.2s Picker
    # --------------------------------------------------------------------------
    def _clear_hotkey_slot(self, cfg_key: str) -> None:
        self._cancel_bind_mode(resume_hotkeys=True)
        if cfg_key in self._hotkey_vars:
            self._hotkey_vars[cfg_key].set("")
        self.config[cfg_key] = ""
        self.save_config()
        self._register_all_hotkeys()
        if cfg_key == "hotkey":
            self.launch_btn.configure(text=self._launch_button_text())
        self.set_status("Cleared shortcut slot", THEME["fg_secondary"])

    def _start_hotkey_bind(self, cfg_key: str, _hk_id: int) -> None:
        if self._coach_active:
            return
        self._cancel_bind_mode(resume_hotkeys=False)
        self.hotkeys.pause_all()
        self._bind_target_key = cfg_key
        if cfg_key in self._hotkey_status_vars:
            self._hotkey_status_vars[cfg_key].set("👉 Press key...")
            self._hotkey_status_lbls[cfg_key].configure(fg=THEME["fg_warning"])
        self.set_status("Press F13–F24 or modifier combo (Esc cancels)...", THEME["fg_warning"])
        target_w = self._settings_win if (self._settings_win and self._settings_win.winfo_exists()) else self.root
        focus_window_no_alt(resolve_tk_toplevel_hwnd(int(target_w.winfo_id())), max_attempts=4)
        target_w.focus_force()
        self._bind_after_id = self.root.after(8000, lambda: self._on_bind_timeout(cfg_key))

    def _on_bind_timeout(self, cfg_key: str) -> None:
        if self._bind_target_key != cfg_key:
            return
        self._cancel_bind_mode(resume_hotkeys=True)
        self.set_status("Bind timed out — press F13–F24 or Alt+<Key>", THEME["fg_warning"])

    def _cancel_bind_mode(self, resume_hotkeys: bool = True) -> None:
        was_binding = self._bind_target_key is not None
        self._bind_target_key = None
        if self._bind_after_id is not None:
            try:
                self.root.after_cancel(self._bind_after_id)
            except Exception:
                pass
            self._bind_after_id = None
        if was_binding and resume_hotkeys:
            self.hotkeys.resume_all()
        self._refresh_hotkey_badges()

    def _set_pick_buttons_state(self, active: bool) -> None:
        st = "disabled" if active else "normal"
        for b in list(self._pick_buttons):
            try:
                if b.winfo_exists():
                    b.configure(state=st)
            except Exception:
                pass

    def _start_hold_still_picker(self, pick_kind: str) -> None:
        """
        Zero-rush Hold-Still (1.2s) coordinate picker for:
          - `'tos'` (Main@thinkorswim Symbol Box)
          - `'bm_symbol'` (Bookmap Symbol Box)
          - `'bm_subscribe'` (Bookmap Subscribe Button)
          - `'webull'` (Webull Desktop Symbol Box)
        """
        if self._coach_active:
            return
        self._cancel_bind_mode(resume_hotkeys=True)
        self._coach_active = True
        self._coach_pid_cache.clear()
        self._set_pick_buttons_state(True)

        desc_map = {
            "tos": ("Main TOS", "Symbol Input Box", "tos"),
            "bm_symbol": ("Bookmap", "Symbol Input Box", "bookmap"),
            "bm_subscribe": ("Bookmap", "Subscribe Button", "bookmap"),
            "webull": ("Webull", "Symbol Input Box", "webull"),
        }
        label, what, expected_kind = desc_map[pick_kind]
        own_hw = {self._top_hwnd}
        if self._settings_hwnd:
            own_hw.add(self._settings_hwnd)

        esc_ok = self.hotkeys.register_temp_escape(
            lambda: self.root.after(0, lambda: self._cancel_hold_still_picker("Pick canceled (Esc)"))
        )
        esc_hint = "Esc to cancel" if esc_ok else "Move away to timeout"
        t_start = time.perf_counter()
        anchor_x = -99999
        anchor_y = -99999
        anchor_hwnd = 0
        steady_since: float | None = None

        self.set_status(f"Point at {label} {what} & hold 1.2s ({esc_hint})", THEME["fg_warning"])

        def _poll():
            nonlocal anchor_x, anchor_y, anchor_hwnd, steady_since
            if not self._coach_active or self._closing:
                return
            now = time.perf_counter()
            if (now - t_start) > COACH_TIMEOUT_SEC:
                self._cancel_hold_still_picker("Pick timed out — click 🎯 to try again")
                return

            with per_monitor_dpi_context():
                cx, cy, root_hwnd, cur_kind, cur_title, cur_exe = probe_cursor_target_fast(
                    self._coach_pid_cache, include_webull=(expected_kind == "webull")
                )

            if root_hwnd in own_hw:
                steady_since = None
                anchor_hwnd = 0
                self.coach_overlay.update_at_cursor(
                    cx, cy,
                    f"👉 Move mouse onto {label}'s {what} (DON'T click!)",
                    f"Just hover over the box — {esc_hint}", THEME["fg_warning"],
                )
            elif cur_kind != expected_kind:
                steady_since = None
                anchor_hwnd = 0
                over_d = cur_kind.upper() if cur_kind else (cur_exe or "Desktop")
                rem_t = int(COACH_TIMEOUT_SEC - (now - t_start))
                self.coach_overlay.update_at_cursor(
                    cx, cy,
                    f"👉 Hover over {label}'s {what} (DON'T click!)",
                    f"Currently over: {over_d} • {rem_t}s left • {esc_hint}", THEME["fg_warning"],
                )
            else:
                if root_hwnd != anchor_hwnd or math.hypot(cx - anchor_x, cy - anchor_y) > COACH_ANCHOR_RADIUS_PX or steady_since is None:
                    anchor_x, anchor_y, anchor_hwnd, steady_since = cx, cy, root_hwnd, now
                rem = max(0.0, COACH_HOLD_SEC - (now - steady_since))
                note = " (pick MAIN chart, not grid!)" if (expected_kind == "tos" and len(extract_tos_title_tickers(cur_title)) > 1) else ""
                if rem <= 0.0:
                    self._finish_hold_still_pick(pick_kind, cx, cy, anchor_hwnd, cur_title)
                    return
                self.coach_overlay.update_at_cursor(
                    cx, cy,
                    f"🎯 Hold still on {label} {what}... {rem:.1f}s",
                    f"Over: {cur_title[:28] or label}{note} ✓ • {esc_hint}", THEME["fg_success"],
                )

            self._coach_after_id = self.root.after(50, _poll)

        self._coach_after_id = self.root.after(50, _poll)

    def _cleanup_coach(self) -> None:
        self._coach_active = False
        if self._coach_after_id is not None:
            try:
                self.root.after_cancel(self._coach_after_id)
            except Exception:
                pass
            self._coach_after_id = None
        try:
            self.hotkeys.unregister_temp_escape()
        except Exception:
            pass
        self.coach_overlay.hide()
        self._set_pick_buttons_state(False)

    def _cancel_hold_still_picker(self, reason: str) -> None:
        if not self._coach_active:
            return
        self._cleanup_coach()
        self.set_status(reason, THEME["fg_warning"])

    def _finish_hold_still_pick(self, pick_kind: str, pt_x: int, pt_y: int, hwnd: int, raw_title: str) -> None:
        self._cleanup_coach()
        with per_monitor_dpi_context():
            rc = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rc))
            w, h = int(rc.right - rc.left), int(rc.bottom - rc.top)
            rel_x, rel_y = int(pt_x - rc.left), int(pt_y - rc.top)
            zoomed = bool(user32.IsZoomed(hwnd))
            dpi = get_window_dpi(hwnd)

        if rel_x <= 4 or rel_y <= 4 or rel_x >= w - 4 or rel_y >= h - 4:
            self.set_status("Cursor outside window interior — retry!", THEME["fg_error"])
            return

        flash_highlight_box(pt_x, pt_y, border_rgb=(63, 185, 80), duration_ms=700)
        rect_list = [int(rc.left), int(rc.top), w, h]

        if pick_kind == "tos":
            norm_t = normalize_tos_title(raw_title) or "thinkorswim"
            self._calibrated_tos_hwnd = hwnd
            self.config["tos_symbol_rel_x"] = rel_x
            self.config["tos_symbol_rel_y"] = rel_y
            self.config["tos_window_title"] = norm_t
            self.config["tos_window_rect"] = rect_list
            self.config["tos_window_maximized"] = zoomed
            self.config["tos_window_dpi"] = dpi
            if hasattr(self, "var_tos_x"):
                self.var_tos_x.set(str(rel_x))
                self.var_tos_y.set(str(rel_y))
                self.var_tos_win_lbl.set(norm_t)
            self.set_status(f"Main TOS locked ({rel_x},{rel_y}) ✓", THEME["fg_success"])
        elif pick_kind == "bm_symbol":
            self.config["bookmap_symbol_rel_x"] = rel_x
            self.config["bookmap_symbol_rel_y"] = rel_y
            self.config["bookmap_use_symbol_click"] = True
            if hasattr(self, "var_sym_x"):
                self.var_sym_x.set(str(rel_x))
                self.var_sym_y.set(str(rel_y))
                self.var_use_sym_click.set(True)
            self.set_status(f"BM Symbol Box ({rel_x},{rel_y}) ✓", THEME["fg_success"])
        elif pick_kind == "bm_subscribe":
            self.config["bookmap_subscribe_rel_x"] = rel_x
            self.config["bookmap_subscribe_rel_y"] = rel_y
            self.config["bookmap_use_subscribe_click"] = True
            if hasattr(self, "var_sub_x"):
                self.var_sub_x.set(str(rel_x))
                self.var_sub_y.set(str(rel_y))
                self.var_use_sub_click.set(True)
            self.set_status(f"BM Subscribe Btn ({rel_x},{rel_y}) ✓", THEME["fg_success"])
        elif pick_kind == "webull":
            from deepdive_engine import normalize_window_title
            norm_w = normalize_window_title(raw_title, "webull") or "Webull Desktop"
            self.config["webull_enabled"] = True
            self.config["webull_symbol_rel_x"] = rel_x
            self.config["webull_symbol_rel_y"] = rel_y
            self.config["webull_window_title"] = norm_w
            self.config["webull_window_rect"] = rect_list
            self.config["webull_window_maximized"] = zoomed
            self.config["webull_window_dpi"] = dpi
            if hasattr(self, "var_wb_x"):
                self.var_wb_enabled.set(True)
                self.var_wb_x.set(str(rel_x))
                self.var_wb_y.set(str(rel_y))
                self._sync_webull_widget_states()
            self.set_status(f"Webull Box ({rel_x},{rel_y}) ✓", THEME["fg_success"])

        self.save_config()

    def _copy_diagnostics_to_clipboard(self) -> None:
        report = build_ai_diagnostics_report(
            APP_VERSION, self.config, self.dpi_mode, self.sidecar,
            self.hotkey_statuses, self.grid_tracker, self._bm_subscribed_tabs, ERROR_LOG_PATH,
        )
        ok, _ = write_clipboard_unicode(report, exclude_from_history=True)
        if ok:
            self.set_status("Copied AI Diagnostics to clipboard ✓", THEME["fg_success"])
        else:
            self.set_status("Could not open clipboard for diagnostics", THEME["fg_error"])

    # --------------------------------------------------------------------------
    # Win32 "Edit" Targetable by Trade-Ideas + Thread-Safe Entry Poll
    # --------------------------------------------------------------------------
    def _create_win32_entry(self) -> None:
        self.root.update_idletasks()
        self.root.update()
        self._top_hwnd = user32.GetAncestor(self.root.winfo_id(), GA_ROOT) or self.root.winfo_id()
        apply_dark_titlebar(self._top_hwnd)

        parent_hwnd = self.entry_container.winfo_id()
        width = max(80, self.entry_container.winfo_width())
        height = max(24, self.entry_container.winfo_height())
        # ES_MULTILINE | ES_WANTRETURN | ES_AUTOVSCROLL keeps Trade-Ideas '\r\n' silent (no MessageBeep!)
        style = (
            WS_CHILD | WS_VISIBLE | WS_TABSTOP | ES_LEFT
            | ES_MULTILINE | ES_WANTRETURN | ES_AUTOVSCROLL | ES_AUTOHSCROLL | ES_UPPERCASE
        )
        self.edit_hwnd = user32.CreateWindowExW(
            0, "Edit", "", style, 2, 2, width - 4, height - 4, parent_hwnd, None, None, None
        )
        self._edit_font = gdi32.CreateFontW(-15, 0, 0, 0, 700, 0, 0, 0, 0, 0, 0, 4, 0, "Consolas")
        if self._edit_font:
            user32.SendMessageW(self.edit_hwnd, WM_SETFONT, self._edit_font, 1)

        def on_resize(ev):
            if self.edit_hwnd:
                user32.MoveWindow(self.edit_hwnd, 2, 2, max(40, ev.width - 4), max(18, ev.height - 4), True)

        self.entry_container.bind("<Configure>", on_resize)

    def _is_edit_all_selected(self, raw_len: int) -> bool:
        if not self.edit_hwnd or raw_len <= 0:
            return True
        sel = int(user32.SendMessageW(self.edit_hwnd, EM_GETSEL, 0, 0))
        return (sel & 0xFFFF) == 0 and ((sel >> 16) & 0xFFFF) >= raw_len

    def focus_manual_entry(self, select_all: bool = True) -> None:
        if not self.edit_hwnd:
            return
        self._manual_edit_active = True
        self._unselected_idle_prefix = ""
        if self._top_hwnd and user32.GetForegroundWindow() != self._top_hwnd:
            user32.SetForegroundWindow(self._top_hwnd)
        user32.SetFocus(self.edit_hwnd)
        if select_all:
            user32.SendMessageW(self.edit_hwnd, EM_SETSEL, 0, -1)

        def _ensure():
            if self.edit_hwnd and self._manual_edit_active and user32.GetFocus() != self.edit_hwnd:
                user32.SetFocus(self.edit_hwnd)

        self.root.after(20, _ensure)
        self.root.after(65, _ensure)

    def _on_root_keypress(self, event: tk.Event) -> str | None:
        if self._bind_target_key is not None:
            keycode = int(getattr(event, "keycode", 0) or 0)
            state = int(getattr(event, "state", 0) or 0)
            if keycode == 0x1B:
                self._cancel_bind_mode(resume_hotkeys=True)
                self.set_status("Bind canceled", THEME["fg_secondary"])
                return "break"
            spec, reject_reason = tk_keypress_to_hotkey_spec(keycode, state)
            if not spec and not reject_reason:
                return None
            if reject_reason:
                self.set_status(reject_reason, THEME["fg_error"])
                return "break"
            cfg_key = self._bind_target_key
            if self._bind_after_id is not None:
                try:
                    self.root.after_cancel(self._bind_after_id)
                except Exception:
                    pass
                self._bind_after_id = None
            self._bind_target_key = None
            if cfg_key in self._hotkey_vars:
                self._hotkey_vars[cfg_key].set(spec)
            self.config[cfg_key] = spec
            if cfg_key == "webull_sync_hotkey" and not self.config.get("webull_enabled"):
                self.config["webull_enabled"] = True
                if hasattr(self, "var_wb_enabled"):
                    self.var_wb_enabled.set(True)
                    self._sync_webull_widget_states()
            self.save_config()
            self._register_all_hotkeys()
            if cfg_key == "hotkey":
                self.launch_btn.configure(text=self._launch_button_text())
            altgr = " (non-US layout: F13–F24 avoids AltGr)" if ("<ctrl>" in spec and "<alt>" in spec and is_non_us_altgr_layout()) else ""
            disp = format_hotkey_display(spec)
            self.set_status(f"Bound {disp} ✓{altgr}", THEME["fg_success"])
            return "break"

        if event.widget.winfo_toplevel() is not self.root:
            return None

        focused_widget = self.root.focus_get()
        if isinstance(focused_widget, (tk.Entry, tk.Spinbox)):
            return None
        if (event.state & 0x0004) and not (event.state & 0x20000) and (event.keysym or "").lower() == "v":
            ok, clip_txt, _, _ = read_clipboard_unicode(max_bytes=256, retries=3)
            if ok and clip_txt:
                pasted = parse_tos_ticker(clip_txt) or clean_ti_or_manual_ticker(clip_txt)
                if pasted:
                    self._stage_ticker_atomic(pasted, f"Pasted {pasted} • Ready")
            return None
        if event.state & 0x0004 or event.state & 0x20000:
            return None

        ch = event.char or ""
        if ch and re.match(r"^[A-Za-z0-9.\-]$", ch):
            up_ch = ch.upper()
            new_text = up_ch if not self._manual_edit_active else (clean_ti_or_manual_ticker(self.get_entry_text()) + up_ch)[:12]
            self._manual_edit_active = True
            set_window_text(self.edit_hwnd, new_text)
            self.focus_manual_entry(select_all=False)
            n = len(new_text)
            user32.SendMessageW(self.edit_hwnd, EM_SETSEL, n, n)
            self._sync_entry_to_staged_ticker()
        elif event.keysym == "BackSpace":
            self._manual_edit_active = True
            new_text = clean_ti_or_manual_ticker(self.get_entry_text())[:-1]
            set_window_text(self.edit_hwnd, new_text)
            self.focus_manual_entry(select_all=False)
            n = len(new_text)
            user32.SendMessageW(self.edit_hwnd, EM_SETSEL, n, n)
            self._sync_entry_to_staged_ticker()
        return None

    def _check_manual_edit_click(self) -> None:
        if not self.edit_hwnd:
            return
        fg_hwnd = int(user32.GetForegroundWindow() or 0)
        is_our_fg = bool(self._top_hwnd and fg_hwnd == self._top_hwnd)
        if self._was_our_window_fg and not is_our_fg:
            self._manual_edit_active = False
            self._unselected_idle_prefix = ""
            user32.SendMessageW(self.edit_hwnd, EM_SETSEL, 0, -1)
        self._was_our_window_fg = is_our_fg

        mouse_down = (user32.GetAsyncKeyState(get_primary_mouse_vk()) & 0x8000) != 0
        just_clicked = mouse_down and not self._prev_mouse_down
        self._prev_mouse_down = mouse_down
        if not just_clicked:
            return

        pt = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(pt)):
            return
        hit_hwnd = user32.WindowFromPoint(pt)
        hit_root = int(user32.GetAncestor(hit_hwnd, GA_ROOT) or 0) if hit_hwnd else 0
        if hit_root and hit_root != self._top_hwnd and is_tos_frame_hwnd(hit_root):
            self._last_tos_clicked_hwnd = hit_root
            self._last_tos_click_ts = time.perf_counter()

        er = wintypes.RECT()
        user32.GetWindowRect(self.edit_hwnd, ctypes.byref(er))
        in_edit = (er.left - 4 <= pt.x <= er.right + 4) and (er.top - 4 <= pt.y <= er.bottom + 4)
        if in_edit:
            if hit_root == self._top_hwnd or hit_hwnd == self.edit_hwnd:
                already = self._manual_edit_active and user32.GetFocus() == self.edit_hwnd
                self.focus_manual_entry(select_all=not already)
        elif self._manual_edit_active:
            self._manual_edit_active = False
            self._unselected_idle_prefix = ""
            user32.SendMessageW(self.edit_hwnd, EM_SETSEL, 0, -1)

    def get_entry_text(self) -> str:
        return get_window_text(self.edit_hwnd) if self.edit_hwnd else ""

    def set_entry_text(self, text: str) -> None:
        if self.edit_hwnd:
            set_window_text(self.edit_hwnd, text)
            self._sync_entry_to_staged_ticker()

    def _sync_entry_to_staged_ticker(self) -> str:
        """Runs strictly on the Tk main thread — never concurrently on worker threads."""
        now = time.perf_counter()
        raw = self.get_entry_text()
        has_nl = "\r" in raw or "\n" in raw

        if raw != self._last_raw_entry_text:
            prev_stripped = self._last_raw_entry_text.strip()
            self._last_raw_entry_text = raw
            self._last_entry_change_ts = now
            if not self._manual_edit_active or has_nl:
                self._last_ti_burst_ts = now
            self._entry_burst_armed_for_overwrite = False

            cleaned = clean_ti_or_manual_ticker(raw)
            if (
                has_nl
                and self._unselected_idle_prefix
                and cleaned != clean_ti_or_manual_ticker(prev_stripped)
                and cleaned.startswith(self._unselected_idle_prefix)
                and len(cleaned) > len(self._unselected_idle_prefix)
            ):
                cleaned = cleaned[len(self._unselected_idle_prefix) :]
            if cleaned:
                self.staged_ticker = cleaned
                self._update_staged_ui(cleaned)
            elif not raw.strip():
                self.staged_ticker = ""
                self._unselected_idle_prefix = ""
                self._update_staged_ui("")
        else:
            if raw and (now - self._last_entry_change_ts) >= 0.30:
                if self._is_edit_all_selected(len(raw)):
                    self._unselected_idle_prefix = ""
                elif not self._manual_edit_active:
                    user32.SendMessageW(self.edit_hwnd, EM_SETSEL, 0, -1)
                    self._unselected_idle_prefix = ""
                else:
                    self._unselected_idle_prefix = self.staged_ticker

        if has_nl:
            self._entry_burst_armed_for_overwrite = True
            self._manual_edit_active = False
            self._unselected_idle_prefix = ""
            if self.edit_hwnd:
                cleaned = self.staged_ticker or clean_ti_or_manual_ticker(raw)
                if cleaned and raw != cleaned:
                    set_window_text(self.edit_hwnd, cleaned)
                    self._last_raw_entry_text = cleaned
                user32.SendMessageW(self.edit_hwnd, EM_SETSEL, 0, -1)

        return self.staged_ticker

    def _update_staged_ui(self, ticker: str) -> None:
        if ticker:
            self.staged_var.set(f"Staged: {ticker}")
            if not self._is_running_sequence:
                self.set_status(f"Staged {ticker} • Ready", THEME["fg_ticker"])
        else:
            self.staged_var.set("Staged: —")

    def _stage_ticker_on_main_thread(self, ticker: str, status_msg: str | None) -> None:
        if not ticker:
            return
        # Update edit_hwnd + _last_raw_entry_text + EM_SETSEL atomically so 25ms poll
        # NEVER treats programmatic hover/grab staging as a Trade-Ideas burst!
        self._manual_edit_active = False
        self._unselected_idle_prefix = ""
        self._entry_burst_armed_for_overwrite = True
        self.staged_ticker = ticker
        if self.edit_hwnd:
            set_window_text(self.edit_hwnd, ticker)
            self._last_raw_entry_text = ticker
            self._last_entry_change_ts = time.perf_counter()
            user32.SendMessageW(self.edit_hwnd, EM_SETSEL, 0, -1)
        self.staged_var.set(f"Staged: {ticker}")
        if status_msg:
            self.set_status(status_msg, THEME["fg_success"])

    def _stage_ticker_atomic(self, ticker: str, status_msg: str | None = None, wait_sync: bool = False) -> None:
        if threading.current_thread() is threading.main_thread():
            self._stage_ticker_on_main_thread(ticker, status_msg)
            return
        evt = threading.Event() if wait_sync else None

        def _run():
            try:
                self._stage_ticker_on_main_thread(ticker, status_msg)
            finally:
                if evt is not None:
                    evt.set()

        self.root.after(0, _run)
        if evt is not None:
            evt.wait(timeout=0.20)

    def _read_staged_and_rearm_on_main_thread(self) -> str:
        """
        Reads & re-arms `edit_hwnd` strictly on the Tk main thread (eliminating cross-thread
        races with the 25ms poll).
        """
        def _do() -> str:
            tkr = self._sync_entry_to_staged_ticker()
            if tkr:
                self._manual_edit_active = False
                self._unselected_idle_prefix = ""
                if self.edit_hwnd:
                    set_window_text(self.edit_hwnd, tkr)
                    self._last_raw_entry_text = tkr
                    user32.SendMessageW(self.edit_hwnd, EM_SETSEL, 0, -1)
            return tkr

        if threading.current_thread() is threading.main_thread():
            return _do()

        box: list[str] = []
        evt = threading.Event()

        def _cb():
            try:
                box.append(_do())
            finally:
                evt.set()

        self.root.after(0, _cb)
        if evt.wait(timeout=0.20) and box:
            return box[0]
        return self.staged_ticker

    def _schedule_entry_poll(self) -> None:
        try:
            self._check_manual_edit_click()
            self._sync_entry_to_staged_ticker()
        except Exception as e:
            log_error("Entry poll error", e)
        if not self._closing:
            self.root.after(25, self._schedule_entry_poll)

    def set_status(self, message: str, color: str = THEME["fg_secondary"]) -> None:
        def _apply():
            if not self._closing:
                self.status_var.set(message)
                self.status_bar.configure(fg=color)

        if threading.current_thread() is threading.main_thread():
            _apply()
        else:
            try:
                self.root.after(0, _apply)
            except Exception:
                pass

    # --------------------------------------------------------------------------
    # Pure-Win32 Hotkey Matrix & Press-Time Hover Snapshot
    # --------------------------------------------------------------------------
    def _capture_press_snapshot(self, cursor_pt: tuple[int, int] | None, _fg_at_press: int) -> tuple[int, str, str, str]:
        if cursor_pt is None:
            return 0, "", "", ""
        hit = user32.WindowFromPoint(wintypes.POINT(int(cursor_pt[0]), int(cursor_pt[1])))
        root_c = int(user32.GetAncestor(hit, GA_ROOT) or 0) if hit else 0
        if not root_c or root_c == self._top_hwnd or root_c == self._settings_hwnd:
            return root_c, "", "", ""
        pid = get_hwnd_pid(root_c)
        from deepdive_engine import get_pid_info_and_elevation
        exe_base, exe_full, _ = get_pid_info_and_elevation(pid)
        cls = get_window_class(root_c)
        title = get_window_text(root_c).strip()
        kind = classify_exe_and_class(exe_base, exe_full, cls, include_webull=bool(self.config.get("webull_enabled", False)))
        cached_sym = ""
        if kind == "tos":
            cached_sym = self.grid_tracker.fast_lookup_at_press(root_c, title, int(cursor_pt[0]), int(cursor_pt[1]))
        return root_c, kind, title, cached_sym

    def _register_all_hotkeys(self) -> None:
        webull_on = bool(self.config.get("webull_enabled", False))
        bindings = [
            (HOTKEY_ID_DEEP_DIVE, self.config.get("hotkey", "<alt>+d"),
             lambda d, ex, fg, cp, sn: self._enqueue_action("deep_dive", f"Hotkey ({d})", extra_vks=ex, fg_at_press=fg, cursor_pt=cp, snap=sn)),
            (HOTKEY_ID_HOVER_DD, self.config.get("hover_deep_dive_hotkey", "<alt>+q"),
             lambda d, ex, fg, cp, sn: self._enqueue_action("hover_deep_dive", f"Hotkey ({d})", extra_vks=ex, fg_at_press=fg, cursor_pt=cp, snap=sn)),
            (HOTKEY_ID_PROMOTE_TOS, self.config.get("promote_tos_hotkey", "<alt>+m"),
             lambda d, ex, fg, cp, sn: self._enqueue_action("promote_tos", f"Hotkey ({d})", extra_vks=ex, fg_at_press=fg, cursor_pt=cp, snap=sn)),
            (HOTKEY_ID_TOS_GRAB, self.config.get("tos_grab_hotkey", "<alt>+g"),
             lambda d, ex, fg, cp, sn: self._enqueue_action("tos_grab", f"Hotkey ({d})", extra_vks=ex, fg_at_press=fg, cursor_pt=cp, snap=sn)),
            (HOTKEY_ID_BM_ONLY, self.config.get("bm_only_hotkey", ""),
             lambda d, ex, fg, cp, sn: self._enqueue_action("bm_only", f"Hotkey ({d})", extra_vks=ex, fg_at_press=fg, cursor_pt=cp, snap=sn)),
            (HOTKEY_ID_DT_ONLY, self.config.get("dt_only_hotkey", ""),
             lambda d, ex, fg, cp, sn: self._enqueue_action("dt_only", f"Hotkey ({d})", extra_vks=ex, fg_at_press=fg, cursor_pt=cp, snap=sn)),
            (HOTKEY_ID_WEBULL_ONLY, (self.config.get("webull_sync_hotkey", "") if webull_on else ""),
             lambda d, ex, fg, cp, sn: self._enqueue_action("webull_only", f"Hotkey ({d})", extra_vks=ex, fg_at_press=fg, cursor_pt=cp, snap=sn)),
        ]
        self.hotkey_statuses = self.hotkeys.apply_bindings(bindings)
        self._refresh_hotkey_badges()

        bad = [msg for hk_id, (ok, msg) in self.hotkey_statuses.items() if not ok and msg not in ("Off", "")]
        if bad:
            self.set_status(bad[0], THEME["fg_warning"])

    def _refresh_hotkey_badges(self) -> None:
        id_map = {
            "hotkey": HOTKEY_ID_DEEP_DIVE,
            "hover_deep_dive_hotkey": HOTKEY_ID_HOVER_DD,
            "promote_tos_hotkey": HOTKEY_ID_PROMOTE_TOS,
            "tos_grab_hotkey": HOTKEY_ID_TOS_GRAB,
            "bm_only_hotkey": HOTKEY_ID_BM_ONLY,
            "dt_only_hotkey": HOTKEY_ID_DT_ONLY,
            "webull_sync_hotkey": HOTKEY_ID_WEBULL_ONLY,
        }
        webull_on = bool(self.config.get("webull_enabled", False))
        for cfg_k, hid in id_map.items():
            if cfg_k not in self._hotkey_status_vars:
                continue
            if cfg_k == "webull_sync_hotkey" and not webull_on:
                self._hotkey_status_vars[cfg_k].set("Disabled")
                self._hotkey_status_lbls[cfg_k].configure(fg=THEME["fg_secondary"])
                continue
            ok, msg = self.hotkey_statuses.get(hid, (False, "Off"))
            self._hotkey_status_vars[cfg_k].set(f"✓ {msg}" if ok else (msg or "Off"))
            col = THEME["fg_success"] if ok else (THEME["fg_secondary"] if msg in ("Off", "") else THEME["fg_error"])
            self._hotkey_status_lbls[cfg_k].configure(fg=col)

    def _schedule_background_grid_warmup(self) -> None:
        if self._closing:
            return

        def _bg():
            try:
                wins = enum_visible_top_windows()
                hud_r = self._own_hud_rect()
                for w in wins:
                    if classify_exe_and_class(w.exe_name, w.exe_path, w.class_name, include_webull=False) == "tos":
                        if not w.is_iconic and len(extract_tos_title_tickers(w.title)) > 1:
                            self.grid_tracker.schedule_warmup_scan(w, self.sidecar, hud_exclude_rect=hud_r)
            except Exception:
                pass

        threading.Thread(target=_bg, daemon=True).start()
        if not self._closing:
            self.root.after(12000, self._schedule_background_grid_warmup)

    def _own_hud_rect(self) -> tuple[int, int, int, int] | None:
        if not self._top_hwnd or not user32.IsWindow(self._top_hwnd):
            return None
        rc = wintypes.RECT()
        if user32.GetWindowRect(self._top_hwnd, ctypes.byref(rc)):
            return (int(rc.left), int(rc.top), int(rc.right), int(rc.bottom))
        return None

    # --------------------------------------------------------------------------
    # Unified 1-Slot Latest-Wins Job Queue & Workers
    # --------------------------------------------------------------------------
    def _enqueue_action(
        self,
        action: str,
        source: str,
        *,
        explicit_ticker: str = "",
        extra_vks: tuple[int, ...] = (),
        fg_at_press: int = 0,
        cursor_pt: tuple[int, int] | None = None,
        snap: tuple[int, str, str, str] | None = None,
    ) -> None:
        if self._coach_active or self._bind_target_key is not None:
            self.set_status("Finish or cancel Pick/Bind first!", THEME["fg_warning"])
            return
        if not fg_at_press:
            fg_at_press = int(user32.GetForegroundWindow() or 0)
        req = {
            "action": action,
            "source": source,
            "explicit_ticker": explicit_ticker,
            "extra_vks": tuple(extra_vks),
            "fg_at_press": fg_at_press,
            "cursor_pt": cursor_pt,
            "snap": snap or (0, "", "", ""),
            "queued_at": time.monotonic(),
        }
        with self._queue_lock:
            if not self._worker_busy:
                self._worker_busy = True
                threading.Thread(target=self._worker_loop, args=(req,), daemon=True).start()
                return
            old = self._pending_job
            if old is not None and (old["action"], old["explicit_ticker"], old["snap"][0], old["snap"][3]) == (
                req["action"], req["explicit_ticker"], req["snap"][0], req["snap"][3]
            ):
                return
            self._pending_job = req
            if old is not None and old["action"] != req["action"]:
                self.set_status(f"⚡ Queued {action} (replaced {old['action']})", THEME["fg_warning"])
            else:
                self.set_status(f"⚡ Busy — queued {action}...", THEME["fg_warning"])

    def _worker_loop(self, initial_req: dict) -> None:
        cur: dict | None = initial_req
        try:
            while cur is not None and not self._closing:
                with self._job_lock:
                    self._is_running_sequence = True
                    try:
                        self._execute_job_locked(cur)
                    finally:
                        self._is_running_sequence = False
                with self._queue_lock:
                    nxt = self._pending_job
                    self._pending_job = None
                    if nxt is not None and not self._closing and (time.monotonic() - float(nxt.get("queued_at") or 0.0)) <= 2.5:
                        cur = nxt
                    else:
                        cur = None
                        self._worker_busy = False
        finally:
            with self._queue_lock:
                self._pending_job = None
                self._worker_busy = False

    def _resolve_hover_chart_ticker(
        self,
        job: dict,
        wins: list[WindowInfo],
        *,
        for_promote_tos: bool = False,
    ) -> tuple[str, str, int, bool]:
        """
        Resolves the ticker under the press-time cursor snapshot `(root_c, kind_c, title_c, cached_sym)`:
          - Never falls back to `staged_ticker` if the cursor is over a non-Main TOS grid or Webull chart!
          - Returns `(ticker, detail_msg, src_hwnd, used_staged_fallback)`.
        """
        snap = job.get("snap") or (0, "", "", "")
        root_c, kind_c, title_c, cached_sym = snap
        cursor_pt = job.get("cursor_pt")
        by_hwnd = {w.hwnd: w for w in wins}
        cal_win, _ = find_target_window(
            "tos", str(self.config.get("tos_window_title") or ""), self.config.get("tos_window_rect"),
            label="Main TOS", all_windows=wins, include_webull=bool(self.config.get("webull_enabled", False)),
        )
        cal_hwnd = cal_win.hwnd if cal_win is not None else 0

        if kind_c == "tos":
            is_grid_or_detached = bool(extract_tos_title_tickers(title_c)) or (cal_hwnd and root_c != cal_hwnd)
            if is_grid_or_detached:
                if cached_sym:
                    return cached_sym, f"{cached_sym} ← TOS Grid ✓", root_c, False
                win_obj = by_hwnd.get(root_c)
                if win_obj is None:
                    return "", "Hovered TOS window closed", root_c, False
                sym, det = self.grid_tracker.resolve_grid_at_cursor(
                    win_obj, cursor_pt, self.sidecar, hud_exclude_rect=self._own_hud_rect()
                )
                return sym, det, root_c, False

            # Cursor is over Main@thinkorswim
            if for_promote_tos:
                staged = self._read_staged_and_rearm_on_main_thread()
                if staged:
                    return staged, f"Staged {staged}", root_c, True
                return "", "No staged ticker to send to Main TOS!", root_c, True

            sym_m, det_m, _ = grab_tos_2pass(
                self.config, root_c, self._last_tos_clicked_hwnd, self._top_hwnd, cal_hwnd
            )
            return sym_m, det_m, root_c, False

        if kind_c == "webull" and bool(self.config.get("webull_enabled", False)) and bool(self.config.get("webull_allow_hover_source", True)):
            win_obj = by_hwnd.get(root_c)
            sym_w, det_w = grab_ticker_from_webull_ex(
                self.config, self.sidecar, all_windows=wins, source_win=win_obj, cursor_pt=cursor_pt
            )
            return sym_w, det_w, root_c, False

        if for_promote_tos:
            staged = self._read_staged_and_rearm_on_main_thread()
            if staged:
                return staged, f"Staged {staged}", 0, True
            return "", "Point at a TOS grid tile or stage a ticker first!", 0, True

        # Fallback: check if foreground TOS grid had a fresh title change (<3.5s)
        fg_win = by_hwnd.get(int(job.get("fg_at_press") or 0))
        if fg_win is not None and not fg_win.is_iconic and classify_exe_and_class(fg_win.exe_name, fg_win.exe_path, fg_win.class_name) == "tos":
            _, diff_sym, diff_age = self.grid_tracker.observe_tos_window(fg_win)
            if diff_sym and diff_age <= 3.5 and not self.grid_tracker.is_recent_own_push(diff_sym, 10.0):
                return diff_sym, f"{diff_sym} ← TOS (typed) ✓", fg_win.hwnd, False

        return "", "Hover over a TOS chart & press Alt+Q!", 0, False

    def _execute_job_locked(self, job: dict) -> None:
        action = job["action"]
        source = job["source"]
        extra_vks = job.get("extra_vks") or ()

        try:
            self.sidecar.begin_foreground_op()
            if not wait_for_input_settled(
                max_wait_sec=1.20,
                extra_vks=extra_vks,
                on_waiting=lambda: self.set_status("Release shortcut keys...", THEME["fg_warning"]),
            ):
                self.set_status("Release keys & retry!", THEME["fg_warning"])
                return

            if action == "tos_grab":
                wins = enum_visible_top_windows()
                snap = job.get("snap") or (0, "", "", "")
                if snap[1] == "tos" and (snap[3] or extract_tos_title_tickers(snap[2])):
                    sym, det, _, _ = self._resolve_hover_chart_ticker(job, wins, for_promote_tos=False)
                else:
                    recent_h = (
                        self._last_tos_clicked_hwnd
                        if (self._last_tos_clicked_hwnd and (time.perf_counter() - self._last_tos_click_ts) <= 15.0
                            and user32.IsWindowVisible(self._last_tos_clicked_hwnd) and is_tos_frame_hwnd(self._last_tos_clicked_hwnd))
                        else 0
                    )
                    cal_win, _ = find_target_window(
                        "tos", str(self.config.get("tos_window_title") or ""), self.config.get("tos_window_rect"),
                        label="Main TOS", all_windows=wins,
                    )
                    sym, det, _ = grab_tos_2pass(
                        self.config, int(job.get("fg_at_press") or 0), recent_h, self._top_hwnd, (cal_win.hwnd if cal_win else 0)
                    )
                if sym:
                    self._stage_ticker_atomic(sym, det, wait_sync=True)
                    self._record_recent_ticker(sym)
                    record_trace(f"TOS_GRAB ({source}) -> {sym}")
                else:
                    self.set_status(det or "No TOS ticker grabbed", THEME["fg_warning"])
                return

            if action == "hover_deep_dive":
                wins = enum_visible_top_windows()
                sym, det, _, _ = self._resolve_hover_chart_ticker(job, wins, for_promote_tos=False)
                if not sym:
                    self.set_status(det, THEME["fg_warning"])
                    return
                self._stage_ticker_atomic(sym, det, wait_sync=True)
                self._record_recent_ticker(sym)
                self._run_deep_dive_locked(sym, source=source, skip_ti_guard=True)
                return

            if action == "promote_tos":
                wins = enum_visible_top_windows()
                if job.get("explicit_ticker"):
                    sym = job["explicit_ticker"]
                else:
                    sym, det, _, _ = self._resolve_hover_chart_ticker(job, wins, for_promote_tos=True)
                    if not sym:
                        self.set_status(det, THEME["fg_warning"])
                        return
                self._stage_ticker_atomic(sym, f"Main TOS <- {sym}...", wait_sync=True)
                self._record_recent_ticker(sym)

                with self._clip_lock:
                    if not self._clip_restore_pending:
                        ok_c, txt_c, _, is_txt = read_clipboard_unicode(max_bytes=16384)
                        self._clip_saved_ok = bool(ok_c and is_txt)
                        self._clip_saved_txt = txt_c if is_txt else None
                    self._clip_gen += 1
                    my_gen = self._clip_gen

                res_tos = push_ticker_to_tos_main(sym, self.config, self.grid_tracker, all_windows=wins)
                if not res_tos.ok:
                    self.set_status(res_tos.message, THEME["fg_error"])
                    self._schedule_async_clipboard_finalize(sym, 0, "", 0.0, my_gen)
                    return

                mode = self.config.get("promote_tos_mode", "main_only")
                if mode == "main_only":
                    self.set_status(f"🎯 Main TOS: {sym} ✓", THEME["fg_success"])
                    self._schedule_async_clipboard_finalize(sym, 0, "", 0.0, my_gen)
                    return
                if mode == "main_and_bm":
                    self._run_deep_dive_locked(sym, source=source, skip_browser=True, skip_webull=True, skip_ti_guard=True)
                    return
                self._run_deep_dive_locked(sym, source=source, skip_ti_guard=True)
                return

            # Staged actions: deep_dive, bm_only, dt_only, webull_only
            explicit = job.get("explicit_ticker") or ""
            skip_ti = bool(explicit)
            ticker = explicit or ""
            if not ticker:
                settle_guard_sec = max(0, _safe_int(self.config.get("ti_settle_guard_ms"), 400)) / 1000.0
                elapsed_ti = time.perf_counter() - self._last_ti_burst_ts
                if 0.0 <= elapsed_ti < settle_guard_sec:
                    time.sleep(settle_guard_sec - elapsed_ti)
                ticker = self._read_staged_and_rearm_on_main_thread()

            if not ticker:
                self.set_status(f"{source}: No staged ticker!", THEME["fg_warning"])
                return

            self._record_recent_ticker(ticker)
            if action == "bm_only":
                self._run_deep_dive_locked(ticker, source=source, skip_browser=True, skip_webull=True, skip_ti_guard=True)
            elif action == "dt_only":
                self._run_deep_dive_locked(ticker, source=source, skip_bookmap=True, skip_webull=True, skip_ti_guard=True)
            elif action == "webull_only":
                self._run_deep_dive_locked(ticker, source=source, skip_bookmap=True, skip_browser=True, skip_ti_guard=True)
            else:
                self._run_deep_dive_locked(ticker, source=source, skip_ti_guard=skip_ti or True)
        except Exception as e:
            log_error(f"Worker job error ({action})", e)
            self.set_status(f"Error: {e}", THEME["fg_error"])
        finally:
            self.sidecar.end_foreground_op()

    # --------------------------------------------------------------------------
    # Bookmap Helpers (Window Discovery, Safe Modal Dismisser, Log Tail, Tab Switch)
    # --------------------------------------------------------------------------
    def _extract_bookmap_title_ticker(self, title: str) -> str:
        if not title:
            return ""
        bm_lbl = (self.config.get("bookmap_window_title", "Bookmap") or "Bookmap").strip() or "Bookmap"
        m = re.match(r"^([^\s]+)\s{2,}" + re.escape(bm_lbl) + r"(?:\s|$)", title.strip(), flags=re.IGNORECASE)
        return normalize_bookmap_symbol(m.group(1)) if m else ""

    def _list_bookmap_chart_frames(self, wins: list[WindowInfo] | None = None) -> list[tuple[int, str, int]]:
        target_title = (self.config.get("bookmap_window_title", "Bookmap") or "Bookmap").strip().lower()
        browser_exes = {b.lower() for b in self.config.get("browser_executables", [])}
        all_w = wins if wins is not None else enum_visible_top_windows()
        frames: list[tuple[int, int, int, str]] = []
        for w in all_w:
            if w.exe_name in browser_exes or w.class_name.lower() != "sunawtframe":
                continue
            t_low = w.title.lower()
            is_bm_exe = "bookmap" in w.exe_name
            if not (is_bm_exe or target_title in t_low):
                continue
            if any(k in t_low for k in ("control panel", "run options", "launcher", "subscribe", "plugins manager", "tutorial", "log in to your account")):
                continue
            area = w.rect[2] * w.rect[3]
            frames.append((0 if is_bm_exe else 1, -area, w.hwnd, w.title))
        frames.sort()
        return [(h, t, -na) for (_, na, h, t) in frames]

    def _find_bookmap_chart_for_ticker(self, ticker: str, wins: list[WindowInfo] | None = None) -> int:
        tn = normalize_bookmap_symbol(ticker)
        if not tn:
            return 0
        for h, t, _ in self._list_bookmap_chart_frames(wins):
            if self._extract_bookmap_title_ticker(t) == tn:
                return h
        return 0

    def _find_bookmap_hwnd(self, wins: list[WindowInfo] | None = None) -> int:
        charts = self._list_bookmap_chart_frames(wins)
        if charts:
            return charts[0][0]
        target_title = (self.config.get("bookmap_window_title", "Bookmap") or "Bookmap").strip().lower()
        browser_exes = {b.lower() for b in self.config.get("browser_executables", [])}
        cands = []
        for w in (wins if wins is not None else enum_visible_top_windows()):
            if w.exe_name in browser_exes:
                continue
            tl = w.title.lower()
            cl = w.class_name.lower()
            is_bm = "bookmap" in w.exe_name
            if is_bm or (target_title in tl and "sunawt" in cl):
                is_aux = any(k in tl for k in ("control panel", "run options", "launcher", "subscribe")) or ("dialog" in cl)
                cands.append((0 if is_bm else 1, 1 if is_aux else 0, -(w.rect[2] * w.rect[3]), w.hwnd))
        if not cands:
            return 0
        cands.sort()
        return cands[0][3]

    def _is_bookmap_foreground(self, bm_hwnd: int, bm_pid: int) -> bool:
        fg = int(user32.GetForegroundWindow() or 0)
        if not fg:
            time.sleep(0.03)
            fg = int(user32.GetForegroundWindow() or 0)
        if not fg:
            return False
        if fg == bm_hwnd or (bm_pid and get_hwnd_pid(fg) == bm_pid):
            return True
        return "bookmap" in get_hwnd_process_name(fg).lower()

    def _has_bookmap_modal_popup(self, bm_hwnd: int) -> bool:
        if not bm_hwnd or not user32.IsWindow(bm_hwnd):
            return False
        popup, _, _ = get_enabled_popup_info(bm_hwnd)
        return bool(popup) or not bool(user32.IsWindowEnabled(bm_hwnd))

    def _dismiss_stale_bookmap_subscribe_dialog(self, bm_hwnd: int) -> None:
        """
        Safely closes ONLY a stale Subscribe/Symbol SunAwtDialog if open.
        NEVER sends blind Escape to order/confirm/login/reconnect dialogs or bm_hwnd itself!
        """
        if not bm_hwnd:
            return
        popup, p_cls, p_title = get_enabled_popup_info(bm_hwnd)
        if not popup or popup == bm_hwnd:
            return
        cl_low = p_cls.lower()
        t_low = p_title.lower()
        if "dialog" not in cl_low:
            return
        if any(bad in t_low for bad in _DANGEROUS_BM_POPUP_DENY):
            return
        if any(ok_w in t_low for ok_w in _SAFE_BM_POPUP_ALLOW):
            if focus_window_no_alt(popup, max_attempts=6):
                time.sleep(0.04)
                send_escape_reliable(hold_sec=0.03)
                time.sleep(0.09)

    def _locate_bookmap_logs_dir(self, bm_pid: int) -> str:
        if bm_pid and self._bm_logs_dir_cache[0] == bm_pid and self._bm_logs_dir_cache[1] and os.path.isdir(self._bm_logs_dir_cache[1]):
            return self._bm_logs_dir_cache[1]
        cands: list[str] = []
        if bm_pid:
            try:
                cwd = psutil.Process(bm_pid).cwd()
                if cwd:
                    cands.append(os.path.join(os.path.dirname(os.path.normpath(cwd)), "Logs"))
                    cands.append(os.path.join(cwd, "Logs"))
            except Exception:
                pass
        cands.append(r"C:\Bookmap\Logs")
        la = os.environ.get("LOCALAPPDATA", "")
        if la:
            cands.append(os.path.join(la, "Bookmap", "Logs"))
        cands.append(os.path.expanduser(r"~\Bookmap\Logs"))
        for c in cands:
            if c and os.path.isdir(c):
                if bm_pid:
                    self._bm_logs_dir_cache = (bm_pid, c)
                return c
        return ""

    def _ingest_bookmap_log_lines(self, text: str) -> None:
        if not text:
            return
        for line in text.splitlines():
            if "Adding instrument symbol=" in line:
                m = _BM_ADD_SYM_RE.search(line)
                if m:
                    norm = normalize_bookmap_symbol(m.group(1))
                    if norm:
                        if norm not in self._bm_subscribed_tabs:
                            self._bm_subscribed_tabs.append(norm)
                        self._bm_add_seq += 1
                        self._bm_last_added_sym = norm
            elif "Removing tab symbol=" in line and "deleteTemporarily=false" in line:
                m = _BM_REM_TAB_RE.search(line)
                if m:
                    norm = normalize_bookmap_symbol(m.group(1))
                    if norm and norm in self._bm_subscribed_tabs:
                        self._bm_subscribed_tabs.remove(norm)
            elif "onInstrumentAlreadySubscribed " in line:
                tail = line.split("onInstrumentAlreadySubscribed ", 1)[1].split()
                if tail:
                    norm = normalize_bookmap_symbol(tail[0])
                    if norm:
                        if norm not in self._bm_subscribed_tabs:
                            self._bm_subscribed_tabs.append(norm)
                        self._bm_already_sub_seq += 1
                        self._bm_last_already_sub_sym = norm

    def _sync_bookmap_log_state(self, bm_pid: int) -> tuple[list[str], bool]:
        logs_dir = self._locate_bookmap_logs_dir(bm_pid)
        if not logs_dir:
            return list(self._bm_subscribed_tabs), False
        sessions: dict[str, dict] = {}
        try:
            with os.scandir(logs_dir) as it:
                for entry in it:
                    if not entry.is_file():
                        continue
                    m = _BM_COMMON_LOG_RE.match(entry.name)
                    if not m:
                        continue
                    pfx = m.group(1).lower()
                    ymd, hms, mmm = m.group(2), m.group(3), m.group(4)
                    cnum, is_gz = int(m.group(5)), bool(m.group(6))
                    sess = sessions.get(pfx)
                    if sess is None:
                        try:
                            ep = float(calendar.timegm(time.strptime(f"{ymd}_{hms}", "%Y%m%d_%H%M%S"))) + int(mmm) / 1000.0
                        except Exception:
                            ep = 0.0
                        sess = {"epoch": ep, "has_txt": False, "latest_mtime": 0.0, "chunks": {}}
                        sessions[pfx] = sess
                    if not is_gz:
                        sess["has_txt"] = True
                        try:
                            mt = entry.stat().st_mtime
                            if mt > sess["latest_mtime"]:
                                sess["latest_mtime"] = mt
                        except Exception:
                            pass
                    ex = sess["chunks"].get(cnum)
                    if ex is None or (ex[1] and not is_gz):
                        sess["chunks"][cnum] = (entry.path, is_gz)
        except Exception:
            return list(self._bm_subscribed_tabs), False

        active = [(p, info) for p, info in sessions.items() if info["has_txt"]]
        if not active:
            return list(self._bm_subscribed_tabs), False

        proc_ct = 0.0
        if bm_pid:
            try:
                proc_ct = float(psutil.Process(bm_pid).create_time())
            except Exception:
                pass
        chosen_pfx, chosen_info = "", None
        if proc_ct > 0.0:
            near = [(p, info) for p, info in active if info["epoch"] >= (proc_ct - 15.0)]
            if near:
                near.sort(key=lambda kv: abs(kv[1]["epoch"] - proc_ct))
                chosen_pfx, chosen_info = near[0]
        if chosen_info is None:
            active.sort(key=lambda kv: (kv[1]["latest_mtime"], kv[1]["epoch"]), reverse=True)
            chosen_pfx, chosen_info = active[0]

        skey = (bm_pid, chosen_pfx)
        if skey != self._bm_log_session_key:
            self._bm_log_session_key = skey
            self._bm_log_offsets.clear()
            self._bm_log_gz_done.clear()
            self._bm_subscribed_tabs.clear()

        for cnum in sorted(chosen_info["chunks"].keys()):
            cpath, is_gz = chosen_info["chunks"][cnum]
            if is_gz:
                if cnum in self._bm_log_gz_done:
                    continue
                try:
                    with gzip.open(cpath, "rb") as gf:
                        raw_gz = gf.read()
                    prev = self._bm_log_offsets.get(cnum, 0)
                    if len(raw_gz) > prev:
                        self._ingest_bookmap_log_lines(raw_gz[prev:].decode("utf-8", errors="replace"))
                    self._bm_log_offsets[cnum] = len(raw_gz)
                    self._bm_log_gz_done.add(cnum)
                except Exception:
                    pass
            else:
                try:
                    prev = self._bm_log_offsets.get(cnum, 0)
                    txt, new_off = read_shared_file_from_offset(cpath, prev)
                    self._bm_log_offsets[cnum] = new_off
                    if txt:
                        self._ingest_bookmap_log_lines(txt)
                except Exception:
                    pass
        return list(self._bm_subscribed_tabs), True

    def _switch_bookmap_to_tab(
        self, bm_hwnd: int, _bm_pid: int, ticker: str, approx_tabs: list[str], step_label: str
    ) -> tuple[bool, int]:
        target_norm = normalize_bookmap_symbol(ticker)
        if not bm_hwnd or not target_norm:
            return False, bm_hwnd
        cur_hwnd = bm_hwnd
        init_title = get_window_text(cur_hwnd)
        cur_norm = self._extract_bookmap_title_ticker(init_title)
        if cur_norm == target_norm:
            return True, cur_hwnd

        reverse = False
        if cur_norm and cur_norm in approx_tabs and target_norm in approx_tabs and len(approx_tabs) >= 2:
            n = len(approx_tabs)
            ci, ti = approx_tabs.index(cur_norm), approx_tabs.index(target_norm)
            reverse = ((ci - ti) % n) < ((ti - ci) % n)

        max_steps = min(24, max(6, len(approx_tabs) + 2))
        other_frames = [h for h, _, _ in self._list_bookmap_chart_frames() if h != cur_hwnd]
        seen_titles = {init_title} if init_title else set()
        pivoted = False
        step = 0
        while step < max_steps:
            fg_now = int(user32.GetForegroundWindow() or 0)
            if fg_now and is_tos_process_hwnd(fg_now):
                raise RuntimeError("Blocked Bookmap tab switch: Thinkorswim owns foreground")
            if fg_now != cur_hwnd:
                if not focus_window_no_alt(cur_hwnd, max_attempts=6):
                    return False, cur_hwnd
                wait_for_stable_foreground(cur_hwnd, settle_ms=70, max_total_ms=300)
                inp_tab_u = INPUT(type=INPUT_KEYBOARD)
                inp_tab_u.union.ki = KEYBDINPUT(wVk=VK_TAB, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)
                user32.SendInput(1, ctypes.byref(inp_tab_u), ctypes.sizeof(INPUT))
                time.sleep(0.015)
                if int(user32.GetForegroundWindow() or 0) != cur_hwnd:
                    return False, cur_hwnd

            prev_title = get_window_text(cur_hwnd)
            lbl = "Ctrl+Shift+Tab" if reverse else "Ctrl+Tab"
            try:
                send_ctrl_vk_guarded(VK_TAB, shift=reverse, hold_sec=0.025, expected_hwnd=cur_hwnd, label=lbl)
            except FocusLostError:
                if not focus_window_no_alt(cur_hwnd, max_attempts=6):
                    return False, cur_hwnd
                time.sleep(0.03)
                try:
                    send_ctrl_vk_guarded(VK_TAB, shift=reverse, hold_sec=0.025, expected_hwnd=cur_hwnd, label=lbl)
                except FocusLostError:
                    return False, cur_hwnd

            t_w = time.perf_counter()
            new_title = prev_title
            while (time.perf_counter() - t_w) < 0.34:
                time.sleep(0.015)
                ct = get_window_text(cur_hwnd)
                if ct and ct != prev_title:
                    new_title = ct
                    break

            if new_title == prev_title:
                if step == 0 and other_frames and not pivoted:
                    cur_hwnd = other_frames.pop(0)
                    pivoted = True
                    self._dismiss_stale_bookmap_subscribe_dialog(cur_hwnd)
                    if not focus_window_no_alt(cur_hwnd, max_attempts=8):
                        return False, bm_hwnd
                    wait_for_stable_foreground(cur_hwnd, settle_ms=90, max_total_ms=350)
                    pt = get_window_text(cur_hwnd)
                    if self._extract_bookmap_title_ticker(pt) == target_norm:
                        return True, cur_hwnd
                    seen_titles = {pt} if pt else set()
                    continue
                return False, cur_hwnd

            cur_norm = self._extract_bookmap_title_ticker(new_title)
            if cur_norm == target_norm:
                time.sleep(0.035)
                return True, cur_hwnd
            if new_title in seen_titles:
                return False, cur_hwnd
            seen_titles.add(new_title)
            step += 1
            self.set_status(f"[{ticker}] {step_label}: Switching BM tab ({cur_norm or '...'})...", THEME["fg_warning"])
        return False, cur_hwnd

    def _find_browser_hwnd(self, wins: list[WindowInfo] | None = None) -> int:
        browser_exes = {b.lower() for b in self.config.get("browser_executables", [])}
        title_hint = self.config.get("browser_title_hint", "").strip().lower()
        all_w = wins if wins is not None else enum_visible_top_windows()
        bwins = [
            w for w in all_w
            if w.exe_name in browser_exes and (
                w.class_name.lower() in BROWSER_FRAME_CLASSES or "mozilla" in w.class_name.lower() or "chrome" in w.class_name.lower()
            )
        ] or [w for w in all_w if w.exe_name in browser_exes]
        if not bwins:
            return 0
        if title_hint:
            h_m = [w for w in bwins if title_hint in w.title.lower()]
            if h_m:
                self._last_browser_hwnd = h_m[0].hwnd
                return self._last_browser_hwnd
        dt_m = [w for w in bwins if "dilutiontracker" in w.title.lower()]
        if dt_m:
            self._last_browser_hwnd = dt_m[0].hwnd
            return self._last_browser_hwnd
        for w in bwins:
            if w.hwnd == self._last_browser_hwnd:
                return w.hwnd
        bwins.sort(key=lambda w: -(w.rect[2] * w.rect[3]))
        self._last_browser_hwnd = bwins[0].hwnd
        return self._last_browser_hwnd

    # --------------------------------------------------------------------------
    # Off-Lock Generation-Counted Async Clipboard Finalizer
    # --------------------------------------------------------------------------
    def _bump_clip_gen(self) -> int:
        with self._clip_lock:
            self._clip_gen += 1
            return self._clip_gen

    def _schedule_async_clipboard_finalize(
        self, ticker: str, browser_hwnd: int, title_at_enter: str, dt_enter_ts: float, expected_gen: int
    ) -> None:
        mode = self.config.get("clipboard_after_deep_dive", "keep_ticker")
        with self._clip_lock:
            self._clip_restore_pending = True

        def _bg():
            try:
                if browser_hwnd and dt_enter_ts > 0.0:
                    deadline = dt_enter_ts + 1.20
                    floor_ts = dt_enter_ts + 0.50
                    while time.perf_counter() < deadline:
                        with self._clip_lock:
                            if self._clip_gen != expected_gen:
                                return
                        now_c = time.perf_counter()
                        cur_t = get_window_text(browser_hwnd)
                        if now_c >= floor_ts and cur_t and cur_t != title_at_enter:
                            break
                        time.sleep(0.025)

                with self._clip_lock:
                    if self._clip_gen != expected_gen:
                        return
                    if mode == "restore_previous":
                        if self._clip_saved_ok and self._clip_saved_txt is not None:
                            write_clipboard_unicode(self._clip_saved_txt, exclude_from_history=True)
                    else:
                        write_clipboard_unicode(ticker, exclude_from_history=True)
                    self._clip_restore_pending = False
            except Exception:
                pass

        threading.Thread(target=_bg, daemon=True).start()

    # --------------------------------------------------------------------------
    # Core Deep-Dive Pipeline (Bookmap -> DilutionTracker -> Optional Webull)
    # --------------------------------------------------------------------------
    def _run_deep_dive_locked(
        self,
        ticker: str,
        *,
        source: str = "Manual",
        skip_bookmap: bool = False,
        skip_browser: bool = False,
        skip_webull: bool = False,
        skip_ti_guard: bool = False,
    ) -> None:
        t_start = time.perf_counter()
        if not skip_ti_guard:
            sg = max(0, _safe_int(self.config.get("ti_settle_guard_ms"), 400)) / 1000.0
            el_ti = time.perf_counter() - self._last_ti_burst_ts
            if 0.0 <= el_ti < sg:
                time.sleep(sg - el_ti)

        # Snapshot user's clipboard ONLY if an async restore isn't currently pending
        with self._clip_lock:
            if not self._clip_restore_pending:
                ok_c, txt_c, _, is_txt = read_clipboard_unicode(max_bytes=16384)
                self._clip_saved_ok = bool(ok_c and is_txt)
                self._clip_saved_txt = txt_c if is_txt else None
            self._clip_gen += 1
            my_gen = self._clip_gen

        wins = enum_visible_top_windows()
        bm_ms = 0
        dt_ms = 0
        wb_ms = 0
        dt_enter_ts = 0.0
        dt_title_at_enter = ""
        browser_hwnd = 0

        # ======================================================================
        # STEP 1: BOOKMAP (3-Stage Switch-Existing or Event-Driven Ctrl+T)
        # ======================================================================
        bm_hwnd = 0
        bm_did_dialog = False
        if not skip_bookmap:
            t_bm0 = time.perf_counter()
            bm_lbl = "BM Only" if (skip_browser and skip_webull) else "Step 1/2"
            switch_bm = bool(self.config.get("bookmap_switch_existing_tab", True))
            target_bm_norm = normalize_bookmap_symbol(ticker)
            bm_done = False
            post_enter_elapsed = 0.0

            self.set_status(f"[{ticker}] {bm_lbl}: Focusing Bookmap...", THEME["fg_warning"])
            matched_bm = self._find_bookmap_chart_for_ticker(ticker, wins) if switch_bm else 0
            bm_hwnd = matched_bm or self._find_bookmap_hwnd(wins)
            bm_pid = get_hwnd_pid(bm_hwnd) if bm_hwnd else 0
            bm_was_ic = bool(bm_hwnd and user32.IsIconic(bm_hwnd))

            if bm_hwnd:
                self._dismiss_stale_bookmap_subscribe_dialog(bm_hwnd)

            if bm_hwnd and focus_window_no_alt(bm_hwnd, max_attempts=14):
                bm_foc_ms = _safe_int(self.config.get("bookmap_focus_delay_ms"), 220, 50, 5000)
                eff_bm_settle = max(300, bm_foc_ms + 100) if bm_was_ic else bm_foc_ms
                bm_stable = wait_for_stable_foreground(bm_hwnd, settle_ms=eff_bm_settle, max_total_ms=950)
                if not bm_stable and not self._is_bookmap_foreground(bm_hwnd, bm_pid):
                    if skip_browser and skip_webull:
                        self.set_status(f"[{ticker}] Could not focus Bookmap!", THEME["fg_error"])
                        return
                else:
                    my_gen = self._bump_clip_gen()
                    write_clipboard_unicode(ticker, exclude_from_history=True)

                    if switch_bm and target_bm_norm and self._extract_bookmap_title_ticker(get_window_text(bm_hwnd)) == target_bm_norm:
                        bm_done = True
                    if not bm_done and switch_bm and target_bm_norm:
                        approx_tabs, _ = self._sync_bookmap_log_state(bm_pid)
                        if target_bm_norm in approx_tabs:
                            self.set_status(f"[{ticker}] {bm_lbl}: Switching Bookmap tab...", THEME["fg_warning"])
                            sw_ok, bm_hwnd = self._switch_bookmap_to_tab(bm_hwnd, bm_pid, ticker, approx_tabs, bm_lbl)
                            if sw_ok:
                                bm_done = True

                    if not bm_done:
                        bm_did_dialog = True
                        send_ctrl_key_guarded("t", hold_sec=0.035)
                        bm_dlg_cap = max(0.10, _safe_int(self.config.get("bookmap_dialog_delay_ms"), 280, 50, 5000) / 1000.0)

                        # Event-driven wait for Subscribe JDialog to open & own foreground + 45ms Swing JTextField floor
                        t_op = time.perf_counter()
                        dlg_ready = False
                        while (time.perf_counter() - t_op) < bm_dlg_cap:
                            pop_h, pop_c, _ = get_enabled_popup_info(bm_hwnd)
                            fg_c = int(user32.GetForegroundWindow() or 0)
                            if pop_h and "dialog" in pop_c.lower() and fg_c == pop_h:
                                time.sleep(0.045)
                                dlg_ready = True
                                break
                            time.sleep(0.015)

                        if not dlg_ready and not self._is_bookmap_foreground(bm_hwnd, bm_pid):
                            self.set_status(f"[{ticker}] Bookmap focus stolen — retrying Ctrl+T...", THEME["fg_warning"])
                            focus_window_no_alt(bm_hwnd, max_attempts=10)
                            wait_for_stable_foreground(bm_hwnd, settle_ms=160, max_total_ms=550)
                            send_ctrl_key_guarded("t", hold_sec=0.035)
                            time.sleep(bm_dlg_cap)

                        if self._is_bookmap_foreground(bm_hwnd, bm_pid):
                            rc_bm = wintypes.RECT()
                            user32.GetWindowRect(bm_hwnd, ctypes.byref(rc_bm))
                            if self.config.get("bookmap_use_symbol_click", False):
                                rx, ry = _safe_int(self.config.get("bookmap_symbol_rel_x"), 0), _safe_int(self.config.get("bookmap_symbol_rel_y"), 0)
                                if rx or ry:
                                    fg_now = int(user32.GetForegroundWindow() or bm_hwnd)
                                    click_screen_point_safe(rc_bm.left + rx, rc_bm.top + ry, expected_hwnd=fg_now, restore_cursor=True)
                                    time.sleep(0.04)

                            my_gen = self._bump_clip_gen()
                            write_clipboard_unicode(ticker, exclude_from_history=True)
                            time.sleep(0.020)
                            send_ctrl_key_guarded("a", hold_sec=0.028)
                            time.sleep(0.030)
                            send_ctrl_key_guarded("v", hold_sec=0.032)
                            time.sleep(0.060)

                            if self.config.get("bookmap_use_subscribe_click", False):
                                sx, sy = _safe_int(self.config.get("bookmap_subscribe_rel_x"), 0), _safe_int(self.config.get("bookmap_subscribe_rel_y"), 0)
                                if sx or sy:
                                    fg_now = int(user32.GetForegroundWindow() or bm_hwnd)
                                    click_screen_point_safe(rc_bm.left + sx, rc_bm.top + sy, expected_hwnd=fg_now, restore_cursor=True)
                                    time.sleep(0.03)

                            if switch_bm:
                                self._sync_bookmap_log_state(bm_pid)
                            pre_add_seq = self._bm_add_seq
                            pre_alr_seq = self._bm_already_sub_seq
                            send_enter_reliable(hold_sec=0.035)
                            t_ent = time.perf_counter()

                            if switch_bm and target_bm_norm:
                                trans_cap = _safe_int(self.config.get("step_transition_delay_ms"), 250, 50, 5000) / 1000.0
                                poll_budget = 0.65 if (skip_browser and skip_webull) else max(0.32, 0.08 + trans_cap)
                                while (time.perf_counter() - t_ent) < poll_budget:
                                    time.sleep(0.020)
                                    if self._extract_bookmap_title_ticker(get_window_text(bm_hwnd)) == target_bm_norm:
                                        break
                                    approx_after, log_ok = self._sync_bookmap_log_state(bm_pid)
                                    if log_ok and self._bm_already_sub_seq > pre_alr_seq and self._bm_last_already_sub_sym == target_bm_norm:
                                        det_h = self._find_bookmap_chart_for_ticker(ticker)
                                        if det_h:
                                            focus_window_no_alt(det_h, max_attempts=8)
                                            bm_done = True
                                            break
                                        t_dlg = time.perf_counter()
                                        while (time.perf_counter() - t_dlg) < 0.35:
                                            if not self._has_bookmap_modal_popup(bm_hwnd):
                                                if int(user32.GetForegroundWindow() or 0) != bm_hwnd:
                                                    focus_window_no_alt(bm_hwnd, max_attempts=6)
                                                if int(user32.GetForegroundWindow() or 0) == bm_hwnd:
                                                    break
                                            time.sleep(0.020)
                                        if int(user32.GetForegroundWindow() or 0) == bm_hwnd:
                                            sw_p, bm_hwnd = self._switch_bookmap_to_tab(bm_hwnd, bm_pid, ticker, approx_after, bm_lbl)
                                            if sw_p:
                                                bm_done = True
                                        break
                                    if log_ok and self._bm_add_seq > pre_add_seq and self._bm_last_added_sym == target_bm_norm:
                                        break
                            else:
                                time.sleep(0.09)
                            post_enter_elapsed = time.perf_counter() - t_ent
                        else:
                            self.set_status(f"[{ticker}] Bookmap focus lost during Ctrl+T", THEME["fg_warning"])
                            if skip_browser and skip_webull:
                                return
            else:
                if skip_browser and skip_webull:
                    self.set_status(f"[{ticker}] Bookmap window not found!", THEME["fg_error"])
                    return
                self.set_status(f"[{ticker}] Bookmap not found — jumping to DT...", THEME["fg_warning"])

            # Wait for Bookmap Subscribe dialog close before touching clipboard for DT!
            if bm_did_dialog and bm_hwnd and not bm_done and (not skip_browser or not skip_webull):
                t_close = time.perf_counter()
                while (time.perf_counter() - t_close) < 0.40 and self._has_bookmap_modal_popup(bm_hwnd):
                    time.sleep(0.015)
                if post_enter_elapsed < 0.08:
                    time.sleep(0.08 - post_enter_elapsed)

            bm_ms = int((time.perf_counter() - t_bm0) * 1000)
            if skip_browser and skip_webull:
                record_trace(f"BM_ONLY '{ticker}' ({source}) -> OK ({bm_ms}ms)")
                self.set_status(f"BM Complete: {ticker} ({bm_ms}ms)", THEME["fg_success"])
                self._schedule_async_clipboard_finalize(ticker, 0, "", 0.0, my_gen)
                return

        # ======================================================================
        # STEP 2: DILUTIONTRACKER BROWSER (Wrong-Tab Guard + Same-Ticker Skip)
        # ======================================================================
        dt_status_note = ""
        if not skip_browser:
            t_dt0 = time.perf_counter()
            tab_idx = _safe_int(self.config.get("pinned_tab_index"), 6, 1, 8)
            step_lbl = "DT Only" if skip_bookmap else "Step 2/2"
            self.set_status(f"[{ticker}] {step_lbl}: Browser Ctrl+{tab_idx}...", THEME["fg_warning"])

            browser_hwnd = self._find_browser_hwnd(wins)
            br_was_ic = bool(browser_hwnd and user32.IsIconic(browser_hwnd))
            if not browser_hwnd or not focus_window_no_alt(browser_hwnd, max_attempts=14):
                self.set_status(f"[{ticker}] Browser window not found!", THEME["fg_error"])
                self._schedule_async_clipboard_finalize(ticker, 0, "", 0.0, my_gen)
                return

            br_foc_ms = _safe_int(self.config.get("browser_focus_delay_ms"), 120, 20, 5000)
            eff_br_settle = max(220, br_foc_ms + 100) if br_was_ic else max(55, br_foc_ms)
            wait_for_stable_foreground(browser_hwnd, settle_ms=eff_br_settle, max_total_ms=850)
            if int(user32.GetForegroundWindow() or 0) != browser_hwnd:
                focus_window_no_alt(browser_hwnd, max_attempts=6)
                time.sleep(0.05)
            if int(user32.GetForegroundWindow() or 0) != browser_hwnd:
                self.set_status(f"[{ticker}] Browser focus lost before Ctrl+{tab_idx}!", THEME["fg_error"])
                self._schedule_async_clipboard_finalize(ticker, 0, "", 0.0, my_gen)
                return

            send_ctrl_key_guarded(str(tab_idx), hold_sec=0.028, expected_hwnd=browser_hwnd)
            br_tab_cap = max(0.24, _safe_int(self.config.get("browser_tab_delay_ms"), 120, 20, 5000) / 1000.0)
            dt_hint = str(self.config.get("dt_tab_title_hint") or "").strip().lower()

            t_tab = time.perf_counter()
            tab_title = get_window_text(browser_hwnd)
            if dt_hint:
                while (time.perf_counter() - t_tab) < br_tab_cap:
                    tab_title = get_window_text(browser_hwnd)
                    if dt_hint in tab_title.lower():
                        break
                    time.sleep(0.020)
                else:
                    tab_title = get_window_text(browser_hwnd)

                if dt_hint not in tab_title.lower():
                    if bool(self.config.get("dt_open_new_tab_if_missing", False)):
                        if int(user32.GetForegroundWindow() or 0) != browser_hwnd:
                            focus_window_no_alt(browser_hwnd, max_attempts=6)
                        send_ctrl_key_guarded("t", hold_sec=0.028, expected_hwnd=browser_hwnd)
                        time.sleep(0.14)
                    else:
                        record_trace(f"DT_ABORT '{ticker}' -> Ctrl+{tab_idx} title={tab_title!r} missing hint {dt_hint!r}")
                        self.set_status(f"Tab Ctrl+{tab_idx} isn't DilutionTracker — aborted!", THEME["fg_error"])
                        self._schedule_async_clipboard_finalize(ticker, 0, "", 0.0, my_gen)
                        return
            else:
                time.sleep(max(0.05, _safe_int(self.config.get("browser_tab_delay_ms"), 120) / 1000.0))
                tab_title = get_window_text(browser_hwnd)

            # Same-ticker fast skip (case-sensitive match + >4.0s double-tap refresh bypass!)
            last_nav = self._last_dt_nav_ts.get(ticker, 0.0)
            if (
                bool(self.config.get("dt_skip_if_same_ticker", True))
                and matches_dilution_tracker_title_ticker(tab_title, ticker)
                and (time.monotonic() - last_nav) > 4.0
            ):
                dt_status_note = " (DT cached)"
            else:
                if int(user32.GetForegroundWindow() or 0) != browser_hwnd:
                    focus_window_no_alt(browser_hwnd, max_attempts=6)
                    time.sleep(0.04)
                if int(user32.GetForegroundWindow() or 0) != browser_hwnd:
                    self.set_status(f"[{ticker}] Browser focus lost before URL paste!", THEME["fg_error"])
                    self._schedule_async_clipboard_finalize(ticker, 0, "", 0.0, my_gen)
                    return

                url_tmpl = self.config.get("dilution_tracker_url_template", "https://dilutiontracker.com/app/search/{ticker}")
                target_url = url_tmpl.replace("{ticker}", ticker).replace("{staged_ticker}", ticker)
                my_gen = self._bump_clip_gen()
                write_clipboard_unicode(target_url, exclude_from_history=True)

                send_ctrl_key_guarded("l", hold_sec=0.028, expected_hwnd=browser_hwnd)
                br_url_sec = max(0.04, _safe_int(self.config.get("browser_url_delay_ms"), 100, 20, 5000) / 1000.0)
                time.sleep(br_url_sec)

                send_ctrl_key_guarded("v", hold_sec=0.028, expected_hwnd=browser_hwnd)
                time.sleep(0.045)
                send_enter_reliable(hold_sec=0.032, expected_hwnd=browser_hwnd)
                dt_enter_ts = time.perf_counter()
                dt_title_at_enter = tab_title
                self._last_dt_nav_ts[ticker] = time.monotonic()

            dt_ms = int((time.perf_counter() - t_dt0) * 1000)

        # ======================================================================
        # STEP 3: OPTIONAL WEBULL DESKTOP (ONLY WHEN MASTER ENABLED)
        # ======================================================================
        webull_on = bool(self.config.get("webull_enabled", False))
        run_webull = webull_on and not skip_webull and (
            (skip_bookmap and skip_browser) or (not skip_browser and bool(self.config.get("webull_include_in_deep_dive", True)))
        )
        wb_note = ""
        if run_webull:
            if dt_enter_ts > 0.0:
                # Ensure Firefox/Chrome processed Ctrl+V BEFORE Webull overwrites clipboard with ticker!
                while (time.perf_counter() - dt_enter_ts) < 0.35:
                    if browser_hwnd and get_window_text(browser_hwnd) != dt_title_at_enter:
                        break
                    time.sleep(0.020)
                if (time.perf_counter() - dt_enter_ts) < 0.28:
                    time.sleep(0.28 - (time.perf_counter() - dt_enter_ts))

            t_wb0 = time.perf_counter()
            self.set_status(f"[{ticker}] Sending to Webull...", THEME["fg_warning"])
            my_gen = self._bump_clip_gen()
            wb_res = push_ticker_to_webull(ticker, self.config, self.sidecar, all_windows=wins)
            wb_ms = int((time.perf_counter() - t_wb0) * 1000)
            if not wb_res.ok:
                wb_note = f" | WB: {wb_res.message}"

        total_ms = int((time.perf_counter() - t_start) * 1000)
        parts = []
        if not skip_bookmap:
            parts.append(f"BM={bm_ms}ms")
        if not skip_browser:
            parts.append(f"DT={dt_ms}ms")
        if run_webull:
            parts.append(f"WB={wb_ms}ms")
        record_trace(f"DEEP_DIVE '{ticker}' ({source}) -> OK ({', '.join(parts)}, total={total_ms}ms){wb_note}")

        if skip_bookmap and skip_browser and run_webull:
            self.set_status(f"Webull Loaded: {ticker} ({wb_ms}ms)" if not wb_note else wb_note.strip(" |"),
                            THEME["fg_success"] if not wb_note else THEME["fg_warning"])
        elif skip_bookmap:
            self.set_status(f"DT Complete: {ticker}{dt_status_note} ({dt_ms}ms)", THEME["fg_success"])
        else:
            self.set_status(f"Deep-Dive: {ticker}{dt_status_note} ({total_ms}ms){wb_note}", THEME["fg_success"])

        self._schedule_async_clipboard_finalize(ticker, browser_hwnd, dt_title_at_enter, dt_enter_ts, my_gen)

    # --------------------------------------------------------------------------
    # Clean Shutdown
    # --------------------------------------------------------------------------
    def on_close(self) -> None:
        self._closing = True
        try:
            self.config["window_x"] = self.root.winfo_x()
            self.config["window_y"] = self.root.winfo_y()
            self.config["recent_tickers"] = list(self._recent_tickers[:10])
            self.save_config()
        except Exception:
            pass
        try:
            self._close_settings_window()
        except Exception:
            pass
        try:
            self.hotkeys.stop()
        except Exception:
            pass
        try:
            self.sidecar.stop()
        except Exception:
            pass
        try:
            if self._edit_font:
                gdi32.DeleteObject(self._edit_font)
                self._edit_font = 0
        except Exception:
            pass
        self.root.destroy()


def main() -> None:
    dpi_mode = enable_per_monitor_dpi_awareness()
    sys.excepthook = lambda _t, exc_val, _tb: log_error("Uncaught exception", exc_val)
    threading.excepthook = lambda args: log_error(
        f"Unhandled thread exception in {getattr(args.thread, 'name', 'thread')}", args.exc_value
    )
    close_previous_instances()
    root = tk.Tk()
    root.report_callback_exception = lambda _t, exc_val, _tb: log_error("Tkinter callback exception", exc_val)
    TickerAutomationApp(root, dpi_mode=dpi_mode)
    root.mainloop()


if __name__ == "__main__":
    main()
