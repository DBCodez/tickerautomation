"""
Pure-Win32 Engine, Zero-Click TOS Grid Tracker, Hardware-Guarded Push & Hotkey Manager
======================================================================================
Clean modular engine for TI Ticker Deep-Dive v3.0.

CRITICAL LIVE-TRADING & WIN32 INVARIANTS ENFORCED HERE:
1. Private `ctypes.WinDLL(..., use_last_error=True)` handles ONLY — never mutate `ctypes.windll`.
2. Call `enable_per_monitor_dpi_awareness()` once BEFORE `tk.Tk()` is created.
3. NEVER send `Ctrl+<letter>` or `Ctrl+Shift+<letter>` when Thinkorswim (`thinkorswim.exe`)
   owns foreground (guarded at `send_ctrl_vk_guarded` / `send_ctrl_key_guarded`).
   NEVER allow raw letter chords in `send_vk_chord_atomic`.
4. NEVER send `VK_MENU` (`Alt`) when focusing windows (`focus_window_no_alt` uses unassigned
   `0x0E` key-up) or masking `RegisterHotKey` `Alt` triggers (`tap_menu_mask_vk_if_held`
   sends an atomic `0xE8` key-down + key-up `SendInput` pair on the hotkey thread).
5. Clipboard writes tag `ExcludeClipboardContentFromMonitorProcessing` & `CanIncludeInClipboardHistory=0`
   so Windows Win+V history is never cluttered.
6. All `RegisterHotKey` / `UnregisterHotKey` calls run strictly on the `HotkeyManager` owner thread.
7. Webull discovery & heuristics remain 100% disabled whenever `include_webull=False`.
"""

from __future__ import annotations

from collections import Counter, deque
from contextlib import contextmanager
from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import os
import queue
import re
import subprocess
import threading
import time


# ==============================================================================
# 1. ISOLATED WIN32 DLL HANDLES & 64-BIT SIGNATURES
# ==============================================================================
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
ULONG_PTR = ctypes.c_size_t


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.UINT),
        ("flags", wintypes.UINT),
        ("showCmd", wintypes.UINT),
        ("ptMinPosition", wintypes.POINT),
        ("ptMaxPosition", wintypes.POINT),
        ("rcNormalPosition", wintypes.RECT),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTunion(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", _INPUTunion),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ULONG_PTR),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


# Win32 Constants
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_TABSTOP = 0x00010000
WS_POPUP = 0x80000000
WS_CAPTION = 0x00C00000

WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

ES_LEFT = 0x0000
ES_MULTILINE = 0x0004
ES_UPPERCASE = 0x0008
ES_AUTOVSCROLL = 0x0040
ES_AUTOHSCROLL = 0x0080
ES_WANTRETURN = 0x1000

WM_SETTEXT = 0x000C
WM_CLOSE = 0x0010
WM_QUIT = 0x0012
WM_SETFONT = 0x0030
WM_SYSCOMMAND = 0x0112
WM_HOTKEY = 0x0312
SC_RESTORE = 0xF120
EM_GETSEL = 0x00B0
EM_SETSEL = 0x00B1

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
MAPVK_VK_TO_VSC = 0

VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_UNASSIGNED_FOCUS = 0x0E
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_ESCAPE = 0x1B
VK_END = 0x23
VK_HOME = 0x24
VK_INSERT = 0x2D
VK_DELETE = 0x2E
VK_D = 0x44
VK_G = 0x47
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5
VK_MENU_MASK = 0xE8

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

SMTO_ABORTIFHUNG = 0x0002
GW_HWNDNEXT = 2
GW_OWNER = 4
GW_ENABLEDPOPUP = 6
GA_ROOT = 2

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
SW_SHOW = 5
SW_RESTORE = 9

GWL_STYLE = -16
GWL_EXSTYLE = -20
HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

MONITOR_DEFAULTTONULL = 0
MONITOR_DEFAULTTONEAREST = 2

SPI_GETFOREGROUNDLOCKTIMEOUT = 0x2000
SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001
SM_SWAPBUTTON = 23

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_ELEVATION_CLASS = 20

DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
WDA_EXCLUDEFROMCAPTURE = 0x00000011
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_ssize_t(-4)

GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_SHARE_DELETE = 0x00000004
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x80
FILE_BEGIN = 0
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

JOB_OBJECT_EXTENDED_LIMIT_INFO_CLASS = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

# Explicit 64-bit ctypes bindings
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.DestroyWindow.restype = wintypes.BOOL
user32.MoveWindow.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.BOOL]
user32.MoveWindow.restype = wintypes.BOOL
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    wintypes.UINT, wintypes.UINT, ctypes.POINTER(ULONG_PTR),
]
user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsWindowEnabled.argtypes = [wintypes.HWND]
user32.IsWindowEnabled.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.IsZoomed.restype = wintypes.BOOL
user32.GetTopWindow.argtypes = [wintypes.HWND]
user32.GetTopWindow.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
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
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
user32.SetWindowPos.restype = wintypes.BOOL
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD]
user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
user32.SetWindowRgn.restype = ctypes.c_int
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int
user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]
user32.FillRect.restype = ctypes.c_int
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
user32.GetWindowPlacement.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, wintypes.LPVOID, wintypes.UINT]
user32.SystemParametersInfoW.restype = wintypes.BOOL
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = wintypes.HMONITOR
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HMONITOR
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wintypes.BOOL
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = wintypes.SHORT
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.BlockInput.argtypes = [wintypes.BOOL]
user32.BlockInput.restype = wintypes.BOOL
user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
user32.GetKeyboardLayout.restype = wintypes.HANDLE
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = wintypes.BOOL
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardSequenceNumber.argtypes = []
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
user32.GetClipboardOwner.argtypes = []
user32.GetClipboardOwner.restype = wintypes.HWND
user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
user32.RegisterClipboardFormatW.restype = wintypes.UINT

dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long

gdi32.CreateFontW.argtypes = [
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR,
]
gdi32.CreateFontW.restype = wintypes.HFONT
gdi32.CreateRectRgn.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
gdi32.CreateRectRgn.restype = wintypes.HRGN
gdi32.CombineRgn.argtypes = [wintypes.HRGN, wintypes.HRGN, wintypes.HRGN, ctypes.c_int]
gdi32.CombineRgn.restype = ctypes.c_int
gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL

kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.GetCurrentProcessId.argtypes = []
kernel32.GetCurrentProcessId.restype = wintypes.DWORD
kernel32.GetLastError.argtypes = []
kernel32.GetLastError.restype = wintypes.DWORD
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.GetFileSizeEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_longlong)]
kernel32.GetFileSizeEx.restype = wintypes.BOOL
kernel32.SetFilePointerEx.argtypes = [
    wintypes.HANDLE, ctypes.c_longlong, ctypes.POINTER(ctypes.c_longlong), wintypes.DWORD,
]
kernel32.SetFilePointerEx.restype = wintypes.BOOL
kernel32.ReadFile.argtypes = [
    wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
]
kernel32.ReadFile.restype = wintypes.BOOL
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
kernel32.CreateJobObjectW.restype = wintypes.HANDLE
kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
kernel32.SetInformationJobObject.restype = wintypes.BOOL
kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel32.AssignProcessToJobObject.restype = wintypes.BOOL

advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
advapi32.OpenProcessToken.restype = wintypes.BOOL
advapi32.GetTokenInformation.argtypes = [
    wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
]
advapi32.GetTokenInformation.restype = wintypes.BOOL

_GetDpiForWindow = getattr(user32, "GetDpiForWindow", None)
if _GetDpiForWindow is not None:
    _GetDpiForWindow.argtypes = [wintypes.HWND]
    _GetDpiForWindow.restype = wintypes.UINT

_SetThreadDpiAwarenessContext = getattr(user32, "SetThreadDpiAwarenessContext", None)
if _SetThreadDpiAwarenessContext is not None:
    _SetThreadDpiAwarenessContext.argtypes = [ctypes.c_ssize_t]
    _SetThreadDpiAwarenessContext.restype = ctypes.c_ssize_t

_SetProcessDpiAwarenessContext = getattr(user32, "SetProcessDpiAwarenessContext", None)
if _SetProcessDpiAwarenessContext is not None:
    _SetProcessDpiAwarenessContext.argtypes = [ctypes.c_ssize_t]
    _SetProcessDpiAwarenessContext.restype = wintypes.BOOL


# ==============================================================================
# 2. TRACE RING BUFFER, PERSISTENT EVENT LOG, DPI AWARENESS & WINDOW GEOMETRY
# ==============================================================================
_TRACE_LOCK = threading.Lock()
_TRACE_RING: deque[str] = deque(maxlen=80)
_ERROR_LOG_CALLBACK = None

_EVENT_LOG_PATH: str = ""
_EVENT_LOG_MAX_BYTES = 1_048_576
_EVENT_LOG_HARD_CAP_BYTES = 4_194_304
_EVENT_QUEUE: queue.SimpleQueue = queue.SimpleQueue()
_EVENT_WRITER_LOCK = threading.Lock()
_EVENT_WRITER_STARTED = False


def set_error_log_callback(fn) -> None:
    global _ERROR_LOG_CALLBACK
    _ERROR_LOG_CALLBACK = fn


def _report_error(msg: str, exc: BaseException | None = None) -> None:
    cb = _ERROR_LOG_CALLBACK
    if callable(cb):
        try:
            cb(msg, exc)
        except Exception:
            pass


def cleanup_stale_temp_files(directory: str, prefixes: tuple[str, ...]) -> None:
    if not directory or not os.path.isdir(directory):
        return
    try:
        for name in os.listdir(directory):
            if any(name.startswith(pfx) for pfx in prefixes):
                try:
                    os.remove(os.path.join(directory, name))
                except Exception:
                    pass
    except Exception:
        pass


def write_live_diagnostics_atomic(path: str, content: str) -> bool:
    """
    Writes `content` atomically to `path` via unique sibling temp file + retryable `os.replace`
    so concurrent viewers/IDE reads never hit a half-written file or raise PermissionError.
    """
    if not path:
        return False
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = f"{path}.tmp.{os.getpid()}.{threading.get_ident()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
        for delay in (0.03, 0.05, 0.08, 0.12):
            try:
                os.replace(tmp_path, path)
                return True
            except PermissionError:
                time.sleep(delay)
            except OSError:
                time.sleep(delay)
        try:
            os.replace(tmp_path, path)
            return True
        except Exception:
            return False
    except Exception:
        return False
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def _append_event_log_batch(log_path: str, batch: list[tuple[float, str, str]]) -> None:
    if not log_path or not batch:
        return
    lines_out: list[str] = []
    for ts_epoch, level, msg in batch:
        lt = time.localtime(ts_epoch)
        ms = int((ts_epoch % 1.0) * 1000.0)
        ts_str = f"{time.strftime('%Y-%m-%d %H:%M:%S', lt)}.{ms:03d}"
        safe_msg = redact_sensitive_text(msg)
        lines_out.append(f"{ts_str} [{level}] {safe_msg}\n")

    parent = os.path.dirname(os.path.abspath(log_path))
    if parent:
        os.makedirs(parent, exist_ok=True)

    open_mode = "a"
    try:
        if os.path.exists(log_path):
            sz = os.path.getsize(log_path)
            if sz >= _EVENT_LOG_MAX_BYTES:
                rot_path = log_path + ".1"
                try:
                    os.replace(log_path, rot_path)
                except OSError:
                    if sz >= _EVENT_LOG_HARD_CAP_BYTES:
                        open_mode = "w"
    except Exception:
        pass

    try:
        with open(log_path, open_mode, encoding="utf-8") as f:
            f.writelines(lines_out)
            f.flush()
    except Exception:
        pass


def _event_writer_loop() -> None:
    while True:
        try:
            first = _EVENT_QUEUE.get(timeout=0.20)
        except queue.Empty:
            continue

        batch: list[tuple[float, str, str]] = []
        flush_waiters: list[threading.Event] = []
        items = [first]
        while len(items) < 256:
            try:
                items.append(_EVENT_QUEUE.get_nowait())
            except queue.Empty:
                break

        for ts_epoch, level, payload in items:
            if level == "__FLUSH__":
                if isinstance(payload, threading.Event):
                    flush_waiters.append(payload)
            else:
                batch.append((ts_epoch, level, str(payload)))

        path = _EVENT_LOG_PATH
        if path and batch:
            _append_event_log_batch(path, batch)

        for ev in flush_waiters:
            try:
                ev.set()
            except Exception:
                pass


def _ensure_event_writer_started() -> None:
    global _EVENT_WRITER_STARTED
    if _EVENT_WRITER_STARTED:
        return
    with _EVENT_WRITER_LOCK:
        if not _EVENT_WRITER_STARTED:
            t = threading.Thread(target=_event_writer_loop, daemon=True, name="EventLogWriter")
            t.start()
            _EVENT_WRITER_STARTED = True


def configure_event_log(path: str) -> None:
    global _EVENT_LOG_PATH
    _EVENT_LOG_PATH = str(path or "").strip()
    if _EVENT_LOG_PATH:
        parent = os.path.dirname(os.path.abspath(_EVENT_LOG_PATH))
        base = os.path.basename(_EVENT_LOG_PATH)
        diag_base = "live_diagnostics.md.tmp."
        cleanup_stale_temp_files(parent, (base + ".tmp.", diag_base))
        _ensure_event_writer_started()


def flush_event_log(timeout: float = 0.5) -> bool:
    if not _EVENT_LOG_PATH:
        return True
    _ensure_event_writer_started()
    ev = threading.Event()
    _EVENT_QUEUE.put_nowait((0.0, "__FLUSH__", ev))
    return ev.wait(timeout=max(0.05, float(timeout)))


def record_trace(event_msg: str, level: str = "INFO") -> None:
    ts_epoch = time.time()
    lt = time.localtime(ts_epoch)
    ms = int((ts_epoch % 1.0) * 1000.0)
    short_ts = f"{time.strftime('%H:%M:%S', lt)}.{ms:03d}"
    lvl = str(level or "INFO").upper()
    msg_s = str(event_msg or "")
    line = f"[{short_ts}] [{lvl}] {msg_s}" if lvl != "INFO" else f"[{short_ts}] {msg_s}"
    with _TRACE_LOCK:
        _TRACE_RING.append(line)
    if _EVENT_LOG_PATH:
        try:
            _ensure_event_writer_started()
            _EVENT_QUEUE.put_nowait((ts_epoch, lvl, msg_s))
        except Exception:
            pass


def get_recent_traces() -> list[str]:
    with _TRACE_LOCK:
        return list(_TRACE_RING)


def enable_per_monitor_dpi_awareness() -> str:
    """
    MUST be called ONCE at process startup BEFORE `tk.Tk()` so Win32 window rects,
    cursor positions, and Tk scaling share a consistent physical pixel coordinate space.
    """
    if _SetProcessDpiAwarenessContext is not None:
        try:
            if _SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2):
                return "PerMonitorV2"
        except Exception:
            pass
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
        shcore.SetProcessDpiAwareness.restype = ctypes.c_long
        if shcore.SetProcessDpiAwareness(2) == 0:
            return "PerMonitorV1"
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
        return "SystemDPI"
    except Exception:
        return "Unaware"


@contextmanager
def per_monitor_dpi_context():
    prev = 0
    if _SetThreadDpiAwarenessContext is not None:
        try:
            prev = int(_SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) or 0)
        except Exception:
            prev = 0
    try:
        yield
    finally:
        if prev and _SetThreadDpiAwarenessContext is not None:
            try:
                _SetThreadDpiAwarenessContext(ctypes.c_ssize_t(prev))
            except Exception:
                pass


def get_window_dpi(hwnd: int) -> int:
    if hwnd and _GetDpiForWindow is not None:
        try:
            dpi = int(_GetDpiForWindow(hwnd))
            if dpi > 0:
                return dpi
        except Exception:
            pass
    return 96


def apply_dark_titlebar(hwnd: int) -> None:
    if not hwnd:
        return
    try:
        val = ctypes.c_int(1)
        dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(val), ctypes.sizeof(val)
        )
    except Exception:
        pass


def get_visible_frame_rect(
    hwnd: int,
    fallback_rect: tuple[int, int, int, int],
    is_zoomed: bool = False,
) -> tuple[int, int, int, int]:
    """Returns DWM visible frame bounds `(left, top, width, height)` excluding invisible 8px zoom border."""
    if hwnd:
        try:
            rc = wintypes.RECT()
            hr = dwmapi.DwmGetWindowAttribute(
                wintypes.HWND(hwnd), DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rc), ctypes.sizeof(rc)
            )
            if hr == 0:
                vw = int(rc.right - rc.left)
                vh = int(rc.bottom - rc.top)
                if vw >= 80 and vh >= 60:
                    return (int(rc.left), int(rc.top), vw, vh)
        except Exception:
            pass
    wl, wt, ww, wh = fallback_rect
    if is_zoomed and ww > 32 and wh > 32:
        return (wl + 8, wt + 8, ww - 16, wh - 16)
    return fallback_rect


def _work_area_from_hmonitor(hmon: int) -> tuple[int, int, int, int] | None:
    if not hmon:
        return None
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
        rc = mi.rcWork
        return int(rc.left), int(rc.top), int(rc.right), int(rc.bottom)
    return None


def get_monitor_work_area(x: int, y: int) -> tuple[int, int, int, int]:
    try:
        hmon = user32.MonitorFromPoint(wintypes.POINT(int(x), int(y)), MONITOR_DEFAULTTONEAREST)
        wa = _work_area_from_hmonitor(hmon)
        if wa is not None:
            return wa
    except Exception:
        pass
    return 0, 0, 1920, 1040


def get_monitor_work_area_for_hwnd(
    hwnd: int, fallback_x: int = 0, fallback_y: int = 0
) -> tuple[int, int, int, int]:
    if hwnd and user32.IsWindow(hwnd):
        try:
            hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
            wa = _work_area_from_hmonitor(hmon)
            if wa is not None:
                return wa
        except Exception:
            pass
    return get_monitor_work_area(fallback_x, fallback_y)


def is_position_on_any_monitor(x: int, y: int) -> bool:
    try:
        pt = wintypes.POINT(int(x) + 60, int(y) + 25)
        return bool(user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONULL))
    except Exception:
        return True


def resolve_tk_toplevel_hwnd(tk_widget_id: int) -> int:
    if not tk_widget_id:
        return 0
    parent = int(user32.GetParent(tk_widget_id) or 0)
    if parent:
        return parent
    root = int(user32.GetAncestor(tk_widget_id, GA_ROOT) or 0)
    return root or int(tk_widget_id)


def make_hwnd_noactivate_transparent(hwnd: int) -> None:
    if not hwnd:
        return
    ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongPtrW(
        hwnd,
        GWL_EXSTYLE,
        ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_LAYERED | WS_EX_TRANSPARENT,
    )
    user32.SetLayeredWindowAttributes(hwnd, 0, 245, 0x00000002)


def show_hwnd_noactivate(hwnd: int, x: int, y: int, w: int, h: int) -> None:
    if not hwnd:
        return
    user32.SetWindowPos(
        hwnd, HWND_TOPMOST, int(x), int(y), max(10, int(w)), max(10, int(h)), SWP_NOACTIVATE | SWP_SHOWWINDOW
    )


def hide_hwnd_noactivate(hwnd: int) -> None:
    if hwnd and user32.IsWindow(hwnd):
        user32.ShowWindow(hwnd, SW_HIDE)


def exclude_window_from_capture(root_hwnd: int) -> bool:
    if not root_hwnd or not user32.IsWindow(root_hwnd):
        return False
    try:
        return bool(user32.SetWindowDisplayAffinity(root_hwnd, WDA_EXCLUDEFROMCAPTURE))
    except Exception:
        return False


def is_non_us_altgr_layout() -> bool:
    try:
        hkl = int(user32.GetKeyboardLayout(0) or 0)
        lang_id = hkl & 0xFFFF
        return lang_id != 0 and lang_id != 0x0409
    except Exception:
        return False


def is_primary_mouse_swapped() -> bool:
    return bool(user32.GetSystemMetrics(SM_SWAPBUTTON))


def get_primary_mouse_vk() -> int:
    return VK_RBUTTON if is_primary_mouse_swapped() else VK_LBUTTON


# ==============================================================================
# 3. TICKER VALIDATION, OCR PARSING & PRIVACY SCRUBBER
# ==============================================================================
_STRICT_TICKER_RE = re.compile(r"^([A-Z]{1,5})(?:([./\-])([A-Z]{1,2}))?$")
_TOS_TICKER_RE = re.compile(r"^([A-Za-z]{1,5})(?:([./\-])([A-Za-z]{1,2}))?$")

_EXCHANGE_NOISE = {
    "NYSE", "NASDAQ", "AMEX", "ARCA", "BATS", "OTC",
    "SHARES", "LIMIT", "MARKET", "CHART", "STOCK", "LEVEL",
}
_FREEFORM_OCR_STOPWORDS = _EXCHANGE_NOISE | {
    "USD", "BUY", "SELL", "QTY", "STOP", "BID", "ASK", "LAST", "VOL", "HIGH", "CLOSE",
    "MKT", "LMT", "STP", "GTC", "EXT", "PRE", "POST", "VWAP", "SMA", "EMA", "MACD",
    "ATR", "BOLL", "KDJ", "OBV", "SAR", "MA", "AVG", "RSI", "EXP", "ADJ", "ADJUSTED",
    "MINUTE", "MIN", "HOUR", "WEEK", "MONTH", "BBO", "NBBO", "LV2", "POINTER",
    "DEFAULT", "DEFAUL", "STYLE", "AUTO",
}
_COMPANY_SUFFIX_NOISE = {"INC", "CORP", "LTD", "LLC", "PLC", "GROUP", "HLDG", "HOLDINGS"}


def clean_ti_or_manual_ticker(raw_text: str) -> str:
    """
    Cleans raw text from Trade-Ideas External Linking or manual typing into a ticker symbol.
    Always keeps the LAST non-empty token/line so stacked TI bursts never emit an old symbol.
    """
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


def parse_valid_ticker(raw: str) -> str:
    """Strict equity/ETF ticker validator (`AAPL`, `$tsla` -> `TSLA`, `BRK/B` -> `BRK.B`)."""
    if not raw:
        return ""
    lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip()]
    if not lines:
        return ""
    cleaned = lines[0]
    if not cleaned or len(cleaned) > 12 or " " in cleaned or "\t" in cleaned:
        return ""
    if cleaned.startswith("$"):
        cleaned = cleaned[1:].strip()
    up = cleaned.upper()
    m = _STRICT_TICKER_RE.match(up)
    if not m:
        return ""
    root = m.group(1)
    if root in _EXCHANGE_NOISE:
        return ""
    suffix = m.group(3)
    return f"{root}.{suffix}" if suffix else root


def parse_tos_ticker(raw_text: str) -> str:
    """Validates a single equity/ETF ticker copied from Thinkorswim (`BRK/B` -> `BRK.B`)."""
    if not raw_text:
        return ""
    s = raw_text.strip()
    if not s or len(s) > 12 or re.search(r"\s", s):
        return ""
    if s.startswith("$"):
        s = s[1:]
    m = _TOS_TICKER_RE.match(s)
    if not m:
        return ""
    root_sym = m.group(1).upper()
    sep = m.group(2)
    suffix = m.group(3).upper() if m.group(3) else ""
    if sep and suffix:
        norm_sep = "." if sep == "/" else sep
        return f"{root_sym}{norm_sep}{suffix}"
    return root_sym


def normalize_bookmap_symbol(raw_sym: str) -> str:
    if not raw_sym:
        return ""
    s = raw_sym.strip().upper().lstrip("$")
    if "@" in s:
        s = s.split("@", 1)[0]
    if ":" in s:
        s = s.split(":", 1)[0]
    s = s.strip()
    m = re.match(r"^([A-Z0-9]{1,6})[./\-]([A-Z0-9]{1,3})$", s)
    if m:
        return f"{m.group(1)}.{m.group(2)}"
    return re.sub(r"[^A-Z0-9.]", "", s)


def parse_strict_uppercase_ticker_token(raw_tok: str) -> str:
    if not raw_tok:
        return ""
    tok = raw_tok.strip().strip("|,;:[]()\"'").lstrip("$").strip()
    if not tok or len(tok) > 8:
        return ""
    if any("a" <= ch <= "z" for ch in tok):
        return ""
    if not any("A" <= ch <= "Z" for ch in tok):
        return ""
    if tok == tok.upper():
        direct = parse_valid_ticker(tok)
        if direct:
            return direct
    if 2 <= len(tok) <= 5 and all(("A" <= ch <= "Z") or ch in ("0", "1", ".", "/") for ch in tok):
        repaired = tok.replace("0", "O").replace("1", "I")
        cand = parse_valid_ticker(repaired)
        if cand:
            return cand
    return ""


def parse_ocr_ticker_candidates(*raw_texts: str) -> str:
    for txt in raw_texts:
        if not txt:
            continue
        for line in txt.splitlines():
            for raw_t in re.split(r"[\s,|\[\]()]+", line.strip()):
                if not raw_t or raw_t.endswith(":"):
                    continue
                cand = parse_strict_uppercase_ticker_token(raw_t)
                if not cand or len(cand) < 2:
                    continue
                if cand.split(".", 1)[0] in _FREEFORM_OCR_STOPWORDS:
                    continue
                return cand
    return ""


def _point_to_rect_dist(px: float, py: float, rx: float, ry: float, rw: float, rh: float) -> float:
    dx = (rx - px) if px < rx else (px - (rx + rw) if px > rx + rw else 0.0)
    dy = (ry - py) if py < ry else (py - (ry + rh) if py > ry + rh else 0.0)
    return (dx * dx + dy * dy) ** 0.5


def select_best_ocr_word_ticker(ocr_res: dict, aim_cx: float, aim_cy: float) -> tuple[str, bool]:
    if not isinstance(ocr_res, dict) or not ocr_res.get("ok"):
        return "", False
    crop_w = float(ocr_res.get("crop_w") or 160.0)
    needs_widen = False

    def _best_from_words(words_list) -> tuple[str, float]:
        nonlocal needs_widen
        scored: list[tuple[float, str]] = []
        if not isinstance(words_list, list):
            return "", 9999.0
        for item in words_list:
            if not isinstance(item, dict):
                continue
            raw_w = str(item.get("text") or "").strip()
            if not raw_w or raw_w.endswith(":"):
                continue
            rc = item.get("rect") or [0, 0, 0, 0]
            if not (isinstance(rc, list) and len(rc) == 4):
                continue
            wx, wy, ww, wh = float(rc[0]), float(rc[1]), float(rc[2]), float(rc[3])
            if wh < 7.0:
                continue
            for st in re.split(r"[\s,|\[\]()]+", raw_w):
                if not st or st.endswith(":"):
                    continue
                cand = parse_strict_uppercase_ticker_token(st)
                if not cand or len(cand) < 2:
                    continue
                base = cand.split(".", 1)[0]
                if base in _FREEFORM_OCR_STOPWORDS or base in _COMPANY_SUFFIX_NOISE:
                    continue
                if wx <= 1.5 or (wx + ww) >= (crop_w - 1.5):
                    needs_widen = True
                    continue
                scored.append((_point_to_rect_dist(aim_cx, aim_cy, wx, wy, ww, wh), cand))
        if not scored:
            return "", 9999.0
        scored.sort(key=lambda t: (t[0], -len(t[1])))
        return scored[0][1], scored[0][0]

    cand_inv, sc_inv = _best_from_words(ocr_res.get("words_inverted"))
    cand_norm, sc_norm = _best_from_words(ocr_res.get("words_normal"))
    if cand_inv and cand_norm:
        if cand_inv == cand_norm:
            return cand_inv, needs_widen
        if cand_norm in cand_inv:
            return cand_inv, needs_widen
        if cand_inv in cand_norm:
            return cand_norm, needs_widen
        if abs(sc_inv - sc_norm) > 28.0:
            return (cand_inv if sc_inv < sc_norm else cand_norm), needs_widen
        return "", needs_widen
    if cand_inv:
        return cand_inv, needs_widen
    if cand_norm:
        return cand_norm, needs_widen
    if not needs_widen:
        return parse_ocr_ticker_candidates(
            str(ocr_res.get("text_inverted") or ""),
            str(ocr_res.get("text_normal") or ""),
        ), False
    return "", needs_widen


def select_leftmost_header_ticker(ocr_res: dict, max_left_px: float = 64.0) -> str:
    if not isinstance(ocr_res, dict) or not ocr_res.get("ok"):
        return ""

    def _first_line_leftmost(words_list) -> str:
        if not isinstance(words_list, list) or not words_list:
            return ""
        by_line: dict[int, list[dict]] = {}
        for item in words_list:
            if isinstance(item, dict):
                by_line.setdefault(int(item.get("line_idx") or 0), []).append(item)
        for li in sorted(by_line.keys()):
            row_items = sorted(by_line[li], key=lambda it: float((it.get("rect") or [9999.0])[0]))
            found_any = False
            for item in row_items:
                raw_w = str(item.get("text") or "").strip()
                if not raw_w or raw_w.endswith(":"):
                    continue
                rc = item.get("rect") or [0, 0, 0, 0]
                if not (isinstance(rc, list) and len(rc) == 4):
                    continue
                wx, _, _, wh = float(rc[0]), float(rc[1]), float(rc[2]), float(rc[3])
                for st in re.split(r"[\s,|\[\]()]+", raw_w):
                    if not st or st.endswith(":"):
                        continue
                    cand = parse_strict_uppercase_ticker_token(st)
                    if not cand or len(cand) < 2:
                        continue
                    if cand.split(".", 1)[0] in _FREEFORM_OCR_STOPWORDS:
                        continue
                    found_any = True
                    if wx <= 1.5 or wx > max_left_px or wh < 7.0:
                        return ""
                    return cand
            if found_any:
                return ""
        return ""

    w_inv = ocr_res.get("words_inverted") or []
    w_norm = ocr_res.get("words_normal") or []
    c_inv = _first_line_leftmost(w_inv)
    c_norm = _first_line_leftmost(w_norm)
    if w_inv and w_norm:
        return c_inv if (c_inv and c_norm and c_inv == c_norm) else ""
    if not w_inv and c_norm and len(c_norm) >= 3:
        return c_norm
    if not w_norm and c_inv and len(c_inv) >= 3:
        return c_inv
    return ""


def matches_dilution_tracker_title_ticker(title: str, ticker: str) -> bool:
    """
    Strict CASE-SENSITIVE check that a browser window title already shows DilutionTracker
    for `ticker` (so `'APP'` or `'IT'` NEVER falsely match `'App'` / `'it'`).
    """
    if not title or not ticker:
        return False
    esc = re.escape(ticker)
    if re.match(rf"^{esc}(?:\s*(?:[-|•:—]|\s+Dilution)|$)", title.strip()):
        return True
    if re.search(rf"(?:[-|•:—]\s*|Dilution\s*Tracker\s*[-|•:—]\s*){esc}(?:\s*[-|•:—]|\s*$)", title):
        return True
    return False


def redact_sensitive_text(text: str) -> str:
    if not text:
        return ""
    out = re.sub(r"(?i)(\\\\\?\\)?([A-Z]:[\\/]Users[\\/])([^\\/\r\n\"]+)", r"\1\2<USER>", text)
    out = re.sub(
        r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})(?![A-Za-z0-9._%+-])",
        "<EMAIL_REDACTED>",
        out,
    )
    out = re.sub(r"\b[A-Z]\d{6,10}\b", "<ACCT_REDACTED>", out)
    out = re.sub(r"\b\d{3,4}-\d{5,9}\b", "<ACCT_REDACTED>", out)
    out = re.sub(r"(?<!0x)\b\d{6,12}\b", "<NUM_REDACTED>", out)
    return out


# ==============================================================================
# 4. SHARED FILE READER, PROCESS & WINDOW DISCOVERY
# ==============================================================================
def read_shared_file_from_offset(
    path: str, offset: int, max_bytes: int = 8 * 1024 * 1024
) -> tuple[str, int]:
    """Reads complete lines using FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE."""
    h_file = kernel32.CreateFileW(
        path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, None,
    )
    if not h_file or h_file == INVALID_HANDLE_VALUE:
        return "", offset
    try:
        fsize = ctypes.c_longlong(0)
        if not kernel32.GetFileSizeEx(h_file, ctypes.byref(fsize)):
            return "", offset
        total_size = int(fsize.value)
        if total_size < offset:
            offset = 0
        if total_size <= offset:
            return "", offset

        jumped_mid = False
        to_read = total_size - offset
        if to_read > max_bytes:
            offset = total_size - max_bytes
            to_read = max_bytes
            jumped_mid = True

        if not kernel32.SetFilePointerEx(h_file, ctypes.c_longlong(offset), None, FILE_BEGIN):
            return "", offset

        chunks: list[bytes] = []
        remaining = to_read
        buf_size = min(65536, remaining)
        buf = ctypes.create_string_buffer(buf_size)
        bytes_read = wintypes.DWORD(0)
        while remaining > 0:
            ok = kernel32.ReadFile(h_file, buf, min(buf_size, remaining), ctypes.byref(bytes_read), None)
            got = int(bytes_read.value)
            if not ok or got <= 0:
                break
            chunks.append(buf.raw[:got])
            remaining -= got
        if not chunks:
            return "", offset
        raw = b"".join(chunks)
        last_nl = raw.rfind(b"\n")
        if last_nl < 0:
            return "", offset
        new_offset = offset + last_nl + 1
        complete = raw[: last_nl + 1]
        if jumped_mid:
            first_nl = complete.find(b"\n")
            if first_nl < 0:
                return "", new_offset
            complete = complete[first_nl + 1 :]
        return complete.decode("utf-8", errors="replace"), new_offset
    finally:
        kernel32.CloseHandle(h_file)


def get_own_pid() -> int:
    return int(kernel32.GetCurrentProcessId())


def get_hwnd_pid(hwnd: int) -> int:
    if not hwnd:
        return 0
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def get_pid_info_and_elevation(pid_val: int) -> tuple[str, str, bool | None]:
    if not pid_val:
        return "", "", None
    hp = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid_val)
    if not hp:
        return "", "", None
    exe_full = ""
    elev: bool | None = None
    try:
        bl = wintypes.DWORD(512)
        buf = ctypes.create_unicode_buffer(512)
        if kernel32.QueryFullProcessImageNameW(hp, 0, buf, ctypes.byref(bl)):
            exe_full = buf.value
        tok = wintypes.HANDLE()
        if advapi32.OpenProcessToken(hp, TOKEN_QUERY, ctypes.byref(tok)):
            try:
                val = wintypes.DWORD(0)
                rl = wintypes.DWORD(0)
                if advapi32.GetTokenInformation(
                    tok, TOKEN_ELEVATION_CLASS, ctypes.byref(val), ctypes.sizeof(val), ctypes.byref(rl)
                ):
                    elev = bool(val.value)
            finally:
                kernel32.CloseHandle(tok)
    finally:
        kernel32.CloseHandle(hp)
    base = os.path.basename(exe_full).lower() if exe_full else ""
    return base, exe_full, elev


def is_current_process_elevated() -> bool:
    _, _, elev = get_pid_info_and_elevation(get_own_pid())
    return bool(elev)


def get_hwnd_process_name(hwnd: int) -> str:
    pid = get_hwnd_pid(hwnd)
    if not pid:
        return ""
    base, _, _ = get_pid_info_and_elevation(pid)
    return base


def get_window_text(hwnd: int) -> str:
    if not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 2)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def get_window_class(hwnd: int) -> str:
    if not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def set_window_text(hwnd: int, text: str) -> None:
    if not hwnd:
        return
    buf = ctypes.create_unicode_buffer(text)
    user32.SendMessageW(hwnd, WM_SETTEXT, 0, ctypes.cast(buf, ctypes.c_void_p).value)


_PID_TOS_CACHE: dict[int, tuple[float, bool]] = {}
_PID_TOS_CACHE_LOCK = threading.Lock()


def is_tos_pid(pid_val: int) -> bool:
    if not pid_val:
        return False
    now = time.monotonic()
    with _PID_TOS_CACHE_LOCK:
        cached = _PID_TOS_CACHE.get(pid_val)
        if cached is not None and (now - cached[0]) < 30.0:
            return cached[1]
    exe_name, full_path, _ = get_pid_info_and_elevation(pid_val)
    fp_low = full_path.lower()
    is_tos = ("thinkorswim" in exe_name or "thinkorswim" in fp_low) and "bookmap" not in exe_name
    with _PID_TOS_CACHE_LOCK:
        if len(_PID_TOS_CACHE) > 256:
            _PID_TOS_CACHE.clear()
        _PID_TOS_CACHE[pid_val] = (now, is_tos)
    return is_tos


def is_tos_process_hwnd(hwnd: int) -> bool:
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    return is_tos_pid(get_hwnd_pid(hwnd))


def is_tos_frame_hwnd(hwnd: int) -> bool:
    if not is_tos_process_hwnd(hwnd):
        return False
    return get_window_class(hwnd).lower() == "sunawtframe"


def normalize_tos_title(title: str) -> str:
    if not title:
        return ""
    return re.sub(r"\s*\[build[^\]]*\]\s*$", "", title.strip(), flags=re.IGNORECASE)


DYNAMIC_WEBULL_TITLE_TOKEN = "<DYNAMIC_TICKER>"


def normalize_window_title(title: str, app_kind: str = "") -> str:
    if not title:
        return ""
    s = title.strip()
    if app_kind == "tos":
        s = re.sub(r"\s*\[build[^\]]*\]\s*$", "", s, flags=re.IGNORECASE).strip()
        s = re.sub(r"^.*\s-\s(?=[^-]*thinkorswim)", "", s, flags=re.IGNORECASE).strip()
        return s
    if app_kind == "webull":
        if s == DYNAMIC_WEBULL_TITLE_TOKEN:
            return DYNAMIC_WEBULL_TITLE_TOKEN
        if s == s.upper() and parse_valid_ticker(s) == s:
            return DYNAMIC_WEBULL_TITLE_TOKEN
    return s


@dataclass
class WindowInfo:
    hwnd: int
    pid: int
    exe_name: str
    exe_path: str
    class_name: str
    title: str
    rect: tuple[int, int, int, int]
    dpi: int
    is_iconic: bool
    is_zoomed: bool
    is_elevated: bool | None
    has_owner: bool
    is_topmost: bool = False


def get_window_rect_or_restored(hwnd: int) -> tuple[int, int, int, int]:
    if user32.IsIconic(hwnd):
        wp = WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(WINDOWPLACEMENT)
        if user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
            rc = wp.rcNormalPosition
            w = max(0, int(rc.right - rc.left))
            h = max(0, int(rc.bottom - rc.top))
            if w >= 140 and h >= 90:
                return (int(rc.left), int(rc.top), w, h)
        return (0, 0, 1280, 800)
    rc = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rc))
    return (int(rc.left), int(rc.top), max(0, int(rc.right - rc.left)), max(0, int(rc.bottom - rc.top)))


def classify_exe_and_class(
    exe_name: str, exe_path: str, class_name: str, *, include_webull: bool = False
) -> str:
    exe = exe_name.lower()
    path_low = exe_path.lower()
    cls = class_name.lower()
    if ("thinkorswim" in exe or "thinkorswim" in path_low) and "bookmap" not in exe:
        return "tos" if cls == "sunawtframe" else ""
    if include_webull and ("webull" in exe or "webull" in path_low):
        if any(bad in exe for bad in ("crashpad", "helper", "update", "setup")):
            return ""
        return "webull"
    if "bookmap" in exe:
        return "bookmap" if cls == "sunawtframe" else ""
    return ""


def probe_cursor_target_fast(
    pid_cache: dict[int, tuple[str, str]],
    *,
    include_webull: bool = False,
) -> tuple[int, int, int, str, str, str]:
    pt = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return 0, 0, 0, "", "", ""
    hit = user32.WindowFromPoint(pt)
    root = int(user32.GetAncestor(hit, GA_ROOT) or 0) if hit else 0
    if not root:
        return int(pt.x), int(pt.y), 0, "", "", ""

    pid = get_hwnd_pid(root)
    cached = pid_cache.get(pid)
    if cached is None:
        exe_base, exe_full, _ = get_pid_info_and_elevation(pid)
        cached = (exe_base, exe_full)
        if len(pid_cache) > 256:
            pid_cache.clear()
        pid_cache[pid] = cached
    exe_base, exe_full = cached
    cls = get_window_class(root)
    title = get_window_text(root).strip()
    kind = classify_exe_and_class(exe_base, exe_full, cls, include_webull=include_webull)
    return int(pt.x), int(pt.y), root, kind, title, exe_base


def enum_visible_top_windows() -> list[WindowInfo]:
    own_pid = get_own_pid()
    items: list[WindowInfo] = []

    def _cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = get_hwnd_pid(hwnd)
        if not pid or pid == own_pid:
            return True
        rect_t = get_window_rect_or_restored(hwnd)
        if rect_t[2] < 140 or rect_t[3] < 90:
            return True
        title = get_window_text(hwnd).strip()
        if not title:
            return True
        cls_name = get_window_class(hwnd)
        exe_base, exe_full, elev = get_pid_info_and_elevation(pid)
        owner = user32.GetWindow(hwnd, GW_OWNER)
        ex_st = int(user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) or 0)
        items.append(
            WindowInfo(
                hwnd=int(hwnd),
                pid=pid,
                exe_name=exe_base,
                exe_path=exe_full,
                class_name=cls_name,
                title=title,
                rect=rect_t,
                dpi=get_window_dpi(hwnd),
                is_iconic=bool(user32.IsIconic(hwnd)),
                is_zoomed=bool(user32.IsZoomed(hwnd)),
                is_elevated=elev,
                has_owner=bool(owner),
                is_topmost=bool(ex_st & WS_EX_TOPMOST),
            )
        )
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return items


def find_target_window(
    kind: str,
    saved_title: str,
    saved_rect: list | None = None,
    *,
    label: str = "",
    size_tol: int = 6,
    all_windows: list[WindowInfo] | None = None,
    include_webull: bool = False,
) -> tuple[WindowInfo | None, str]:
    lbl = label or kind.upper()
    norm_saved = normalize_window_title(str(saved_title or ""), kind)
    wins = all_windows if all_windows is not None else enum_visible_top_windows()
    cands = [
        w for w in wins
        if classify_exe_and_class(w.exe_name, w.exe_path, w.class_name, include_webull=include_webull) == kind
    ]
    if not cands:
        return None, f"{lbl} window is not open"

    if norm_saved:
        exact = [
            w for w in cands
            if normalize_window_title(w.title, kind).lower() == norm_saved.lower()
        ]
        if len(exact) > 1:
            non_ic = [w for w in exact if not w.is_iconic]
            if non_ic:
                exact = non_ic
        if len(exact) == 1:
            return exact[0], ""
        if len(exact) > 1 and isinstance(saved_rect, list) and len(saved_rect) == 4:
            sz_m = [
                w for w in exact
                if abs(w.rect[2] - int(saved_rect[2])) <= size_tol
                and abs(w.rect[3] - int(saved_rect[3])) <= size_tol
            ]
            if len(sz_m) == 1:
                return sz_m[0], ""
            pos_m = [
                w for w in (sz_m or exact)
                if abs(w.rect[0] - int(saved_rect[0])) <= 24
                and abs(w.rect[1] - int(saved_rect[1])) <= 24
            ]
            if len(pos_m) == 1:
                return pos_m[0], ""
            return None, f"Multiple {lbl} windows match — re-pick in ⚙"
        if len(exact) > 1:
            return None, f"Multiple {lbl} windows match — re-pick in ⚙"

    if kind == "tos" and not norm_saved:
        return None, "Pick Main TOS Box in ⚙ first"

    active = [w for w in cands if not w.is_iconic] or cands
    unowned = [w for w in active if not w.has_owner and w.title]
    pool = unowned or active
    if len(pool) == 1:
        return pool[0], ""
    pool.sort(key=lambda w: w.rect[2] * w.rect[3], reverse=True)
    if len(pool) >= 2:
        a0 = pool[0].rect[2] * pool[0].rect[3]
        a1 = pool[1].rect[2] * pool[1].rect[3]
        if a1 > 0.65 * a0:
            return None, f"Multiple {lbl} windows open — pick target in ⚙"
    return pool[0], ""


def is_rect_unoccluded_for_hwnd(
    hwnd: int, x: int, y: int, w: int, h: int, allow_own_pid: bool = True
) -> tuple[bool, str]:
    if not hwnd or w <= 4 or h <= 4:
        return False, "invalid rect"
    own_pid = get_own_pid() if allow_own_pid else -1
    pts = (
        (int(x + w // 2), int(y + h // 2)),
        (int(x + 3), int(y + 3)),
        (int(x + max(3, w - 4)), int(y + 3)),
        (int(x + 3), int(y + max(3, h - 4))),
        (int(x + max(3, w - 4)), int(y + max(3, h - 4))),
    )
    for px, py in pts:
        hit = user32.WindowFromPoint(wintypes.POINT(px, py))
        root = int(user32.GetAncestor(hit, GA_ROOT) or 0) if hit else 0
        if root == hwnd:
            continue
        if root and own_pid > 0 and get_hwnd_pid(root) == own_pid:
            continue
        cov_cls = get_window_class(root) if root else "Desktop"
        cov_t = get_window_text(root)[:28] if root else ""
        return False, f"{cov_cls}({cov_t})" if cov_t else cov_cls
    return True, ""


def get_enabled_popup_info(hwnd: int) -> tuple[int, str, str]:
    if not hwnd or not user32.IsWindow(hwnd):
        return 0, "", ""
    popup = int(user32.GetWindow(hwnd, GW_ENABLEDPOPUP) or 0)
    if popup and popup != hwnd and user32.IsWindowVisible(popup):
        return popup, get_window_class(popup), get_window_text(popup).strip()
    return 0, "", ""


_MODAL_BLOCK_KEYWORDS = (
    "dialog", "#32770", "confirm", "preview", "order", "status", "message",
    "error", "warning", "alert", "verify", "notice", "disclosure",
    "agreement", "login", "timeout",
)


def has_modal_or_popup(hwnd: int, app_kind: str = "") -> bool:
    if not hwnd or not user32.IsWindow(hwnd):
        return True
    if not user32.IsWindowEnabled(hwnd):
        return True
    popup = int(user32.GetWindow(hwnd, GW_ENABLEDPOPUP) or 0)
    if popup and popup != hwnd and user32.IsWindowVisible(popup):
        if app_kind == "webull":
            p_cls = get_window_class(popup).lower()
            p_title = get_window_text(popup).strip().lower()
            prc = wintypes.RECT()
            user32.GetWindowRect(popup, ctypes.byref(prc))
            pw, ph = int(prc.right - prc.left), int(prc.bottom - prc.top)
            if pw < 380 or ph < 260 or any(kw in p_cls or kw in p_title for kw in _MODAL_BLOCK_KEYWORDS):
                return True
        else:
            return True
    if app_kind == "tos":
        pid = get_hwnd_pid(hwnd)
        found_dialog = False

        def _chk(h, _):
            nonlocal found_dialog
            if not user32.IsWindowVisible(h):
                return True
            if get_hwnd_pid(h) == pid:
                cls = get_window_class(h).lower()
                if "dialog" in cls or "#32770" in cls:
                    found_dialog = True
                    return False
            return True

        user32.EnumWindows(WNDENUMPROC(_chk), 0)
        if found_dialog:
            return True
    return False


def snapshot_process_popups(
    owner_hwnd: int, owner_pid: int
) -> dict[int, tuple[str, str, tuple[int, int, int, int]]]:
    out: dict[int, tuple[str, str, tuple[int, int, int, int]]] = {}
    if not owner_hwnd:
        return out

    def _cb(h, _):
        ih = int(h)
        if ih == owner_hwnd or not user32.IsWindowVisible(h):
            return True
        h_owner = int(user32.GetWindow(h, GW_OWNER) or 0)
        h_pid = get_hwnd_pid(ih)
        if h_owner == owner_hwnd or (owner_pid and h_pid == owner_pid):
            rc = wintypes.RECT()
            user32.GetWindowRect(h, ctypes.byref(rc))
            w, ht = max(0, int(rc.right - rc.left)), max(0, int(rc.bottom - rc.top))
            if w >= 8 and ht >= 8:
                out[ih] = (get_window_class(ih), get_window_text(ih).strip(), (int(rc.left), int(rc.top), w, ht))
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return out


def is_verified_autocomplete_popup(
    cand_hwnd: int,
    owner_hwnd: int,
    owner_pid: int,
    pre_click_hwnds: set[int],
    click_x: int,
    click_y: int,
) -> tuple[bool, str]:
    if not cand_hwnd or cand_hwnd == owner_hwnd or cand_hwnd in pre_click_hwnds:
        return False, "pre-existing or invalid"
    if not user32.IsWindowVisible(cand_hwnd):
        return False, "not visible"
    if owner_pid and get_hwnd_pid(cand_hwnd) != owner_pid:
        return False, "pid mismatch"
    cls = get_window_class(cand_hwnd)
    cls_low = cls.lower()
    if any(b in cls_low for b in ("dialog", "#32770", "messagebox", "tooltip")):
        return False, f"disallowed class '{cls}'"
    st = int(user32.GetWindowLongPtrW(cand_hwnd, GWL_STYLE) or 0)
    if (st & WS_CAPTION) == WS_CAPTION:
        return False, f"has WS_CAPTION '{cls}'"
    if not ("qwindowpopup" in cls_low or cls_low == "sunawtwindow"):
        return False, f"unrecognized popup '{cls}'"
    title = get_window_text(cand_hwnd).strip()
    if title and not ("qwindowpopup" in cls_low and title.lower() == "webull desktop"):
        return False, f"non-empty title '{title[:20]}'"
    rc = wintypes.RECT()
    if not user32.GetWindowRect(cand_hwnd, ctypes.byref(rc)):
        return False, "no rect"
    pl, pt, pr, pb = int(rc.left), int(rc.top), int(rc.right), int(rc.bottom)
    pw, ph = pr - pl, pb - pt
    dpi_sc = max(1.0, float(get_window_dpi(owner_hwnd) or 96) / 96.0)
    if not (int(44 * dpi_sc) <= pw <= int(480 * dpi_sc) and int(14 * dpi_sc) <= ph <= int(600 * dpi_sc)):
        return False, f"size {pw}x{ph} outside bounds"
    horiz_ok = ((pl - int(45 * dpi_sc)) <= click_x <= (pr + int(130 * dpi_sc))) and (
        abs(pl - click_x) <= int(280 * dpi_sc)
    )
    vert_below = (click_y - int(32 * dpi_sc)) <= pt <= (click_y + int(115 * dpi_sc))
    vert_above = (click_y - int(32 * dpi_sc)) <= pb <= (click_y + int(45 * dpi_sc))
    if not (horiz_ok and (vert_below or vert_above)):
        return False, f"pos ({pl},{pt},{pw}x{ph}) not anchored"
    return True, f"{cls}@({pl},{pt},{pw}x{ph})"


def inspect_new_process_popups(
    owner_hwnd: int, owner_pid: int, pre_click_hwnds: set[int], click_x: int, click_y: int
) -> tuple[str, str, tuple[int, int, int, int] | None]:
    if not user32.IsWindow(owner_hwnd) or not user32.IsWindowEnabled(owner_hwnd):
        return "blocking", "owner window disabled", None
    cur_map = snapshot_process_popups(owner_hwnd, owner_pid)
    dropdowns: list[tuple[str, tuple[int, int, int, int]]] = []
    for h, (cls, _title, rect_t) in cur_map.items():
        if h in pre_click_hwnds:
            continue
        cl = cls.lower()
        if "tooltip" in cl or cl in ("tooltips_class32", "msctfime ui", "ime"):
            continue
        ok_dd, why = is_verified_autocomplete_popup(
            h, owner_hwnd, owner_pid, pre_click_hwnds, click_x, click_y
        )
        if ok_dd:
            dropdowns.append((why, rect_t))
        else:
            return "blocking", f"unexpected window {cls} ({why})", None
    if len(dropdowns) > 1:
        return "blocking", f"multiple popups ({len(dropdowns)})", None
    if len(dropdowns) == 1:
        return "dropdown", dropdowns[0][0], dropdowns[0][1]
    return "none", "", None


# ==============================================================================
# 5. FOCUS WITHOUT ALT (NEVER SENDS VK_MENU) & PHYSICAL INPUT SETTLE
# ==============================================================================
def _tap_unassigned_focus_vk() -> None:
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki = KEYBDINPUT(wVk=VK_UNASSIGNED_FOCUS, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def _is_any_alt_down() -> bool:
    return any(
        (user32.GetAsyncKeyState(vk) & 0x8000) != 0
        for vk in (VK_MENU, VK_LMENU, VK_RMENU)
    )


def _is_any_win_down() -> bool:
    return any(
        (user32.GetAsyncKeyState(vk) & 0x8000) != 0
        for vk in (VK_LWIN, VK_RWIN)
    )


def tap_menu_mask_vk_if_held(hk_mods: int) -> None:
    """
    Executes directly on the hotkey owner thread when an Alt- or Win-modified
    `RegisterHotKey` arrives: injects an atomic [0xE8 DOWN, 0xE8 UP] pair while
    Alt/Win is still held so Windows never fires `SC_KEYMENU` (Bookmap/TOS menu bar)
    or Start Menu on modifier key-up. NEVER sends `VK_MENU`.
    """
    need_mask = ((hk_mods & MOD_ALT) and _is_any_alt_down()) or (
        (hk_mods & MOD_WIN) and _is_any_win_down()
    )
    if not need_mask:
        return
    arr = (INPUT * 2)()
    arr[0].type = INPUT_KEYBOARD
    arr[0].union.ki = KEYBDINPUT(wVk=VK_MENU_MASK, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)
    arr[1].type = INPUT_KEYBOARD
    arr[1].union.ki = KEYBDINPUT(wVk=VK_MENU_MASK, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)
    user32.SendInput(2, arr, ctypes.sizeof(INPUT))


def _force_focus_once(hwnd: int) -> bool:
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        res = ULONG_PTR(0)
        user32.SendMessageTimeoutW(hwnd, WM_SYSCOMMAND, SC_RESTORE, 0, SMTO_ABORTIFHUNG, 250, ctypes.byref(res))
        user32.ShowWindow(hwnd, SW_RESTORE)
        t_res = time.perf_counter()
        while user32.IsIconic(hwnd) and (time.perf_counter() - t_res) < 0.22:
            time.sleep(0.015)
        time.sleep(0.05)

    fg = user32.GetForegroundWindow()
    if fg == hwnd and not user32.IsIconic(hwnd):
        return True

    cur_tid = kernel32.GetCurrentThreadId()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    tgt_tid = user32.GetWindowThreadProcessId(hwnd, None)

    old_timeout = wintypes.DWORD(0)
    timeout_zeroed = False
    if user32.SystemParametersInfoW(SPI_GETFOREGROUNDLOCKTIMEOUT, 0, ctypes.byref(old_timeout), 0):
        if user32.SystemParametersInfoW(SPI_SETFOREGROUNDLOCKTIMEOUT, 0, None, 0):
            timeout_zeroed = True

    att_fg = False
    att_tgt = False
    try:
        if fg_tid and fg_tid != cur_tid:
            att_fg = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
        if tgt_tid and tgt_tid != cur_tid and tgt_tid != fg_tid:
            att_tgt = bool(user32.AttachThreadInput(cur_tid, tgt_tid, True))

        _tap_unassigned_focus_vk()
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(hwnd)
    finally:
        if att_tgt:
            user32.AttachThreadInput(cur_tid, tgt_tid, False)
        if att_fg:
            user32.AttachThreadInput(cur_tid, fg_tid, False)
        if timeout_zeroed:
            user32.SystemParametersInfoW(
                SPI_SETFOREGROUNDLOCKTIMEOUT, 0, ctypes.c_void_p(old_timeout.value), 0
            )
    time.sleep(0.02)
    return bool(user32.GetForegroundWindow() == hwnd and not user32.IsIconic(hwnd))


def focus_window_no_alt(hwnd: int, max_attempts: int = 12) -> bool:
    for i in range(max_attempts):
        if _force_focus_once(hwnd):
            return True
        time.sleep(0.025 + i * 0.008)
    return bool(user32.GetForegroundWindow() == hwnd and not user32.IsIconic(hwnd))


def wait_for_stable_foreground(hwnd: int, settle_ms: int = 90, max_total_ms: int = 650) -> bool:
    t0 = time.perf_counter()
    stable_since = None
    req_sec = max(0.02, settle_ms / 1000.0)
    budget_sec = max(req_sec + 0.05, max_total_ms / 1000.0)
    while (time.perf_counter() - t0) < budget_sec:
        if user32.GetForegroundWindow() == hwnd and not user32.IsIconic(hwnd):
            if stable_since is None:
                stable_since = time.perf_counter()
            elif (time.perf_counter() - stable_since) >= req_sec:
                return True
        else:
            stable_since = None
            _force_focus_once(hwnd)
        time.sleep(0.015)
    return bool(user32.GetForegroundWindow() == hwnd and not user32.IsIconic(hwnd))


def resolve_z_order_context(
    fg_hwnd: int,
    own_root_hwnd: int,
    all_windows: list[WindowInfo] | None = None,
    *,
    include_webull: bool = False,
) -> tuple[WindowInfo | None, int]:
    wins = all_windows if all_windows is not None else enum_visible_top_windows()
    by_hwnd = {w.hwnd: w for w in wins}
    own_pid = get_own_pid()

    if fg_hwnd and fg_hwnd != own_root_hwnd:
        fg_win = by_hwnd.get(fg_hwnd)
        if fg_win is not None and not fg_win.is_iconic:
            if classify_exe_and_class(fg_win.exe_name, fg_win.exe_path, fg_win.class_name, include_webull=include_webull) in ("tos", "webull"):
                return fg_win, fg_hwnd

    restore_hwnd = (
        fg_hwnd
        if (fg_hwnd and fg_hwnd != own_root_hwnd and user32.IsWindowVisible(fg_hwnd) and not user32.IsIconic(fg_hwnd))
        else 0
    )
    top_trading: WindowInfo | None = None
    cur = int(user32.GetTopWindow(None) or 0)
    steps = 0
    while cur and steps < 400:
        steps += 1
        if cur != own_root_hwnd:
            w = by_hwnd.get(cur)
            if w is not None and not w.is_iconic and w.pid != own_pid:
                if restore_hwnd == 0 and not w.has_owner:
                    restore_hwnd = w.hwnd
                if top_trading is None:
                    if classify_exe_and_class(w.exe_name, w.exe_path, w.class_name, include_webull=include_webull) in ("tos", "webull"):
                        top_trading = w
                        if restore_hwnd != 0:
                            break
        cur = int(user32.GetWindow(cur, GW_HWNDNEXT) or 0)
    return top_trading, restore_hwnd


def wait_for_input_settled(
    max_wait_sec: float = 1.20,
    extra_vks: tuple[int, ...] | list[int] = (),
    on_waiting: object | None = None,
) -> bool:
    """
    Waits until physical modifiers, mouse buttons, and `extra_vks` are released.
    Returns immediately with 0ms sleep when nothing is physically held; NEVER sends VK_MENU.
    """
    watched = (
        VK_MENU, VK_LMENU, VK_RMENU,
        VK_CONTROL, VK_LCONTROL, VK_RCONTROL,
        VK_SHIFT, VK_LSHIFT, VK_RSHIFT,
        VK_LWIN, VK_RWIN,
        VK_LBUTTON, VK_RBUTTON,
    ) + tuple(extra_vks)

    if not any((user32.GetAsyncKeyState(vk) & 0x8000) != 0 for vk in watched):
        return True

    notified = False
    t0 = time.perf_counter()
    while (time.perf_counter() - t0) < max_wait_sec:
        if not any((user32.GetAsyncKeyState(vk) & 0x8000) != 0 for vk in watched):
            time.sleep(0.015)
            if not any((user32.GetAsyncKeyState(vk) & 0x8000) != 0 for vk in watched):
                return True
        elif not notified and callable(on_waiting) and (time.perf_counter() - t0) >= 0.22:
            notified = True
            try:
                on_waiting()
            except Exception:
                pass
        time.sleep(0.015)
    return False


# ==============================================================================
# 6. ATOMIC SENDINPUT ENGINE & STRICT TOS FOREGROUND SAFETY GUARD
# ==============================================================================
class FocusLostError(RuntimeError):
    pass


_KEY_AUDIT_HOOK = None


def set_key_audit_hook(fn) -> None:
    """Test-only inspection hook called with `(op_label, vk, mod_vks, fg_hwnd)` before key dispatch."""
    global _KEY_AUDIT_HOOK
    _KEY_AUDIT_HOOK = fn


def _emit_audit(op_label: str, vk: int, mod_vks: tuple[int, ...], fg_hwnd: int) -> None:
    cb = _KEY_AUDIT_HOOK
    if callable(cb):
        cb(op_label, vk, mod_vks, fg_hwnd)


def _make_key_input(vk: int, key_up: bool = False, extended: bool = False) -> INPUT:
    scan = int(user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC))
    flags = KEYEVENTF_KEYUP if key_up else 0
    if scan:
        flags |= KEYEVENTF_SCANCODE
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki = KEYBDINPUT(
        wVk=0 if (scan and not extended) else vk,
        wScan=scan,
        dwFlags=flags,
        time=0,
        dwExtraInfo=0,
    )
    return inp


def _make_key_vk_event(vk: int, key_up: bool = False, extended: bool = False) -> INPUT:
    """Scan-code + VK paired event for held chords (`Ctrl+<key>`) so Java Swing & browsers both accept it."""
    scan = int(user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)) & 0xFF
    flags = (KEYEVENTF_KEYUP if key_up else 0) | (KEYEVENTF_EXTENDEDKEY if extended else 0)
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki = KEYBDINPUT(wVk=vk & 0xFF, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return inp


def release_modifiers_emergency() -> None:
    """Sends atomic KEYUP for Shift, Ctrl, and Win keys (NEVER VK_MENU / Alt)."""
    vks = (
        (VK_SHIFT, False), (VK_LSHIFT, False), (VK_RSHIFT, True),
        (VK_CONTROL, False), (VK_LCONTROL, False), (VK_RCONTROL, True),
        (VK_LWIN, True), (VK_RWIN, True),
    )
    arr = (INPUT * len(vks))()
    for idx, (vk, ext) in enumerate(vks):
        arr[idx] = _make_key_input(vk, key_up=True, extended=ext)
    user32.SendInput(len(vks), arr, ctypes.sizeof(INPUT))


class HardwareInputGuard:
    def __init__(self) -> None:
        self.blocked_ok: bool = False

    def __enter__(self) -> "HardwareInputGuard":
        try:
            self.blocked_ok = bool(user32.BlockInput(True))
        except Exception:
            self.blocked_ok = False
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        try:
            release_modifiers_emergency()
        except Exception:
            pass
        if self.blocked_ok:
            try:
                user32.BlockInput(False)
            except Exception:
                pass


def _verify_expected_foreground(
    expected_hwnd: int,
    label: str,
    allow_popup_ctx: tuple[int, set[int], int, int] | None = None,
) -> None:
    if not expected_hwnd:
        return
    fg = int(user32.GetForegroundWindow() or 0)
    if fg == expected_hwnd:
        return
    if allow_popup_ctx is not None and fg:
        owner_pid, pre_hwnds, cx, cy = allow_popup_ctx
        ok_dd, _ = is_verified_autocomplete_popup(fg, expected_hwnd, owner_pid, pre_hwnds, cx, cy)
        if ok_dd:
            return
    raise FocusLostError(f"Foreground left target window before {label}")


def send_vk_tap_atomic(
    vk: int,
    extended: bool = False,
    expected_hwnd: int = 0,
    label: str = "key",
    allow_popup_ctx: tuple[int, set[int], int, int] | None = None,
) -> float:
    _verify_expected_foreground(expected_hwnd, label, allow_popup_ctx=allow_popup_ctx)
    fg = int(user32.GetForegroundWindow() or 0)
    _emit_audit(label, vk, (), fg)
    arr = (INPUT * 2)(
        _make_key_input(vk, key_up=False, extended=extended),
        _make_key_input(vk, key_up=True, extended=extended),
    )
    sent = int(user32.SendInput(2, arr, ctypes.sizeof(INPUT)))
    if sent != 2:
        raise RuntimeError(f"SendInput failed for {label} (sent={sent}/2)")
    return time.perf_counter()


def send_vk_chord_atomic(
    mod_vk: int,
    key_vk: int,
    key_extended: bool = False,
    expected_hwnd: int = 0,
    label: str = "chord",
    allow_popup_ctx: tuple[int, set[int], int, int] | None = None,
) -> float:
    """
    Atomic [MOD_DOWN, KEY_DOWN, KEY_UP, MOD_UP] `SendInput(4, ...)` for SAFE
    navigation/clipboard keys ONLY (`Insert`, `End`, `Home`, `Delete`, `Tab`).
    """
    if key_vk not in (VK_INSERT, VK_END, VK_HOME, VK_DELETE, VK_TAB):
        raise ValueError(f"Refusing modifier chord with disallowed VK={hex(key_vk)}")
    _verify_expected_foreground(expected_hwnd, label, allow_popup_ctx=allow_popup_ctx)
    fg = int(user32.GetForegroundWindow() or 0)
    _emit_audit(label, key_vk, (mod_vk,), fg)
    arr = (INPUT * 4)(
        _make_key_input(mod_vk, key_up=False, extended=False),
        _make_key_input(key_vk, key_up=False, extended=key_extended),
        _make_key_input(key_vk, key_up=True, extended=key_extended),
        _make_key_input(mod_vk, key_up=True, extended=False),
    )
    sent = int(user32.SendInput(4, arr, ctypes.sizeof(INPUT)))
    if sent != 4:
        raise RuntimeError(f"SendInput failed for {label} (sent={sent}/4)")
    return time.perf_counter()


def send_ctrl_vk_guarded(
    vk: int,
    *,
    shift: bool = False,
    hold_sec: float = 0.032,
    expected_hwnd: int = 0,
    label: str = "",
) -> None:
    """
    Universal guarded `Ctrl+[Shift+]<vk>` sender for Bookmap & Browser ONLY:
      - INVARIANT #3: Refuses to run (before Ctrl down AND before `<vk>` down) if
        Thinkorswim owns foreground, regardless of `shift`!
      - Verifies `GetForegroundWindow() == expected_hwnd` when specified.
    """
    desc = label or f"VK_{vk:#04x}"
    fg_pre = int(user32.GetForegroundWindow() or 0)
    if fg_pre and is_tos_process_hwnd(fg_pre):
        _report_error(f"Safety guard blocked send_ctrl_vk_guarded({desc}): Thinkorswim owns foreground {hex(fg_pre)}")
        raise RuntimeError("Blocked Ctrl shortcut: Thinkorswim owns foreground")
    if expected_hwnd and fg_pre != expected_hwnd:
        raise FocusLostError(f"Target lost foreground before {desc} (fg={hex(fg_pre)}, exp={hex(expected_hwnd)})")

    mods = (VK_CONTROL, VK_SHIFT) if shift else (VK_CONTROL,)
    _emit_audit(desc, vk, mods, fg_pre)

    def _send_one(inp_obj: INPUT) -> None:
        user32.SendInput(1, ctypes.byref(inp_obj), ctypes.sizeof(INPUT))

    _send_one(_make_key_vk_event(VK_CONTROL, key_up=False))
    try:
        if shift:
            _send_one(_make_key_vk_event(VK_SHIFT, key_up=False))
        try:
            time.sleep(hold_sec)
            fg_mid = int(user32.GetForegroundWindow() or 0)
            if fg_mid and is_tos_process_hwnd(fg_mid):
                _report_error(f"Safety guard blocked keydown({desc}): Thinkorswim stole foreground {hex(fg_mid)}")
                raise RuntimeError("Blocked Ctrl shortcut: Thinkorswim stole foreground")
            if expected_hwnd and fg_mid != expected_hwnd:
                raise FocusLostError(f"Target lost foreground mid-{desc} (fg={hex(fg_mid)}, exp={hex(expected_hwnd)})")
            _send_one(_make_key_vk_event(vk, key_up=False))
            try:
                time.sleep(hold_sec)
            finally:
                _send_one(_make_key_vk_event(vk, key_up=True))
            time.sleep(max(0.015, hold_sec * 0.55))
        finally:
            if shift:
                _send_one(_make_key_vk_event(VK_SHIFT, key_up=True))
    finally:
        _send_one(_make_key_vk_event(VK_CONTROL, key_up=True))


def send_ctrl_key_guarded(key_char: str, hold_sec: float = 0.035, expected_hwnd: int = 0) -> None:
    ch = key_char.upper()
    send_ctrl_vk_guarded(ord(ch[0]), shift=False, hold_sec=hold_sec, expected_hwnd=expected_hwnd, label=f"Ctrl+{ch[0]}")


def send_enter_reliable(hold_sec: float = 0.035, expected_hwnd: int = 0) -> None:
    fg = int(user32.GetForegroundWindow() or 0)
    if expected_hwnd and fg != expected_hwnd:
        raise FocusLostError("Foreground left target window before Enter")
    _emit_audit("Enter", VK_RETURN, (), fg)
    inp_d = _make_key_vk_event(VK_RETURN, key_up=False)
    user32.SendInput(1, ctypes.byref(inp_d), ctypes.sizeof(INPUT))
    try:
        time.sleep(hold_sec)
    finally:
        inp_u = _make_key_vk_event(VK_RETURN, key_up=True)
        user32.SendInput(1, ctypes.byref(inp_u), ctypes.sizeof(INPUT))


def send_escape_reliable(hold_sec: float = 0.025) -> None:
    fg = int(user32.GetForegroundWindow() or 0)
    _emit_audit("Escape", VK_ESCAPE, (), fg)
    inp_d = _make_key_vk_event(VK_ESCAPE, key_up=False)
    user32.SendInput(1, ctypes.byref(inp_d), ctypes.sizeof(INPUT))
    try:
        time.sleep(hold_sec)
    finally:
        inp_u = _make_key_vk_event(VK_ESCAPE, key_up=True)
        user32.SendInput(1, ctypes.byref(inp_u), ctypes.sizeof(INPUT))


def click_screen_point_safe(
    x: int,
    y: int,
    expected_hwnd: int,
    restore_cursor: bool = True,
    allow_popup_ctx: tuple[int, set[int], int, int] | None = None,
) -> None:
    """
    Moves cursor to `(x, y)`, verifies `WindowFromPoint` root belongs to `expected_hwnd`
    (when not a verified popup), handles `SM_SWAPBUTTON`, clicks via `SendInput`,
    and immediately restores prior cursor in `try/finally`.
    """
    _verify_expected_foreground(expected_hwnd, "mouse click", allow_popup_ctx=allow_popup_ctx)
    hit = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
    hit_root = int(user32.GetAncestor(hit, GA_ROOT) or 0) if hit else 0
    if hit_root != expected_hwnd:
        if allow_popup_ctx is None or not is_verified_autocomplete_popup(
            hit_root, expected_hwnd, allow_popup_ctx[0], allow_popup_ctx[1], int(x), int(y)
        )[0]:
            raise FocusLostError("Target symbol box is covered by another window")

    orig_pt = wintypes.POINT()
    has_orig = bool(user32.GetCursorPos(ctypes.byref(orig_pt)))
    swapped = is_primary_mouse_swapped()
    down_flag = MOUSEEVENTF_RIGHTDOWN if swapped else MOUSEEVENTF_LEFTDOWN
    up_flag = MOUSEEVENTF_RIGHTUP if swapped else MOUSEEVENTF_LEFTUP

    user32.SetCursorPos(int(x), int(y))
    try:
        time.sleep(0.022)
        _verify_expected_foreground(expected_hwnd, "mouse down", allow_popup_ctx=allow_popup_ctx)
        inp_down = INPUT()
        inp_down.type = INPUT_MOUSE
        inp_down.union.mi = MOUSEINPUT(0, 0, 0, down_flag, 0, 0)
        user32.SendInput(1, ctypes.byref(inp_down), ctypes.sizeof(INPUT))
        time.sleep(0.030)
        inp_up = INPUT()
        inp_up.type = INPUT_MOUSE
        inp_up.union.mi = MOUSEINPUT(0, 0, 0, up_flag, 0, 0)
        user32.SendInput(1, ctypes.byref(inp_up), ctypes.sizeof(INPUT))
        time.sleep(0.035)
    finally:
        if restore_cursor and has_orig:
            user32.SetCursorPos(orig_pt.x, orig_pt.y)


# ==============================================================================
# 7. PRIVACY-SAFE PURE-WIN32 CLIPBOARD (WIN+V HISTORY EXCLUDED)
# ==============================================================================
_CF_EXCLUDE_MONITOR = user32.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing")
_CF_CAN_INCLUDE_HIST = user32.RegisterClipboardFormatW("CanIncludeInClipboardHistory")


def read_clipboard_unicode(
    max_bytes: int = 16384, retries: int = 10, retry_sleep: float = 0.010
) -> tuple[bool, str | None, int, bool]:
    """
    Returns `(opened_ok, text_or_none, owner_pid, is_text_format)`.
    `is_text_format` is False when the clipboard currently holds non-text data (images/files)
    so callers never destroy non-text clipboards on restore.
    """
    owner_hwnd = user32.GetClipboardOwner()
    owner_pid = get_hwnd_pid(owner_hwnd) if owner_hwnd else 0
    if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return True, None, owner_pid, False

    opened = False
    for i in range(max(1, retries)):
        if user32.OpenClipboard(None):
            opened = True
            break
        if i + 1 < retries:
            time.sleep(retry_sleep)
    if not opened:
        return False, None, owner_pid, True

    try:
        h_data = user32.GetClipboardData(CF_UNICODETEXT)
        if not h_data:
            return True, None, owner_pid, True
        bsize = int(kernel32.GlobalSize(h_data))
        if bsize <= 0 or bsize > max_bytes:
            return True, None, owner_pid, True
        ptr = kernel32.GlobalLock(h_data)
        if not ptr:
            return True, None, owner_pid, True
        try:
            raw = ctypes.wstring_at(ptr, bsize // 2)
            return True, raw.split("\x00", 1)[0], owner_pid, True
        finally:
            kernel32.GlobalUnlock(h_data)
    finally:
        user32.CloseClipboard()


def write_clipboard_unicode(
    text: str, exclude_from_history: bool = True, retries: int = 10, retry_sleep: float = 0.010
) -> tuple[bool, int]:
    opened = False
    for i in range(max(1, retries)):
        if user32.OpenClipboard(None):
            opened = True
            break
        if i + 1 < retries:
            time.sleep(retry_sleep)
    if not opened:
        return False, int(user32.GetClipboardSequenceNumber())

    try:
        user32.EmptyClipboard()
        if text == "":
            return True, int(user32.GetClipboardSequenceNumber())
        raw_bytes = (text + "\x00").encode("utf-16-le")
        h_mem = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(raw_bytes))
        if not h_mem:
            return False, int(user32.GetClipboardSequenceNumber())
        ptr = kernel32.GlobalLock(h_mem)
        if not ptr:
            kernel32.GlobalFree(h_mem)
            return False, int(user32.GetClipboardSequenceNumber())
        ctypes.memmove(ptr, raw_bytes, len(raw_bytes))
        kernel32.GlobalUnlock(h_mem)
        if not user32.SetClipboardData(CF_UNICODETEXT, h_mem):
            kernel32.GlobalFree(h_mem)
            return False, int(user32.GetClipboardSequenceNumber())

        if exclude_from_history:
            for cf_id in (_CF_EXCLUDE_MONITOR, _CF_CAN_INCLUDE_HIST):
                if cf_id:
                    hm = kernel32.GlobalAlloc(GMEM_MOVEABLE, 4)
                    if hm:
                        p = kernel32.GlobalLock(hm)
                        if p:
                            ctypes.memset(p, 0, 4)
                            kernel32.GlobalUnlock(hm)
                            if not user32.SetClipboardData(cf_id, hm):
                                kernel32.GlobalFree(hm)
                        else:
                            kernel32.GlobalFree(hm)
    finally:
        user32.CloseClipboard()
    return True, int(user32.GetClipboardSequenceNumber())


def wait_for_clipboard_quiet(quiet_ms: int = 130, max_wait_ms: int = 380) -> None:
    quiet_sec = quiet_ms / 1000.0
    deadline = time.perf_counter() + (max_wait_ms / 1000.0)
    last_seq = user32.GetClipboardSequenceNumber()
    stable_since = time.perf_counter()
    while time.perf_counter() < deadline:
        cur_seq = user32.GetClipboardSequenceNumber()
        if cur_seq != last_seq:
            last_seq = cur_seq
            stable_since = time.perf_counter()
        elif (time.perf_counter() - stable_since) >= quiet_sec:
            return
        time.sleep(0.015)


# ==============================================================================
# 8. CLICK-THROUGH VISUAL HIGHLIGHT RETICLE
# ==============================================================================
def flash_highlight_box(
    center_x: int,
    center_y: int,
    width: int = 110,
    height: int = 34,
    border_rgb: tuple[int, int, int] = (56, 189, 248),
    duration_ms: int = 600,
) -> None:
    def _worker():
        hwnd = 0
        brush = 0
        rgn_inner = 0
        try:
            w = max(24, int(width))
            h = max(16, int(height))
            ex_style = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST
            hwnd = user32.CreateWindowExW(
                ex_style, "Static", "", WS_POPUP,
                int(center_x - w // 2), int(center_y - h // 2), w, h,
                None, None, None, None,
            )
            if not hwnd:
                return
            rgn_outer = gdi32.CreateRectRgn(0, 0, w, h)
            rgn_inner = gdi32.CreateRectRgn(3, 3, w - 3, h - 3)
            gdi32.CombineRgn(rgn_outer, rgn_outer, rgn_inner, 4)
            gdi32.DeleteObject(rgn_inner)
            rgn_inner = 0
            if not user32.SetWindowRgn(hwnd, rgn_outer, True):
                gdi32.DeleteObject(rgn_outer)
            user32.SetLayeredWindowAttributes(hwnd, 0, 235, 0x00000002)
            user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
            r, g, b = border_rgb
            brush = gdi32.CreateSolidBrush((int(b) << 16) | (int(g) << 8) | int(r))
            hdc = user32.GetDC(hwnd)
            if hdc and brush:
                rc = wintypes.RECT(0, 0, w, h)
                user32.FillRect(hdc, ctypes.byref(rc), brush)
                user32.ReleaseDC(hwnd, hdc)
            time.sleep(max(0.15, float(duration_ms) / 1000.0))
        except Exception:
            pass
        finally:
            if brush:
                try:
                    gdi32.DeleteObject(brush)
                except Exception:
                    pass
            if rgn_inner:
                try:
                    gdi32.DeleteObject(rgn_inner)
                except Exception:
                    pass
            if hwnd:
                try:
                    user32.DestroyWindow(hwnd)
                except Exception:
                    pass

    threading.Thread(target=_worker, daemon=True).start()


# ==============================================================================
# 9. LAZY UIA/OCR SIDECAR WITH WIN32 KILL-ON-JOB-CLOSE JOB OBJECT
# ==============================================================================
class UiaOcrSidecar:
    """
    Lazy read-only `uia_ocr_bridge.ps1` sidecar bound to a Win32 Job Object
    (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) so `powershell.exe` never orphans.
    """

    def __init__(self, script_path: str) -> None:
        self._script_path = script_path
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._job_handle: int = 0
        self._out_q: queue.Queue[str | None] = queue.Queue()
        self._next_req_id: int = 0
        self._fg_lock = threading.Lock()
        self._fg_depth: int = 0
        self._fg_waiting = threading.Event()
        self._ever_started: bool = False
        self.restart_count: int = 0
        self.uia_ready: bool = False
        self.ocr_ready: bool = False
        self.ocr_lang: str = ""
        self.ocr_max_dim: int = 4096

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def begin_foreground_op(self) -> None:
        with self._fg_lock:
            self._fg_depth += 1
            self._fg_waiting.set()

    def end_foreground_op(self) -> None:
        with self._fg_lock:
            self._fg_depth = max(0, self._fg_depth - 1)
            if self._fg_depth == 0:
                self._fg_waiting.clear()

    def is_foreground_waiting(self) -> bool:
        return self._fg_waiting.is_set()

    def _ensure_job_object_locked(self) -> int:
        if self._job_handle:
            return self._job_handle
        h_job = int(kernel32.CreateJobObjectW(None, None) or 0)
        if not h_job:
            return 0
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            wintypes.HANDLE(h_job),
            JOB_OBJECT_EXTENDED_LIMIT_INFO_CLASS,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            kernel32.CloseHandle(wintypes.HANDLE(h_job))
            return 0
        self._job_handle = h_job
        return h_job

    def start(self) -> dict:
        with self._lock:
            return self._ensure_started_locked()

    def _ensure_started_locked(self) -> dict:
        if self._proc is not None and self._proc.poll() is None:
            return {
                "ok": True,
                "uia_ready": self.uia_ready,
                "ocr_ready": self.ocr_ready,
                "ocr_lang": self.ocr_lang,
                "ocr_max_dim": self.ocr_max_dim,
            }
        if self._ever_started:
            self.restart_count += 1
        self._stop_locked()
        if not os.path.isfile(self._script_path):
            return {"ok": False, "error": "uia_ocr_bridge.ps1 missing"}

        creationflags = 0x08000000 if os.name == "nt" else 0
        try:
            proc = subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-Sta", "-ExecutionPolicy", "Bypass", "-File", self._script_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creationflags,
            )
        except Exception as e:
            return {"ok": False, "error": str(e)}

        h_job = self._ensure_job_object_locked()
        if h_job and getattr(proc, "_handle", None):
            try:
                kernel32.AssignProcessToJobObject(wintypes.HANDLE(h_job), wintypes.HANDLE(int(proc._handle)))
            except Exception:
                pass

        out_q: queue.Queue[str | None] = queue.Queue()

        def _reader():
            try:
                assert proc.stdout is not None
                for ln in iter(proc.stdout.readline, ""):
                    out_q.put(ln)
            except Exception:
                pass
            finally:
                out_q.put(None)

        threading.Thread(target=_reader, daemon=True).start()
        self._proc = proc
        self._out_q = out_q

        ping_res = self._request_locked({"cmd": "ping"}, timeout=6.0)
        if ping_res.get("ok"):
            self._ever_started = True
            self.uia_ready = bool(ping_res.get("uia_ready"))
            self.ocr_ready = bool(ping_res.get("ocr_ready"))
            self.ocr_lang = str(ping_res.get("ocr_lang") or "")
            md = int(ping_res.get("ocr_max_dim") or 4096)
            if md >= 1024:
                self.ocr_max_dim = md
        return ping_res

    def _request_locked(self, payload: dict, timeout: float = 1.8) -> dict:
        if self._proc is None or self._proc.poll() is not None:
            init = self._ensure_started_locked()
            if not init.get("ok") or self._proc is None:
                return init
        try:
            self._next_req_id += 1
            req_id = self._next_req_id
            req_payload = {**payload, "id": req_id}
            assert self._proc.stdin is not None
            import json
            self._proc.stdin.write(json.dumps(req_payload) + "\n")
            self._proc.stdin.flush()

            deadline = time.monotonic() + max(0.3, float(timeout))
            while True:
                rem = deadline - time.monotonic()
                if rem <= 0.0:
                    raise queue.Empty()
                raw = self._out_q.get(timeout=rem)
                if not raw:
                    self._stop_locked()
                    return {"ok": False, "error": "Sidecar EOF"}
                obj = json.loads(raw.strip())
                if isinstance(obj, dict):
                    resp_id = int(obj.get("id") or 0)
                    if resp_id not in (0, req_id):
                        continue
                    if "winrt ocr timeout" in str(obj.get("error") or "").lower():
                        self._stop_locked()
                    return obj
        except queue.Empty:
            self._stop_locked()
            return {"ok": False, "error": "Sidecar timeout"}
        except Exception as e:
            self._stop_locked()
            return {"ok": False, "error": str(e)}

    def query_focused_element(self, timeout: float = 0.6) -> dict:
        with self._lock:
            res = self._request_locked({"cmd": "uia_focused"}, timeout=timeout)
            return res.get("element") or {} if res.get("ok") else {}

    def query_element_at_point(self, x: int, y: int, timeout: float = 0.6) -> dict:
        with self._lock:
            res = self._request_locked({"cmd": "uia_at_point", "x": int(x), "y": int(y)}, timeout=timeout)
            return res.get("element") or {} if res.get("ok") else {}

    def ocr_screen_rect(
        self, x: int, y: int, w: int, h: int, scale: int = 3, mode: str = "both", timeout: float = 2.2
    ) -> dict:
        with self._lock:
            dl_ms = max(400, int(float(timeout) * 1000.0) - 300)
            return self._request_locked(
                {
                    "cmd": "ocr_rect",
                    "x": int(x), "y": int(y), "w": int(w), "h": int(h),
                    "scale": int(scale), "mode": str(mode), "deadline_ms": dl_ms,
                },
                timeout=timeout,
            )

    def _stop_locked(self) -> None:
        p = self._proc
        self._proc = None
        if p is not None and p.poll() is None:
            try:
                if p.stdin:
                    p.stdin.write('{"cmd":"quit"}\n')
                    p.stdin.flush()
                p.wait(timeout=0.3)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()
            if self._job_handle:
                try:
                    kernel32.CloseHandle(wintypes.HANDLE(self._job_handle))
                except Exception:
                    pass
                self._job_handle = 0


# ==============================================================================
# 10. PUSH & GRAB ENGINES (MAIN TOS + OPTIONAL WEBULL)
# ==============================================================================
@dataclass
class PushResult:
    ok: bool
    label: str
    message: str
    last_clip_seq: int = 0
    app_kind: str = ""


def _execute_verified_symbol_push(
    ticker: str,
    app_kind: str,
    win: WindowInfo,
    rel_x: int,
    rel_y: int,
    focus_settle_ms: int,
    dropdown_delay_ms: int,
    sidecar: UiaOcrSidecar | None,
    *,
    label: str,
    saved_maximized: bool | None = None,
    saved_dpi: int = 0,
    saved_rect: list | None = None,
    size_tol: int = 6,
    on_before_push: object | None = None,
) -> PushResult:
    """
    Live-trading-safe symbol push for calibrated Main TOS (`tos`) or Webull (`webull`):
      - Strict maximize, DPI, window size (±6px), bounds, modal, and WindowFromPoint checks.
      - HardwareInputGuard (`BlockInput(True)`) around `Click -> Home -> Shift+End -> Shift+Insert`
        (plus `Delete` ONLY on Webull, NEVER on Java Swing TOS).
      - Mandatory Clipboard Read-Back (`Home -> Shift+End -> Ctrl+Insert`) before pressing Enter.
      - ZERO `Ctrl+<letter>` keys ever dispatched.
    """
    clean_ticker = parse_valid_ticker(ticker)
    if not clean_ticker:
        return PushResult(False, label, f"{label}: invalid ticker '{ticker}'", app_kind=app_kind)

    if has_modal_or_popup(win.hwnd, app_kind):
        record_trace(f"PUSH {label} '{clean_ticker}' -> ABORT (modal pre-focus)")
        return PushResult(False, label, f"{label}: close open popup/dialog first!", app_kind=app_kind)

    was_iconic = win.is_iconic
    if not focus_window_no_alt(win.hwnd, max_attempts=10):
        record_trace(f"PUSH {label} '{clean_ticker}' -> ABORT (focus failed)")
        return PushResult(False, label, f"{label}: could not focus window", app_kind=app_kind)

    eff_settle = max(65, int(focus_settle_ms) + (120 if was_iconic else 0))
    if not wait_for_stable_foreground(win.hwnd, settle_ms=eff_settle, max_total_ms=650):
        focus_window_no_alt(win.hwnd, max_attempts=6)
        if not wait_for_stable_foreground(win.hwnd, settle_ms=eff_settle, max_total_ms=550):
            record_trace(f"PUSH {label} '{clean_ticker}' -> ABORT (focus unstable)")
            return PushResult(False, label, f"{label}: focus unstable — aborted", app_kind=app_kind)

    if has_modal_or_popup(win.hwnd, app_kind):
        return PushResult(False, label, f"{label}: dialog appeared — aborted", app_kind=app_kind)

    if saved_maximized is not None and bool(user32.IsZoomed(win.hwnd)) != bool(saved_maximized):
        return PushResult(False, label, f"{label}: max state changed — re-pick in ⚙", app_kind=app_kind)

    cur_dpi = get_window_dpi(win.hwnd)
    if saved_dpi > 0 and cur_dpi != saved_dpi:
        return PushResult(False, label, f"{label}: DPI changed — re-pick in ⚙", app_kind=app_kind)

    rc = wintypes.RECT()
    user32.GetWindowRect(win.hwnd, ctypes.byref(rc))
    cur_w, cur_h = int(rc.right - rc.left), int(rc.bottom - rc.top)
    if isinstance(saved_rect, list) and len(saved_rect) == 4:
        if abs(cur_w - int(saved_rect[2])) > size_tol or abs(cur_h - int(saved_rect[3])) > size_tol:
            return PushResult(False, label, f"{label}: resized ({cur_w}x{cur_h}) — re-pick in ⚙", app_kind=app_kind)

    click_x = int(rc.left + rel_x)
    click_y = int(rc.top + rel_y)
    if not (rc.left + 4 <= click_x <= rc.right - 4 and rc.top + 4 <= click_y <= rc.bottom - 4):
        return PushResult(False, label, f"{label}: spot out of bounds — re-pick in ⚙", app_kind=app_kind)

    hit = user32.WindowFromPoint(wintypes.POINT(click_x, click_y))
    if (user32.GetAncestor(hit, GA_ROOT) if hit else 0) != win.hwnd:
        return PushResult(False, label, f"{label}: symbol box covered!", app_kind=app_kind)

    if callable(on_before_push):
        on_before_push(clean_ticker)

    pre_popups = snapshot_process_popups(win.hwnd, win.pid)
    pre_hwnds = set(pre_popups.keys())
    popup_ctx = (win.pid, pre_hwnds, click_x, click_y)
    t_push0 = time.perf_counter()
    last_clip_seq = 0
    require_bi = (app_kind == "webull")

    try:
        clip_ok, last_clip_seq = write_clipboard_unicode(clean_ticker, exclude_from_history=True)
        if not clip_ok:
            return PushResult(False, label, f"{label}: clipboard busy", app_kind=app_kind)

        with HardwareInputGuard() as g1:
            if require_bi and not g1.blocked_ok:
                return PushResult(False, label, f"{label}: run as Admin for BlockInput safety", last_clip_seq=last_clip_seq, app_kind=app_kind)
            click_screen_point_safe(click_x, click_y, expected_hwnd=win.hwnd, restore_cursor=True)
            send_vk_tap_atomic(VK_HOME, extended=True, expected_hwnd=win.hwnd, label="Home", allow_popup_ctx=popup_ctx)
            time.sleep(0.018)
            send_vk_chord_atomic(VK_SHIFT, VK_END, key_extended=True, expected_hwnd=win.hwnd, label="Shift+End", allow_popup_ctx=popup_ctx)
            time.sleep(0.018)
            paste_ts = send_vk_chord_atomic(VK_SHIFT, VK_INSERT, key_extended=True, expected_hwnd=win.hwnd, label="Shift+Insert", allow_popup_ctx=popup_ctx)
            if app_kind != "tos":
                time.sleep(0.025)
                send_vk_tap_atomic(VK_DELETE, extended=True, expected_hwnd=win.hwnd, label="Delete", allow_popup_ctx=popup_ctx)

        time.sleep(0.040)

        # Read-Back Verification (no Ctrl+<letter>!)
        verified_match = False
        readback_val = ""
        readback_mode = "Clip"
        if sidecar is not None and app_kind != "tos" and sidecar.is_running():
            u_el = sidecar.query_focused_element(timeout=0.35)
            if u_el.get("found") and u_el.get("has_value_pattern"):
                cand_u = str(u_el.get("value") or "").strip().upper()
                if cand_u:
                    readback_val = cand_u
                    if cand_u == clean_ticker:
                        verified_match = True
                        readback_mode = "UIA"

        if not verified_match:
            _, seq_empty = write_clipboard_unicode("", exclude_from_history=True)
            last_clip_seq = seq_empty
            with HardwareInputGuard() as g2:
                if require_bi and not g2.blocked_ok:
                    return PushResult(False, label, f"{label}: BlockInput unavailable", last_clip_seq=last_clip_seq, app_kind=app_kind)
                send_vk_tap_atomic(VK_HOME, extended=True, expected_hwnd=win.hwnd, label="Home", allow_popup_ctx=popup_ctx)
                time.sleep(0.016)
                send_vk_chord_atomic(VK_SHIFT, VK_END, key_extended=True, expected_hwnd=win.hwnd, label="Shift+End", allow_popup_ctx=popup_ctx)
                time.sleep(0.016)
                send_vk_chord_atomic(VK_CONTROL, VK_INSERT, key_extended=True, expected_hwnd=win.hwnd, label="Ctrl+Insert", allow_popup_ctx=popup_ctx)

            t_rb = time.perf_counter()
            seq_changed = False
            while (time.perf_counter() - t_rb) < 0.24:
                cs = int(user32.GetClipboardSequenceNumber())
                if cs != seq_empty:
                    seq_changed = True
                    last_clip_seq = cs
                    break
                time.sleep(0.014)
            if seq_changed:
                c_ok, raw_rb, _, _ = read_clipboard_unicode(max_bytes=256)
                if c_ok and raw_rb is not None:
                    readback_val = raw_rb.strip().upper()
                    if readback_val == clean_ticker:
                        verified_match = True

            if app_kind == "tos" and user32.GetForegroundWindow() == win.hwnd:
                with HardwareInputGuard():
                    if user32.GetForegroundWindow() == win.hwnd:
                        send_vk_tap_atomic(VK_END, extended=True, expected_hwnd=win.hwnd, label="End", allow_popup_ctx=popup_ctx)

        if not verified_match:
            if app_kind == "tos" and user32.GetForegroundWindow() == win.hwnd:
                with HardwareInputGuard():
                    if user32.GetForegroundWindow() == win.hwnd:
                        send_vk_tap_atomic(VK_ESCAPE, expected_hwnd=win.hwnd, label="Escape", allow_popup_ctx=popup_ctx)
            shown = readback_val[:14] if readback_val else "empty"
            record_trace(f"PUSH {label} '{clean_ticker}' -> BLOCKED (readback={shown!r})")
            return PushResult(
                False, label, f"{label} BLOCKED: box read '{shown}' != '{clean_ticker}'",
                last_clip_seq=last_clip_seq, app_kind=app_kind,
            )

        min_wait_sec = max(0.26 if app_kind == "webull" else 0.04, min(3.0, dropdown_delay_ms / 1000.0))
        dropdown_seen = ""
        if app_kind == "webull":
            base_max_poll = max(min_wait_sec, 1.15)
            inline_fallback = max(min_wait_sec, 0.95)
            first_dd_elapsed = 0.0
            first_dd_ms: int | None = None
            last_dd_rect: tuple[int, int, int, int] | None = None
            stable_samples = 0
            while True:
                ps, pdesc, prect = inspect_new_process_popups(win.hwnd, win.pid, pre_hwnds, click_x, click_y)
                if ps == "blocking":
                    return PushResult(False, label, f"{label}: popup blocked Enter", last_clip_seq=last_clip_seq, app_kind=app_kind)
                if ps == "dropdown" and prect is not None:
                    now_el = time.perf_counter() - paste_ts
                    if first_dd_ms is None:
                        first_dd_ms = int(now_el * 1000)
                        first_dd_elapsed = now_el
                    dropdown_seen = pdesc
                    stable_samples = (stable_samples + 1) if last_dd_rect == prect else 1
                    last_dd_rect = prect
                    if stable_samples >= 3 and now_el >= min_wait_sec:
                        time.sleep(0.050)
                        ps2, _, pr2 = inspect_new_process_popups(win.hwnd, win.pid, pre_hwnds, click_x, click_y)
                        if ps2 == "blocking":
                            return PushResult(False, label, f"{label}: popup blocked Enter", last_clip_seq=last_clip_seq, app_kind=app_kind)
                        if ps2 == "dropdown" and pr2 == last_dd_rect:
                            break
                        last_dd_rect = pr2
                        stable_samples = 1 if pr2 is not None else 0
                        continue
                elapsed_p = time.perf_counter() - paste_ts
                eff_cap = max(base_max_poll, first_dd_elapsed + 0.35) if first_dd_ms is not None else base_max_poll
                if not dropdown_seen and elapsed_p >= inline_fallback:
                    break
                if elapsed_p >= eff_cap:
                    if dropdown_seen and stable_samples < 2:
                        return PushResult(False, label, f"{label}: dropdown still loading — retry!", last_clip_seq=last_clip_seq, app_kind=app_kind)
                    break
                time.sleep(0.015)
            if not dropdown_seen:
                return PushResult(False, label, f"{label}: search dropdown didn't appear — retry!", last_clip_seq=last_clip_seq, app_kind=app_kind)
        else:
            rem_w = min_wait_sec - (time.perf_counter() - paste_ts)
            if rem_w > 0:
                time.sleep(rem_w)

        _verify_expected_foreground(win.hwnd, "Enter", allow_popup_ctx=popup_ctx)
        if inspect_new_process_popups(win.hwnd, win.pid, pre_hwnds, click_x, click_y)[0] == "blocking":
            return PushResult(False, label, f"{label}: dialog appeared before Enter — aborted", last_clip_seq=last_clip_seq, app_kind=app_kind)

        with HardwareInputGuard() as g_ent:
            if require_bi and not g_ent.blocked_ok:
                return PushResult(False, label, f"{label}: BlockInput failed before Enter", last_clip_seq=last_clip_seq, app_kind=app_kind)
            send_vk_tap_atomic(VK_RETURN, extended=False, expected_hwnd=win.hwnd, label="Enter", allow_popup_ctx=popup_ctx)

        if app_kind == "webull":
            t_ent = time.perf_counter()
            closed_streak = 0
            while (time.perf_counter() - t_ent) < 0.24:
                time.sleep(0.025)
                if inspect_new_process_popups(win.hwnd, win.pid, pre_hwnds, click_x, click_y)[0] == "none":
                    closed_streak += 1
                    if closed_streak >= 2 and (time.perf_counter() - t_ent) >= 0.075:
                        break
                else:
                    closed_streak = 0
        else:
            time.sleep(0.070)

        ms = int((time.perf_counter() - t_push0) * 1000)
        record_trace(f"PUSH {label} '{clean_ticker}' -> OK (rb={readback_mode}, {ms}ms)")
        return PushResult(True, label, f"✅ {label}: '{clean_ticker}' loaded ({ms}ms)!", last_clip_seq=last_clip_seq, app_kind=app_kind)
    except FocusLostError as fe:
        record_trace(f"PUSH {label} '{clean_ticker}' -> FOCUS_LOST ({fe})")
        return PushResult(False, label, f"{label}: {fe}", last_clip_seq=last_clip_seq, app_kind=app_kind)
    except Exception as e:
        record_trace(f"PUSH {label} '{clean_ticker}' -> ERROR ({e})")
        return PushResult(False, label, f"{label} error: {e}", last_clip_seq=last_clip_seq, app_kind=app_kind)


def push_ticker_to_tos_main(
    ticker: str,
    config: dict,
    grid_tracker: "TosGridCacheAndTracker | None" = None,
    all_windows: list[WindowInfo] | None = None,
) -> PushResult:
    rx = int(config.get("tos_symbol_rel_x") or 0)
    ry = int(config.get("tos_symbol_rel_y") or 0)
    if rx <= 0 or ry <= 0 or not str(config.get("tos_window_title") or "").strip():
        return PushResult(False, "Main TOS", "Pick Main TOS Box in ⚙ first!", app_kind="tos")

    wins = all_windows if all_windows is not None else enum_visible_top_windows()
    cal_win, err = find_target_window(
        "tos",
        str(config.get("tos_window_title") or ""),
        config.get("tos_window_rect"),
        label="Main TOS",
        all_windows=wins,
        include_webull=bool(config.get("webull_enabled", False)),
    )
    if cal_win is None:
        return PushResult(False, "Main TOS", err, app_kind="tos")

    return _execute_verified_symbol_push(
        ticker=ticker,
        app_kind="tos",
        win=cal_win,
        rel_x=rx,
        rel_y=ry,
        focus_settle_ms=110,
        dropdown_delay_ms=70,
        sidecar=None,
        label="Main TOS",
        saved_maximized=config.get("tos_window_maximized"),
        saved_dpi=int(config.get("tos_window_dpi") or 0),
        saved_rect=config.get("tos_window_rect"),
        size_tol=4,
        on_before_push=(lambda sym: grid_tracker.record_own_push(sym)) if grid_tracker is not None else None,
    )


def push_ticker_to_webull(
    ticker: str,
    config: dict,
    sidecar: UiaOcrSidecar | None = None,
    all_windows: list[WindowInfo] | None = None,
) -> PushResult:
    if not bool(config.get("webull_enabled", False)):
        return PushResult(False, "Webull", "Webull is disabled in ⚙", app_kind="webull")
    rx = int(config.get("webull_symbol_rel_x") or 0)
    ry = int(config.get("webull_symbol_rel_y") or 0)
    if rx <= 0 or ry <= 0:
        return PushResult(False, "Webull", "Pick Webull Symbol Box in ⚙ first!", app_kind="webull")

    wins = all_windows if all_windows is not None else enum_visible_top_windows()
    wb_win, err = find_target_window(
        "webull",
        str(config.get("webull_window_title") or "Webull Desktop"),
        config.get("webull_window_rect"),
        label="Webull",
        all_windows=wins,
        include_webull=True,
    )
    if wb_win is None:
        return PushResult(False, "Webull", err, app_kind="webull")

    return _execute_verified_symbol_push(
        ticker=ticker,
        app_kind="webull",
        win=wb_win,
        rel_x=rx,
        rel_y=ry,
        focus_settle_ms=int(config.get("webull_focus_settle_ms") or 85),
        dropdown_delay_ms=int(config.get("webull_dropdown_delay_ms") or 260),
        sidecar=sidecar,
        label="Webull",
        saved_maximized=config.get("webull_window_maximized"),
        saved_dpi=int(config.get("webull_window_dpi") or 0),
        saved_rect=config.get("webull_window_rect"),
        size_tol=6,
    )


def _parse_uppercase_title_ticker(title: str) -> str:
    if not title:
        return ""
    for tok in re.split(r"[\s\-|—:]+", title.strip()):
        if tok and tok == tok.upper() and tok not in ("WEBULL", "DESKTOP", "PRO"):
            cand = parse_strict_uppercase_ticker_token(tok)
            if cand and cand == tok:
                return cand
    return ""


def grab_ticker_from_webull_ex(
    config: dict,
    sidecar: UiaOcrSidecar | None,
    all_windows: list[WindowInfo] | None = None,
    source_win: WindowInfo | None = None,
    cursor_pt: tuple[int, int] | None = None,
) -> tuple[str, str]:
    if not bool(config.get("webull_enabled", False)):
        return "", "Webull is disabled in ⚙"
    if (
        source_win is not None
        and not source_win.is_iconic
        and classify_exe_and_class(source_win.exe_name, source_win.exe_path, source_win.class_name, include_webull=True) == "webull"
    ):
        src_cand = _parse_uppercase_title_ticker(source_win.title)
        if src_cand:
            return src_cand, f"{src_cand} ← Webull Title ✓"

    wb_win, err = find_target_window(
        "webull",
        str(config.get("webull_window_title") or "Webull Desktop"),
        config.get("webull_window_rect"),
        label="Webull",
        all_windows=all_windows,
        include_webull=True,
    )
    eff = source_win if (source_win is not None and not source_win.is_iconic) else wb_win
    if eff is None:
        return "", err
    if eff.is_iconic:
        return "", "Webull is minimized"
    tc = _parse_uppercase_title_ticker(eff.title)
    if tc:
        return tc, f"{tc} ← Webull Title ✓"

    rc = wintypes.RECT()
    user32.GetWindowRect(eff.hwnd, ctypes.byref(rc))
    ew, eh = max(1, int(rc.right - rc.left)), max(1, int(rc.bottom - rc.top))
    dpi_sc = max(1.0, float(eff.dpi or get_window_dpi(eff.hwnd) or 96) / 96.0)

    if sidecar is not None and cursor_pt is not None:
        cx, cy = int(cursor_pt[0]), int(cursor_pt[1])
        if rc.left <= cx <= rc.right and rc.top <= cy <= rc.bottom:
            el_c = sidecar.query_element_at_point(cx, cy, timeout=0.45)
            erc = el_c.get("rect") or [0, 0, 0, 0]
            if el_c.get("found") and isinstance(erc, list) and len(erc) == 4:
                qx, qy, qw, qh = int(erc[0]), int(erc[1]), int(erc[2]), int(erc[3])
                if not (qw >= int(0.90 * ew) and qh >= int(0.85 * eh)) and qw >= int(200 * dpi_sc) and qh >= int(115 * dpi_sc):
                    if qx <= cx <= qx + qw and qy <= cy <= qy + qh:
                        hx = max(int(rc.left + 2), qx + 2)
                        hy = max(int(rc.top + 2), qy + 2)
                        hw = min(int(310 * dpi_sc), qw - 4, int(rc.right - 2 - hx))
                        hh = min(int(46 * dpi_sc), qh - 4, int(rc.bottom - 2 - hy))
                        if hw >= 60 and hh >= 24 and is_rect_unoccluded_for_hwnd(eff.hwnd, hx, hy, hw, hh)[0]:
                            o_pane = sidecar.ocr_screen_rect(hx, hy, hw, hh, scale=3, mode="both", timeout=1.5)
                            p_sym = select_leftmost_header_ticker(o_pane, max_left_px=62.0 * dpi_sc)
                            if p_sym:
                                return p_sym, f"{p_sym} ← Webull Chart ✓"

    srx, sry = int(config.get("webull_symbol_rel_x") or 0), int(config.get("webull_symbol_rel_y") or 0)
    if sidecar is not None and wb_win is not None and not wb_win.is_iconic and eff.hwnd == wb_win.hwnd and srx > 0 and sry > 0:
        wrc = wintypes.RECT()
        user32.GetWindowRect(wb_win.hwnd, ctypes.byref(wrc))
        w_dpi_sc = max(1.0, float(wb_win.dpi or 96) / 96.0)
        pt_x, pt_y = int(wrc.left + srx), int(wrc.top + sry)
        sx0 = max(int(wrc.left + 2), int(pt_x - int(48 * w_dpi_sc)))
        sy0 = max(int(wrc.top + 2), int(pt_y - int(22 * w_dpi_sc)))
        sw0 = min(int(280 * w_dpi_sc), int(wrc.right - 2 - sx0))
        sh0 = min(int(46 * w_dpi_sc), int(wrc.bottom - 2 - sy0))
        if sw0 >= 60 and sh0 >= 24 and is_rect_unoccluded_for_hwnd(wb_win.hwnd, sx0, sy0, sw0, sh0)[0]:
            el = sidecar.query_element_at_point(pt_x, pt_y, timeout=0.45)
            if el.get("found"):
                for field in ("value", "name"):
                    raw_f = str(el.get(field) or "").strip()
                    if raw_f:
                        cu = parse_ocr_ticker_candidates(raw_f)
                        if cu:
                            return cu, f"{cu} ← Webull UIA ✓"
            o_hdr = sidecar.ocr_screen_rect(sx0, sy0, sw0, sh0, scale=3, mode="both", timeout=1.5)
            l_sym = select_leftmost_header_ticker(o_hdr, max_left_px=75.0 * w_dpi_sc)
            if l_sym:
                return l_sym, f"{l_sym} ← Webull OCR ✓"
            p_cand, nw = select_best_ocr_word_ticker(o_hdr, float(pt_x - sx0), float(pt_y - sy0))
            if p_cand and not nw:
                return p_cand, f"{p_cand} ← Webull OCR ✓"
    return "", "Could not read Webull ticker — point at Webull chart or pick Webull box in ⚙"


# ==============================================================================
# 11. ZERO-KEYSTROKE TOS THUMBNAIL GRID ENGINE + 2-PASS MAIN GRABBER
# ==============================================================================
def extract_tos_raw_title_slots(raw_title: str) -> tuple[list[str], bool]:
    """
    Splits a Thinkorswim chart/Flexible Grid window title into positional raw slots.
    Preserves `"0"` placeholders and non-equity instruments (`/ES`, `$SPX.X`, options).
    Returns `(raw_slots, title_trusted)`.
    """
    if not raw_title:
        return [], True
    s = re.sub(r"\s*\[build[^\]]*\]\s*$", "", raw_title.strip(), flags=re.IGNORECASE).strip()
    m = re.match(r"^(.*?)\s-\s(?=[^-]*thinkorswim)", s, flags=re.IGNORECASE)
    if not m:
        # e.g. workspace name contains " - " which prevents prefix split
        if "flexible grid" in s.lower() or "," in s:
            return [], False
        return [], True
    prefix = m.group(1).strip()
    if not prefix:
        return [], True
    raw_slots = [part.strip() for part in prefix.split(",")]
    if not raw_slots or len(raw_slots) > 16:
        return raw_slots, False
    if any(tok.count("(") != tok.count(")") for tok in raw_slots):
        return raw_slots, False
    if len(raw_slots) >= 2:
        if not (_candidate_grid_dims(len(raw_slots), 1440, 2400) or _candidate_grid_dims(len(raw_slots), 1920, 1080)):
            return raw_slots, False
    return raw_slots, True


def _parse_tos_grid_equity_slot(tok: str) -> str:
    clean = (tok or "").strip()
    if not clean or clean == "0":
        return ""
    # Futures (/ES, /NQ:XCME), indices ($SPX.X, $TICK), options (.AAPL...), expressions
    if clean.startswith(("$", "/", ".", "(")) or ":" in clean:
        return ""
    return parse_strict_uppercase_ticker_token(clean)


def extract_tos_title_slots(raw_title: str) -> list[str]:
    """
    Returns exact positional slots `list[str]` of length N (`"SPRC"` for valid equities,
    `""` for empty `"0"` placeholders or non-equity instruments). Returns `[]` if untrusted
    or if window is Main@thinkorswim without chart prefix.
    """
    raw_slots, trusted = extract_tos_raw_title_slots(raw_title)
    if not raw_slots or not trusted:
        return []
    return [_parse_tos_grid_equity_slot(tok) for tok in raw_slots]


def extract_tos_title_tickers(raw_title: str) -> list[str]:
    """Non-empty equity tickers from a TOS window title (compatibility wrapper)."""
    return [s for s in extract_tos_title_slots(raw_title) if s]


def describe_empty_or_nonequity_slot(raw_slot: str) -> str:
    clean = (raw_slot or "").strip()
    if not clean or clean == "0":
        return "Hovered TOS chart slot is empty ('0')"
    return f"Hovered TOS chart is non-equity ({clean[:16]})"


def _ocr_canon_key(sym: str) -> str:
    return sym.upper().replace("0", "O").replace("1", "I").replace("5", "S").replace("8", "B")


def match_ocr_word_to_whitelist(
    raw_word: str,
    word_idx: int,
    rect_wh: tuple[float, float],
    whitelist: set[str],
    reject_trailing_comma: bool = False,
) -> str:
    if not raw_word or not whitelist:
        return ""
    stripped = raw_word.strip()
    if stripped.endswith(":") or (reject_trailing_comma and stripped.endswith(",")):
        return ""
    tok = stripped.strip("|,;[]()\"'.-▾▼•*$").lstrip("$").strip()
    if not tok or len(tok) > 9 or any("a" <= ch <= "z" for ch in tok):
        return ""
    if rect_wh[1] < 7.0:
        return ""
    norm_slash = tok.replace("/", ".")
    for cand_tok in (tok, norm_slash):
        if cand_tok in whitelist:
            if len(cand_tok) == 1 and word_idx != 0:
                return ""
            return cand_tok
    if len(tok) >= 3 and "1" <= tok[-1] <= "9":
        base_tok = tok[:-1].replace("/", ".")
        if len(base_tok) >= 2 and base_tok in whitelist:
            return base_tok
    if 2 <= len(norm_slash) <= 6:
        ck = _ocr_canon_key(norm_slash)
        matches = [w for w in whitelist if len(w) == len(norm_slash) and _ocr_canon_key(w) == ck]
        if len(matches) == 1:
            return matches[0]
    return ""


@dataclass
class GridCellRect:
    left: int
    top: int
    right: int
    bottom: int
    ticker: str
    header_x: int
    header_y: int
    verified: bool = True
    title_idx: int = -1
    raw_slot: str = ""
    ocr_confirmed: bool = True


def _sample_window_occlusion_mask(hwnd: int, rect: tuple[int, int, int, int]) -> int:
    if not hwnd or user32.IsIconic(hwnd):
        return 0xFFFF
    wl, wt, ww, wh = rect
    if ww < 80 or wh < 80:
        return 0xFFFF
    own_pid = get_own_pid()
    mask = 0
    bit = 0
    for ry in (0.12, 0.37, 0.63, 0.88):
        py = int(wt + wh * ry)
        for rx in (0.14, 0.38, 0.62, 0.86):
            px = int(wl + ww * rx)
            hit = user32.WindowFromPoint(wintypes.POINT(px, py))
            root = int(user32.GetAncestor(hit, GA_ROOT) or 0) if hit else 0
            if root != hwnd and (not root or get_hwnd_pid(root) != own_pid):
                mask |= 1 << bit
            bit += 1
    return mask


def _candidate_grid_dims(n_charts: int, ww: int, wh: int) -> list[tuple[int, int, float]]:
    if n_charts <= 0 or ww <= 0 or wh <= 0:
        return []
    out: list[tuple[int, int, float]] = []
    for r in range(1, min(9, n_charts + 1)):
        if n_charts % r != 0:
            continue
        c = n_charts // r
        if c > 8:
            continue
        cell_aspect = (float(ww) / c) / max(1.0, (float(wh) / r))
        if not (0.24 <= cell_aspect <= 5.4):
            continue
        ratio = cell_aspect / 1.35
        dist = abs(ratio - 1.0) if ratio >= 1.0 else abs((1.0 / ratio) - 1.0)
        out.append((r, c, dist))
    out.sort(key=lambda t: t[2])
    return out


def _cluster_grid_ocr_candidates(raw_hits: list[tuple[int, int, str, int]]) -> list[tuple[int, int, str]]:
    if not raw_hits:
        return []
    clustered: list[tuple[int, int, str]] = []
    for wx, wy, sym, _widx in sorted(raw_hits, key=lambda h: (0 if h[3] == 0 else 1, h[1], h[0])):
        if any(es == sym and abs(wx - ex) <= 90 and abs(wy - ey) <= 50 for (ex, ey, es) in clustered):
            continue
        clustered.append((int(wx), int(wy), sym))
    return clustered


def fit_verified_grid_lattice_ex(
    win_rect: tuple[int, int, int, int],
    title_tickers: list[str],
    anchors: list[tuple[int, int, str]],
    vis_rect: tuple[int, int, int, int] | None = None,
    dpi: int = 96,
    raw_slots: list[str] | None = None,
) -> tuple[list[GridCellRect] | None, bool]:
    """
    Fits OCR header anchors onto the full N-slot TOS Flexible Grid (preserving empty `'0'`
    and non-equity `""` slots).
    Returns `(cells, layout_locked)` where `layout_locked` is True ONLY when a single
    `(rows, cols, order_mode)` survives and duplicate-symbol slots cannot mis-map `title_idx`.
    """
    n = len(title_tickers)
    active_syms = [s for s in title_tickers if s]
    n_active = len(active_syms)
    if n < 2 or n_active == 0 or not anchors:
        return None, False

    wl, wt, ww, wh = win_rect
    wr, wb = wl + ww, wt + wh
    v_left, v_top, v_w, v_h = vis_rect if vis_rect is not None else win_rect
    dpi_sc = max(1.0, float(dpi or 96) / 96.0)
    nom_top_pad = int(56 * dpi_sc) if v_h >= int(420 * dpi_sc) else 0
    usable_h = max(80.0, float(v_h - nom_top_pad))

    cand_dims = _candidate_grid_dims(n, v_w, int(usable_h)) or _candidate_grid_dims(n, ww, wh)
    if not cand_dims:
        return None, False

    cands: list[tuple[int, int, str]] = []
    seen_tr: set[tuple[int, int, str]] = set()
    for hx, hy, asym in sorted(anchors, key=lambda a: (a[1], a[0], a[2])):
        if asym not in active_syms:
            continue
        tr = (int(hx), int(hy), str(asym))
        if tr not in seen_tr:
            seen_tr.add(tr)
            cands.append(tr)
            if len(cands) >= 24:
                break
    if not cands:
        return None, False
    distinct_anchor_syms = {c[2] for c in cands}

    n_distinct_active = len(set(active_syms))
    if n_active == 1 or n_distinct_active == 1:
        min_req = 1
    elif n >= 6:
        min_req = 3 if (n_active >= 4 and len(cands) >= 3) else 2
    else:
        min_req = 2

    valid_by_shape: dict[tuple[int, int, str], tuple] = {}
    for rows, cols, asp_pen in cand_dims:
        nom_cw = float(v_w) / cols
        nom_ch = usable_h / rows
        same_row_dy = max(52.0, 0.12 * nom_ch)
        same_col_dx = max(68.0, 0.14 * nom_cw)
        step_row_dy = max(65.0, 0.20 * nom_ch)
        step_col_dx = max(85.0, 0.20 * nom_cw)

        for order_mode in ("row_major", "col_major"):
            slot_syms: dict[tuple[int, int], str] = {}
            slot_idx: dict[tuple[int, int], int] = {}
            for idx, sym in enumerate(title_tickers):
                rr, cc = (idx // cols, idx % cols) if order_mode == "row_major" else (idx % rows, idx // rows)
                slot_syms[(rr, cc)] = sym
                slot_idx[(rr, cc)] = idx

            compat: list[list[tuple[int, int, float]]] = []
            for hx, hy, asym in cands:
                slots: list[tuple[int, int, float]] = []
                for (rr, cc), s_sym in slot_syms.items():
                    if not s_sym or s_sym != asym:
                        continue
                    exp_l = v_left + cc * nom_cw
                    exp_t = v_top + nom_top_pad + rr * nom_ch
                    tol_left = max(95.0, nom_cw * 0.38)
                    tol_up = max(110.0, nom_ch * 0.45)
                    tol_down = max(210.0, nom_ch * 0.72)
                    if not ((exp_l - tol_left) <= hx <= (exp_l + nom_cw * 0.88)):
                        continue
                    if not ((exp_t - tol_up) <= hy <= (exp_t + tol_down)):
                        continue
                    err_d = abs(hx - (exp_l + 28.0 * dpi_sc)) + abs(hy - (exp_t + 36.0 * dpi_sc))
                    slots.append((rr, cc, err_d))
                slots.sort(key=lambda t: t[2])
                compat.append(slots)

            active_order = sorted([i for i, sl in enumerate(compat) if sl], key=lambda i: (len(compat[i]), i))
            m_act = len(active_order)
            if m_act == 0:
                continue

            cur_assigned: dict[tuple[int, int], tuple[int, int]] = {}
            best_cnt = 0
            best_err = 1e18
            best_maps: list[dict[tuple[int, int], tuple[int, int]]] = []
            nodes = [0]

            def _consistent(rr: int, cc: int, hx: int, hy: int) -> bool:
                for (r0, c0), (x0, y0) in cur_assigned.items():
                    if r0 == rr and abs(hy - y0) > same_row_dy:
                        return False
                    if c0 == cc and abs(hx - x0) > same_col_dx:
                        return False
                    if r0 < rr and not (y0 + step_row_dy < hy):
                        return False
                    if rr < r0 and not (hy + step_row_dy < y0):
                        return False
                    if c0 < cc and not (x0 + step_col_dx < hx):
                        return False
                    if cc < c0 and not (hx + step_col_dx < x0):
                        return False
                return True

            def _dfs(pos: int, err_acc: float) -> None:
                nonlocal best_cnt, best_err, best_maps
                nodes[0] += 1
                if nodes[0] > 3000 or (len(cur_assigned) + (m_act - pos) < best_cnt):
                    return
                if pos == m_act:
                    cnt = len(cur_assigned)
                    if cnt > best_cnt:
                        best_cnt = cnt
                        best_err = err_acc
                        best_maps = [dict(cur_assigned)]
                    elif cnt == best_cnt and cnt > 0:
                        if err_acc + 1e-6 < best_err:
                            best_err = err_acc
                            best_maps = [dict(cur_assigned)]
                        elif abs(err_acc - best_err) <= 45.0:
                            best_maps.append(dict(cur_assigned))
                    return
                ci = active_order[pos]
                hx, hy, _ = cands[ci]
                for rr, cc, e_d in compat[ci]:
                    if (rr, cc) not in cur_assigned and _consistent(rr, cc, hx, hy):
                        cur_assigned[(rr, cc)] = (hx, hy)
                        _dfs(pos + 1, err_acc + e_d)
                        cur_assigned.pop((rr, cc), None)
                _dfs(pos + 1, err_acc)

            _dfs(0, 0.0)
            if not best_maps or best_cnt < min_req:
                continue

            best_tuple = None
            for amap in best_maps[:4]:
                matched_syms = {slot_syms[rc] for rc in amap}
                unm_d = len(distinct_anchor_syms - matched_syms)
                if unm_d > 0 and (n_active < 4 or unm_d > 1 or len(amap) < max(2, len(distinct_anchor_syms) - 1)):
                    continue
                cand_entry = (rows, cols, order_mode, asp_pen, slot_syms, slot_idx, amap, best_err, len(amap), unm_d)
                if best_tuple is None:
                    best_tuple = cand_entry
            if best_tuple is not None:
                valid_by_shape[(rows, cols, order_mode)] = best_tuple

    if not valid_by_shape:
        return None, False

    valid_layouts = sorted(
        valid_by_shape.values(),
        key=lambda t: (t[8], -t[9], -t[3] + (0.02 if t[2] == "row_major" else 0.0), -t[7] * 1e-5),
        reverse=True,
    )
    top_cnt = valid_layouts[0][8]
    min_unm = valid_layouts[0][9]
    top_layouts = [t for t in valid_layouts if t[8] == top_cnt and t[9] == min_unm]

    # Note: for 1-row or 1-col grids, row_major and col_major produce identical slot maps -> deduplicate!
    dedup_top: list[tuple] = []
    seen_maps: set[tuple[tuple[int, int, str], ...]] = set()
    for t in top_layouts:
        sig = tuple((rc[0], rc[1], t[4][rc]) for rc in sorted(t[4].keys()))
        if sig not in seen_maps:
            seen_maps.add(sig)
            dedup_top.append(t)
    top_layouts = dedup_top

    def _build_cells_for_layout(entry: tuple, unique_layout: bool) -> list[GridCellRect]:
        rows, cols, _, _, slot_syms, slot_idx, best_assigned, _, _, _ = entry
        col_xs_obs: dict[int, list[int]] = {}
        row_ys_obs: dict[int, list[int]] = {}
        for (rr, cc), (hx, hy) in best_assigned.items():
            col_xs_obs.setdefault(cc, []).append(hx)
            row_ys_obs.setdefault(rr, []).append(hy)

        dx_hdr = max(16, min(68, int(round(sum(col_xs_obs[0]) / len(col_xs_obs[0]) - v_left)))) if 0 in col_xs_obs else int(28 * dpi_sc)
        col_lefts: list[int] = []
        nom_cw = float(v_w) / cols
        for cc in range(cols):
            if cc == 0:
                col_lefts.append(wl)
            elif cc in col_xs_obs:
                avg_x = int(round(sum(col_xs_obs[cc]) / len(col_xs_obs[cc])))
                col_lefts.append(max(wl + 40, min(wr - 40, avg_x - dx_hdr)))
            else:
                col_lefts.append(int(round(v_left + cc * nom_cw)))

        dy_hdr = int(34 * dpi_sc)
        eff_top_pad = (
            max(0, min(int(150 * dpi_sc), int(round(sum(row_ys_obs[0]) / len(row_ys_obs[0]) - v_top - dy_hdr))))
            if 0 in row_ys_obs
            else nom_top_pad
        )
        nom_ch_eff = max(80.0, float((v_top + v_h) - (v_top + eff_top_pad))) / rows
        row_tops: list[int] = []
        for rr in range(rows):
            if rr == 0:
                row_tops.append(wt)
            elif rr in row_ys_obs:
                avg_y = int(round(sum(row_ys_obs[rr]) / len(row_ys_obs[rr])))
                min_prev = (max(row_ys_obs[rr - 1]) + int(36 * dpi_sc)) if (rr - 1) in row_ys_obs else (wt + 40)
                row_tops.append(max(min_prev, min(wb - 40, avg_y - dy_hdr)))
            else:
                row_tops.append(int(round(v_top + eff_top_pad + rr * nom_ch_eff)))

        anchored_counts = Counter(slot_syms[rc] for rc in best_assigned if slot_syms[rc])
        leftover_unique = set((Counter(active_syms) - anchored_counts).elements())
        assigned_pts_set = set(best_assigned.values())
        unused_cands = [c for c in cands if (c[0], c[1]) not in assigned_pts_set]

        out_cells: list[GridCellRect] = []
        for rr in range(rows):
            for cc in range(cols):
                idx = slot_idx[(rr, cc)]
                sym = title_tickers[idx]
                raw_s = raw_slots[idx] if (raw_slots is not None and idx < len(raw_slots)) else sym
                c_l = col_lefts[cc]
                c_r = col_lefts[cc + 1] - 1 if (cc + 1) < cols else wr
                c_t = row_tops[rr]
                c_b = row_tops[rr + 1] - 1 if (rr + 1) < rows else wb
                dpt = best_assigned.get((rr, cc))
                hx, hy = dpt if dpt else (int(c_l + dx_hdr), int(c_t + dy_hdr))
                if not sym:
                    # Empty / non-equity slot: verified when layout is uniquely established & no stray ticker OCR hit inside box
                    conflict = any(c_l <= ax <= c_r and c_t <= ay <= c_b for (ax, ay, _asym) in unused_cands)
                    is_ver = bool(unique_layout and not conflict)
                    is_conf = is_ver
                elif dpt is not None:
                    is_ver = True
                    is_conf = True
                else:
                    edges_known = (rr in row_ys_obs or rows == 1) and (cc in col_xs_obs or cols == 1)
                    conflict = any(asym != sym and c_l <= ax <= c_r and c_t <= ay <= c_b for (ax, ay, asym) in unused_cands)
                    is_ver = bool(unique_layout and edges_known and (not conflict) and (len(leftover_unique) == 1 and sym in leftover_unique))
                    is_conf = False
                out_cells.append(
                    GridCellRect(
                        int(c_l), int(c_t), int(c_r), int(c_b),
                        sym, hx, hy, bool(is_ver), idx, raw_s, bool(is_conf),
                    )
                )
        return out_cells

    if len(top_layouts) == 1:
        entry = top_layouts[0]
        cells = _build_cells_for_layout(entry, unique_layout=True)
        # Check duplicate tickers: only lock persistent title_idx skeleton if no unanchored duplicates remain
        sym_counts = Counter(active_syms)
        best_assigned = entry[6]
        slot_syms = entry[4]
        anchored_counts = Counter(slot_syms[rc] for rc in best_assigned if slot_syms[rc])
        dup_locked = all(
            anchored_counts.get(s, 0) >= cnt
            for s, cnt in sym_counts.items()
            if cnt > 1
        )
        return cells, bool(dup_locked)

    # Multiple surviving shapes/orderings (e.g. cold start with only slot 0 filled):
    # NEVER lock skeleton yet, and ONLY keep cells verified where all surviving layouts agree!
    cand_lists = [_build_cells_for_layout(e, unique_layout=True) for e in top_layouts[:6]]
    primary = cand_lists[0]
    for ci, pc in enumerate(primary):
        if not pc.verified:
            continue
        agreed_all = True
        for olist in cand_lists[1:]:
            if not any(
                oc.verified
                and oc.ticker == pc.ticker
                and abs(oc.left - pc.left) <= 45
                and abs(oc.top - pc.top) <= 45
                and abs(oc.right - pc.right) <= 45
                and abs(oc.bottom - pc.bottom) <= 45
                for oc in olist
            ):
                agreed_all = False
                break
        if not agreed_all:
            primary[ci] = GridCellRect(
                pc.left, pc.top, pc.right, pc.bottom,
                pc.ticker, pc.header_x, pc.header_y, False, pc.title_idx, pc.raw_slot, False,
            )
    return primary, False


def fit_verified_grid_lattice(
    win_rect: tuple[int, int, int, int],
    title_tickers: list[str],
    anchors: list[tuple[int, int, str]],
    vis_rect: tuple[int, int, int, int] | None = None,
    dpi: int = 96,
) -> list[GridCellRect] | None:
    cells, _ = fit_verified_grid_lattice_ex(win_rect, title_tickers, anchors, vis_rect=vis_rect, dpi=dpi)
    return cells


def _deduplicate_grid_hits_by_multiplicity(
    raw_hits: list[tuple[int, int, str, int]], title_tickers: list[str]
) -> list[tuple[int, int, str]]:
    if not raw_hits:
        return []
    allowed = Counter(s for s in title_tickers if s)
    clustered: list[tuple[int, int, str, int]] = []
    for wx, wy, sym, widx in sorted(raw_hits, key=lambda h: (0 if h[3] == 0 else 1, h[1], h[0])):
        if any(es == sym and abs(wx - ex) <= 210 and abs(wy - ey) <= 115 for (ex, ey, es, _) in clustered):
            continue
        clustered.append((wx, wy, sym, widx))
    by_sym: dict[str, list[tuple[int, int, str, int]]] = {}
    for it in clustered:
        by_sym.setdefault(it[2], []).append(it)
    out: list[tuple[int, int, str]] = []
    for sym, items in by_sym.items():
        cap = max(1, int(allowed.get(sym, 1)))
        items.sort(key=lambda h: (0 if h[3] == 0 else 1, h[1], h[0]))
        for wx, wy, s, _ in items[:cap]:
            out.append((wx, wy, s))
    return out


class TosGridCacheAndTracker:
    """
    Zero-keystroke TOS Flexible Grid & Detached Chart tracker:
      - Preserves empty `'0'` and non-equity slots across the full N-slot layout.
      - Projects slot changes in 0.05ms once layout_locked without re-scanning OCR.
      - Optional single-cell OCR confirmation (`promote_tos`) on newly projected slots.
      - Own-push echo suppression across linked TOS charts (`record_own_push`).
      - Fast 3-state lookup (`"hit"` / `"empty"` / `"miss"`) + targeted cursor-tile OCR fallback.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._title_hist: dict[int, tuple[tuple[str, ...], str, float]] = {}
        # Cache tuple: (win_rect, win_dpi, slots_tup, raw_slots_tup, cells, occ_mask, scan_ts, layout_locked)
        self._grid_cache: dict[
            int,
            tuple[
                tuple[int, int, int, int],
                int,
                tuple[str, ...],
                tuple[str, ...],
                list[GridCellRect],
                int,
                float,
                bool,
            ],
        ] = {}
        self._scanning_hwnds: set[int] = set()
        self._scan_locks: dict[int, threading.Lock] = {}
        self._fail_backoff: dict[int, tuple[tuple[int, int, int, int], tuple[str, ...], float, float]] = {}
        self._last_own_push: tuple[str, float] = ("", 0.0)
        self._untrusted_logged: set[tuple[int, str]] = set()

    def record_own_push(self, ticker: str) -> None:
        clean = str(ticker or "").strip().upper()
        if clean:
            with self._lock:
                self._last_own_push = (clean, time.monotonic())

    def is_recent_own_push(self, ticker: str, max_age: float = 10.0) -> bool:
        clean = str(ticker or "").strip().upper()
        if not clean:
            return False
        with self._lock:
            p_sym, p_ts = self._last_own_push
        return bool(p_sym == clean and p_ts > 0.0 and (time.monotonic() - p_ts) <= max_age)

    def _get_scan_lock(self, hwnd: int) -> threading.Lock:
        with self._lock:
            lk = self._scan_locks.get(hwnd)
            if lk is None:
                lk = threading.Lock()
                self._scan_locks[hwnd] = lk
            return lk

    @staticmethod
    def _project_cells_in_place(
        cells: list[GridCellRect], slots_tup: tuple[str, ...], raw_tup: tuple[str, ...]
    ) -> list[GridCellRect] | None:
        if len(cells) != len(slots_tup) or not all(0 <= c.title_idx < len(slots_tup) for c in cells):
            return None
        out: list[GridCellRect] = []
        for c in cells:
            idx = c.title_idx
            new_s = slots_tup[idx]
            new_r = raw_tup[idx] if idx < len(raw_tup) else new_s
            if c.ticker == new_s and c.raw_slot == new_r:
                out.append(c)
            else:
                out.append(
                    GridCellRect(
                        c.left, c.top, c.right, c.bottom,
                        new_s, c.header_x, c.header_y, True, idx, new_r,
                        ocr_confirmed=(not new_s),
                    )
                )
        return out

    @staticmethod
    def _check_single_chart_shortcut(
        slots: list[str], raw_slots: list[str], trusted: bool
    ) -> tuple[str, str, str]:
        if not trusted or not slots:
            return "grid", "", ""
        if len(slots) == 1:
            if slots[0]:
                return "hit", slots[0], f"{slots[0]} ← TOS Chart ✓"
            return "empty", "", describe_empty_or_nonequity_slot(raw_slots[0] if raw_slots else "")
        first = slots[0]
        if first and all(s == first for s in slots):
            return "hit", first, f"{first} ← TOS Chart ✓"
        if all(not s for s in slots):
            if raw_slots and all(r in ("", "0") for r in raw_slots):
                return "empty", "", "All TOS grid slots are empty ('0')"
        return "grid", "", ""

    def _parse_window_slots(self, hwnd: int, raw_title: str) -> tuple[list[str], list[str], bool]:
        raw_slots, trusted = extract_tos_raw_title_slots(raw_title)
        if not trusted:
            slots = []
            tk_key = (int(hwnd), raw_title[:80])
            if tk_key not in self._untrusted_logged and raw_title:
                self._untrusted_logged.add(tk_key)
                if len(self._untrusted_logged) > 64:
                    self._untrusted_logged.clear()
                record_trace(f"TOS_TITLE_UNTRUSTED hwnd=0x{hwnd:X} title={raw_title!r}", level="WARN")
        else:
            slots = [_parse_tos_grid_equity_slot(tok) for tok in raw_slots] if raw_slots else []
        return slots, raw_slots, trusted

    def observe_tos_window(self, win: WindowInfo) -> tuple[list[str], str, float]:
        if win.is_iconic:
            return [], "", 9999.0
        t_list, raw_slots, trusted = self._parse_window_slots(win.hwnd, win.title)
        t_tup = tuple(t_list)
        raw_tup = tuple(raw_slots)
        now = time.monotonic()
        with self._lock:
            if not trusted:
                self._grid_cache.pop(win.hwnd, None)
            own_sym, own_ts = self._last_own_push
            own_recent = own_ts > 0.0 and (now - own_ts) <= 10.0
            prev = self._title_hist.get(win.hwnd)
            if prev is None:
                self._title_hist[win.hwnd] = (t_tup, "", 0.0)
                return t_list, "", 9999.0

            old_tup, last_sym, last_ts = prev
            if t_tup != old_tup:
                diff_sym = ""
                if len(t_tup) == len(old_tup) and t_tup:
                    diffs = [(i, o, n) for i, (o, n) in enumerate(zip(old_tup, t_tup)) if o != n]
                    non_empty_diffs = [(i, o, n) for (i, o, n) in diffs if n]
                    if len(non_empty_diffs) == 1:
                        diff_sym = non_empty_diffs[0][2]
                    cached = self._grid_cache.get(win.hwnd)
                    if (
                        trusted
                        and diffs
                        and cached is not None
                        and cached[0] == win.rect
                        and cached[1] == win.dpi
                        and cached[7]
                        and len(cached[4]) == len(t_tup)
                    ):
                        projected = self._project_cells_in_place(cached[4], t_tup, raw_tup)
                        if projected is not None:
                            self._grid_cache[win.hwnd] = (
                                win.rect, win.dpi, t_tup, raw_tup, projected, cached[5], cached[6], True,
                            )
                if not diff_sym:
                    added = [s for s in t_tup if s and s not in set(old_tup)]
                    if len(added) == 1:
                        diff_sym = added[0]
                if diff_sym:
                    if own_recent and diff_sym == own_sym:
                        self._title_hist[win.hwnd] = (t_tup, "", 0.0)
                        return t_list, "", 9999.0
                    self._title_hist[win.hwnd] = (t_tup, diff_sym, now)
                    return t_list, diff_sym, 0.0
                self._title_hist[win.hwnd] = (t_tup, last_sym, last_ts)

            if last_sym and own_recent and last_sym == own_sym:
                return t_list, "", 9999.0
            age = (now - last_ts) if (last_sym and last_ts > 0.0) else 9999.0
            return t_list, last_sym, age

    def fast_lookup_at_press_ex(self, root_hwnd: int, raw_title: str, cx: int, cy: int) -> tuple[str, str, str, bool]:
        """
        Non-blocking <0.1ms 3-state lookup safe to invoke synchronously on WM_HOTKEY.
        Returns `(state, ticker, detail, ocr_confirmed)` where `state` in `("hit", "empty", "miss")`.
        """
        if not root_hwnd or not raw_title or user32.IsIconic(root_hwnd):
            return "miss", "", "", False
        t_list, raw_slots, trusted = self._parse_window_slots(root_hwnd, raw_title)
        if not trusted or not t_list:
            return "miss", "", "", False
        sc_state, sc_sym, sc_msg = self._check_single_chart_shortcut(t_list, raw_slots, trusted)
        if sc_state in ("hit", "empty"):
            return sc_state, sc_sym, sc_msg, True

        rect_now = get_window_rect_or_restored(root_hwnd)
        t_tup = tuple(t_list)
        raw_tup = tuple(raw_slots)
        with self._lock:
            cached = self._grid_cache.get(root_hwnd)
            if cached is not None and cached[0] == rect_now and len(cached[2]) == len(t_tup):
                cells = cached[4]
                if cached[2] != t_tup or cached[3] != raw_tup:
                    if cached[7]:
                        proj = self._project_cells_in_place(cells, t_tup, raw_tup)
                        if proj is not None:
                            cells = proj
                            self._grid_cache[root_hwnd] = (
                                cached[0], cached[1], t_tup, raw_tup, cells, cached[5], cached[6], True,
                            )
                        else:
                            cells = []
                    else:
                        cells = []
                for c in cells:
                    if c.verified and c.left <= cx <= c.right and c.top <= cy <= c.bottom:
                        if c.ticker:
                            return "hit", c.ticker, f"{c.ticker} ← TOS Grid ✓", bool(c.ocr_confirmed)
                        return "empty", "", describe_empty_or_nonequity_slot(c.raw_slot), True
        return "miss", "", "", False

    def fast_lookup_at_press(self, root_hwnd: int, raw_title: str, cx: int, cy: int) -> str:
        state, sym, _, _ = self.fast_lookup_at_press_ex(root_hwnd, raw_title, cx, cy)
        return sym if state == "hit" else ""

    def schedule_warmup_scan(
        self, win: WindowInfo, sidecar: UiaOcrSidecar, hud_exclude_rect: tuple[int, int, int, int] | None = None
    ) -> None:
        if win.is_iconic:
            return
        t_list, _, _ = self.observe_tos_window(win)
        raw_slots, trusted = extract_tos_raw_title_slots(win.title)
        if not trusted or len(t_list) <= 1 or not any(t_list):
            return
        sc_state, _, _ = self._check_single_chart_shortcut(t_list, raw_slots, trusted)
        if sc_state != "grid":
            return
        t_tup = tuple(t_list)
        raw_tup = tuple(raw_slots)
        now = time.monotonic()
        with self._lock:
            if win.hwnd in self._scanning_hwnds:
                return
            bf = self._fail_backoff.get(win.hwnd)
            if bf and bf[0] == win.rect and bf[1] == t_tup and now < bf[2]:
                return
            cached = self._grid_cache.get(win.hwnd)
            if cached is not None and cached[0] == win.rect and cached[1] == win.dpi and len(cached[2]) == len(t_tup):
                if cached[7]:
                    if cached[2] != t_tup or cached[3] != raw_tup:
                        proj = self._project_cells_in_place(cached[4], t_tup, raw_tup)
                        if proj is not None:
                            self._grid_cache[win.hwnd] = (
                                cached[0], cached[1], t_tup, raw_tup, proj, cached[5], cached[6], True,
                            )
                    if all(c.verified for c in self._grid_cache[win.hwnd][4]):
                        return
                elif cached[2] == t_tup and all(c.verified for c in cached[4]):
                    return
            self._scanning_hwnds.add(win.hwnd)

        def _bg():
            try:
                self._scan_grid_synchronously(
                    win, t_list, sidecar, hud_exclude_rect, abort_on_fg=True, allow_full_sweep=True, raw_slots=raw_slots
                )
            finally:
                with self._lock:
                    self._scanning_hwnds.discard(win.hwnd)

        threading.Thread(target=_bg, daemon=True).start()

    def _ocr_band_for_window(
        self, win: WindowInfo, bx: int, by: int, bw: int, bh: int, scale: int,
        whitelist: set[str], sidecar: UiaOcrSidecar, hud_exclude_rect: tuple[int, int, int, int] | None,
        min_y_scr: int = -999999, prefer_inverted: bool = True,
    ) -> list[tuple[int, int, str, int]]:
        hits: list[tuple[int, int, str, int]] = []
        if bw < 60 or bh < 24 or not whitelist:
            return hits
        own_pid = get_own_pid()
        reject_commas = len(whitelist) > 1
        seen: set[tuple[int, int, str]] = set()

        def _collect(ocr_res: dict, pkey: str, ckey: str) -> None:
            chr_lines = set(int(li) for li in (ocr_res.get(ckey) or []))
            for item in ocr_res.get(pkey) or []:
                if not isinstance(item, dict) or int(item.get("line_idx", -1)) in chr_lines:
                    continue
                rc = item.get("rect") or [0, 0, 0, 0]
                if not (isinstance(rc, list) and len(rc) == 4):
                    continue
                wx_scr, wy_scr = int(bx + float(rc[0])), int(by + float(rc[1]))
                if wy_scr < min_y_scr:
                    continue
                ww_w, wh_w = max(4, int(float(rc[2]))), max(4, int(float(rc[3])))
                if hud_exclude_rect is not None:
                    hl, ht, hr, hb = hud_exclude_rect
                    if hl <= wx_scr <= hr and ht <= wy_scr <= hb:
                        continue
                widx = int(item.get("word_idx") or 0)
                sym = match_ocr_word_to_whitelist(str(item.get("text") or ""), widx, (float(rc[2]), float(rc[3])), whitelist, reject_trailing_comma=reject_commas)
                if not sym:
                    continue
                hit_pt = wintypes.POINT(wx_scr + ww_w // 2, wy_scr + wh_w // 2)
                h_at = user32.WindowFromPoint(hit_pt)
                r_at = int(user32.GetAncestor(h_at, GA_ROOT) or 0) if h_at else 0
                if r_at != win.hwnd and (not r_at or get_hwnd_pid(r_at) != own_pid):
                    continue
                k = (wx_scr, wy_scr, sym)
                if k not in seen:
                    seen.add(k)
                    hits.append((wx_scr, wy_scr, sym, widx))

        if prefer_inverted and len(whitelist) > 1:
            oi = sidecar.ocr_screen_rect(int(bx), int(by), int(bw), int(bh), scale=scale, mode="inverted", timeout=2.2)
            if oi.get("ok"):
                _collect(oi, "words_inverted", "chrome_inverted")
            if not hits:
                on = sidecar.ocr_screen_rect(int(bx), int(by), int(bw), int(bh), scale=scale, mode="normal", timeout=2.2)
                if on.get("ok"):
                    _collect(on, "words_normal", "chrome_normal")
        else:
            ob = sidecar.ocr_screen_rect(int(bx), int(by), int(bw), int(bh), scale=scale, mode="both", timeout=2.4)
            if ob.get("ok"):
                _collect(ob, "words_inverted", "chrome_inverted")
                _collect(ob, "words_normal", "chrome_normal")
        return hits

    def _verify_or_invalidate_cell_sync(
        self, win: WindowInfo, title_tickers: list[str], cell: GridCellRect,
        sidecar: UiaOcrSidecar, hud_exclude_rect: tuple[int, int, int, int] | None,
    ) -> str:
        if win.is_iconic or user32.IsIconic(win.hwnd):
            return ""
        whitelist = {s for s in title_tickers if s}
        if not whitelist or not cell.ticker:
            return ""
        vis_rect = get_visible_frame_rect(win.hwnd, win.rect, win.is_zoomed)
        dpi_sc = max(1.0, float(win.dpi or 96) / 96.0)
        min_grid_y = vis_rect[1] + int(40 * dpi_sc)
        bx = max(win.rect[0] + 2, cell.left + 2)
        by = max(min_grid_y, cell.top - int(16 * dpi_sc))
        bw = min(int(420 * dpi_sc), max(60, cell.right - bx - 2))
        bh = min(int(210 * dpi_sc), max(40, cell.bottom - by - 2))
        if bw < 60 or bh < 24:
            return ""
        hits = self._ocr_band_for_window(
            win, bx, by, bw, bh, 3, whitelist, sidecar, hud_exclude_rect, min_y_scr=min_grid_y, prefer_inverted=False
        )
        if not hits:
            return ""
        header_cut_y = by + int(125 * dpi_sc)
        same_hits = [h for h in hits if h[2] == cell.ticker]
        other_hits = [h for h in hits if h[2] != cell.ticker and h[1] <= header_cut_y]
        if same_hits:
            hx, hy, _, _ = sorted(same_hits, key=lambda h: (h[1], h[0]))[0]
            with self._lock:
                cached = self._grid_cache.get(win.hwnd)
                if cached is not None and cached[0] == win.rect and cached[2] == tuple(title_tickers):
                    upd = [
                        GridCellRect(
                            c0.left, c0.top, c0.right, c0.bottom,
                            c0.ticker, hx, hy, True, c0.title_idx, c0.raw_slot, True,
                        )
                        if (c0.left == cell.left and c0.top == cell.top and c0.ticker == cell.ticker) else c0
                        for c0 in cached[4]
                    ]
                    self._grid_cache[win.hwnd] = (cached[0], cached[1], cached[2], cached[3], upd, cached[5], time.monotonic(), cached[7])
            return cell.ticker
        if other_hits:
            record_trace(
                f"TOS_GRID_INVALIDATED hwnd=0x{win.hwnd:X} expected={cell.ticker} saw={other_hits[0][2]}",
                level="WARN",
            )
            with self._lock:
                self._grid_cache.pop(win.hwnd, None)
        return ""

    def _scan_grid_synchronously(
        self,
        win: WindowInfo,
        title_tickers: list[str],
        sidecar: UiaOcrSidecar,
        hud_exclude_rect: tuple[int, int, int, int] | None = None,
        abort_on_fg: bool = False,
        allow_full_sweep: bool = True,
        raw_slots: list[str] | None = None,
        force_rescan: bool = False,
    ) -> list[GridCellRect]:
        whitelist = {s for s in title_tickers if s}
        if win.is_iconic or user32.IsIconic(win.hwnd) or not title_tickers or not whitelist:
            return []
        wl, wt, ww, wh = win.rect
        if ww < 200 or wh < 120:
            return []
        t_tup = tuple(title_tickers)
        raw_tup = tuple(raw_slots) if raw_slots is not None else t_tup
        with self._get_scan_lock(win.hwnd):
            if abort_on_fg and sidecar.is_foreground_waiting():
                return []
            cur_occ = _sample_window_occlusion_mask(win.hwnd, win.rect)
            if not force_rescan:
                with self._lock:
                    cached = self._grid_cache.get(win.hwnd)
                    if cached is not None and cached[0] == win.rect and cached[1] == win.dpi and len(cached[2]) == len(t_tup):
                        if cached[2] == t_tup and (time.monotonic() - cached[6]) < 1.2 and (cached[5] & ~cur_occ) == 0:
                            return cached[4]
                        if cached[7]:
                            proj = self._project_cells_in_place(cached[4], t_tup, raw_tup)
                            if proj is not None:
                                self._grid_cache[win.hwnd] = (
                                    win.rect, win.dpi, t_tup, raw_tup, proj, cached[5], cached[6], True,
                                )
                                return proj

            vis_rect = get_visible_frame_rect(win.hwnd, win.rect, win.is_zoomed)
            v_left, v_top, v_w, v_h = vis_rect
            dpi_sc = max(1.0, float(win.dpi or 96) / 96.0)
            min_grid_y = v_top + int(40 * dpi_sc)
            nom_top_pad = int(56 * dpi_sc) if v_h >= int(420 * dpi_sc) else 0
            usable_h = max(80.0, float(v_h - nom_top_pad))
            max_dim = max(4096, int(sidecar.ocr_max_dim or 8192))

            strip_scale = 3 if (v_w * 3) <= max_dim else (2 if (v_w * 2) <= max_dim else 1)
            strip_h = min(v_h, int(215 * dpi_sc))
            cand_dims = _candidate_grid_dims(len(title_tickers), v_w, int(usable_h))
            raw_y_intervals: list[tuple[int, int]] = [(min_grid_y, min(wt + wh, min_grid_y + strip_h))]
            for rows, _cols, _ in cand_dims[:2]:
                for rr in range(rows):
                    y0 = max(min_grid_y, int(round(v_top + nom_top_pad + rr * (usable_h / rows))) - int(52 * dpi_sc))
                    y1 = min(wt + wh, y0 + strip_h)
                    if y1 - y0 >= 32:
                        raw_y_intervals.append((y0, y1))
            raw_y_intervals.sort()
            merged: list[tuple[int, int]] = []
            for s0, s1 in raw_y_intervals:
                if merged and s0 <= merged[-1][1] + int(16 * dpi_sc):
                    merged[-1] = (merged[-1][0], max(merged[-1][1], s1))
                else:
                    merged.append((s0, s1))

            raw_hits: list[tuple[int, int, str, int]] = []
            for s0, s1 in merged:
                if abort_on_fg and sidecar.is_foreground_waiting():
                    return []
                raw_hits.extend(
                    self._ocr_band_for_window(
                        win, v_left, s0, v_w, s1 - s0, strip_scale, whitelist,
                        sidecar, hud_exclude_rect, min_y_scr=min_grid_y, prefer_inverted=True,
                    )
                )

            clustered = _cluster_grid_ocr_candidates(raw_hits)
            lattice, layout_locked = fit_verified_grid_lattice_ex(
                win.rect, title_tickers, clustered, vis_rect=vis_rect, dpi=win.dpi, raw_slots=raw_slots,
            )

            if lattice is not None and any((not c.verified and bool(c.ticker)) for c in lattice):
                miss_bands: list[tuple[int, int]] = []
                for mc in lattice:
                    if not mc.verified and mc.ticker and is_rect_unoccluded_for_hwnd(win.hwnd, max(v_left, mc.left + 8), max(min_grid_y, mc.top), 140, 64)[0]:
                        my0 = max(min_grid_y, mc.top - int(18 * dpi_sc))
                        my1 = min(mc.bottom, my0 + int(235 * dpi_sc))
                        if my1 - my0 >= 36:
                            miss_bands.append((my0, my1))
                miss_bands.sort()
                merged_m: list[tuple[int, int]] = []
                for m0, m1 in miss_bands:
                    if merged_m and m0 <= merged_m[-1][1] + 20:
                        merged_m[-1] = (merged_m[-1][0], max(merged_m[-1][1], m1))
                    else:
                        merged_m.append((m0, m1))
                added = False
                for m0, m1 in merged_m:
                    if abort_on_fg and sidecar.is_foreground_waiting():
                        return []
                    eh = self._ocr_band_for_window(
                        win, v_left, m0, v_w, m1 - m0, strip_scale, whitelist,
                        sidecar, hud_exclude_rect, min_y_scr=min_grid_y, prefer_inverted=False,
                    )
                    if eh:
                        raw_hits.extend(eh)
                        added = True
                if added:
                    clustered = _cluster_grid_ocr_candidates(raw_hits)
                    lat2, locked2 = fit_verified_grid_lattice_ex(
                        win.rect, title_tickers, clustered, vis_rect=vis_rect, dpi=win.dpi, raw_slots=raw_slots,
                    )
                    if lat2 is not None:
                        lattice, layout_locked = lat2, locked2

            if allow_full_sweep and lattice is None and len({c[2] for c in clustered}) < len(whitelist):
                fb_scale = 2 if (v_w * 2) <= max_dim else 1
                band_h = min(v_h, min(880, max_dim // fb_scale))
                overlap = int(105 * dpi_sc)
                y_scr = min_grid_y
                y_end = wt + wh
                while y_scr < y_end:
                    if abort_on_fg and sidecar.is_foreground_waiting():
                        return []
                    cur_h = min(band_h, y_end - y_scr)
                    if cur_h < 40:
                        break
                    raw_hits.extend(
                        self._ocr_band_for_window(
                            win, v_left, y_scr, v_w, cur_h, fb_scale, whitelist,
                            sidecar, hud_exclude_rect, min_y_scr=min_grid_y, prefer_inverted=True,
                        )
                    )
                    if y_scr + cur_h >= y_end:
                        break
                    y_scr += max(80, band_h - overlap)
                clustered = _cluster_grid_ocr_candidates(raw_hits)
                lattice, layout_locked = fit_verified_grid_lattice_ex(
                    win.rect, title_tickers, clustered, vis_rect=vis_rect, dpi=win.dpi, raw_slots=raw_slots,
                )

            cells = lattice or []
            now_done = time.monotonic()
            with self._lock:
                if not cells:
                    if force_rescan:
                        self._grid_cache.pop(win.hwnd, None)
                    bf = self._fail_backoff.get(win.hwnd)
                    prev_d = bf[3] if (bf is not None and bf[0] == win.rect and bf[1] == t_tup) else 0.0
                    next_d = min(18.0, 3.0 if prev_d <= 0.0 else prev_d * 2.0)
                    self._fail_backoff[win.hwnd] = (win.rect, t_tup, now_done + next_d, next_d)
                    return []
                self._fail_backoff.pop(win.hwnd, None)
                self._grid_cache[win.hwnd] = (win.rect, win.dpi, t_tup, raw_tup, cells, cur_occ, now_done, bool(layout_locked))
            return cells

    def _ocr_targeted_cursor_header(
        self,
        win: WindowInfo,
        slots: list[str],
        raw_slots: list[str],
        trusted: bool,
        cx: int,
        cy: int,
        sidecar: UiaOcrSidecar,
        hud_exclude_rect: tuple[int, int, int, int] | None = None,
    ) -> tuple[str, str, str]:
        """
        Fast ~120ms targeted header-crop OCR fallback when Pass 1 lattice couldn't lock
        (e.g. non-uniform grid, sparse 1-slot ambiguity, or occluded siblings).
        Treats `"0"` and non-equity stems as blocking headers so hovering an empty `'0'` tile
        NEVER reads a ticker from the chart tile above it.
        """
        if win.is_iconic or user32.IsIconic(win.hwnd):
            return "miss", "", "TOS grid window is minimized"
        vis_rect = get_visible_frame_rect(win.hwnd, win.rect, win.is_zoomed)
        v_left, v_top, v_w, v_h = vis_rect
        dpi_sc = max(1.0, float(win.dpi or 96) / 96.0)
        min_grid_y = v_top + int(40 * dpi_sc)
        nom_top_pad = int(56 * dpi_sc) if v_h >= int(420 * dpi_sc) else 0
        usable_h = max(80.0, float(v_h - nom_top_pad))

        whitelist = {s for s in slots if s}
        has_zero = any(r in ("", "0") for r in raw_slots) if raw_slots else False
        non_eq_raw_map: dict[str, str] = {}
        if raw_slots:
            for idx_r, r_tok in enumerate(raw_slots):
                eq_s = slots[idx_r] if idx_r < len(slots) else ""
                if eq_s or r_tok in ("", "0"):
                    continue
                stem = r_tok.strip().lstrip("/$. ").split(":", 1)[0].split()[0].upper()
                for cand_k in (stem, stem.split(".", 1)[0]):
                    if cand_k and cand_k not in whitelist:
                        non_eq_raw_map[cand_k] = r_tok

        cand_dims = _candidate_grid_dims(len(slots), v_w, int(usable_h)) if (trusted and len(slots) >= 2) else []
        crop_boxes: list[tuple[int, int, int, int]] = []

        def _add_crop(bx: int, by: int, bw: int, bh: int) -> None:
            bx = max(v_left, min(bx, (v_left + v_w) - 64))
            by = max(min_grid_y, min(by, (win.rect[1] + win.rect[3]) - 32))
            bw = min(int(560 * dpi_sc), max(60, bw), (v_left + v_w) - bx)
            bh = min(int(200 * dpi_sc), max(32, bh), (win.rect[1] + win.rect[3]) - by)
            if bw < 60 or bh < 28:
                return
            if any(abs(bx - ox) <= 35 and abs(by - oy) <= 35 and abs(bw - ow) <= 45 and abs(bh - oh) <= 45 for (ox, oy, ow, oh) in crop_boxes):
                return
            crop_boxes.append((bx, by, bw, bh))

        with self._lock:
            cached_stale = self._grid_cache.get(win.hwnd)
        if cached_stale is not None:
            for sc in cached_stale[4]:
                if sc.left <= cx <= sc.right and sc.top <= cy <= sc.bottom:
                    _add_crop(
                        sc.left + 2,
                        sc.top - int(16 * dpi_sc),
                        int((sc.right - sc.left) * 0.60),
                        int(125 * dpi_sc),
                    )
                    break

        for rows, cols, _ in cand_dims[:3]:
            nom_cw = float(v_w) / cols
            nom_ch = usable_h / rows
            cc = max(0, min(cols - 1, int((cx - v_left) / max(1.0, nom_cw))))
            rr = max(0, min(rows - 1, int((cy - (v_top + nom_top_pad)) / max(1.0, nom_ch))))
            c_l = int(v_left + cc * nom_cw)
            c_t = max(min_grid_y, int(v_top + nom_top_pad + rr * nom_ch))
            _add_crop(
                c_l + 2,
                c_t - int(18 * dpi_sc),
                max(int(120 * dpi_sc), int(nom_cw * 0.55)),
                max(int(72 * dpi_sc), int(nom_ch * 0.36)),
            )

        if not crop_boxes:
            _add_crop(
                cx - int(220 * dpi_sc),
                cy - int(200 * dpi_sc),
                int(480 * dpi_sc),
                int(220 * dpi_sc),
            )

        own_pid = get_own_pid()
        reject_commas = len(whitelist) > 1
        candidates: list[tuple[float, str, str]] = []

        def _collect_crop(ocr_res: dict, bx: int, by: int, pkey: str, ckey: str) -> None:
            chr_lines = set(int(li) for li in (ocr_res.get(ckey) or []))
            for item in ocr_res.get(pkey) or []:
                if not isinstance(item, dict) or int(item.get("line_idx", -1)) in chr_lines:
                    continue
                rc = item.get("rect") or [0, 0, 0, 0]
                if not (isinstance(rc, list) and len(rc) == 4):
                    continue
                wx_scr = int(bx + float(rc[0]))
                wy_scr = int(by + float(rc[1]))
                if wy_scr < min_grid_y or wy_scr > cy + int(18 * dpi_sc) or wx_scr > cx + int(90 * dpi_sc):
                    continue
                if hud_exclude_rect is not None:
                    hl, ht, hr, hb = hud_exclude_rect
                    if hl <= wx_scr <= hr and ht <= wy_scr <= hb:
                        continue
                ww_w, wh_w = max(4, int(float(rc[2]))), max(4, int(float(rc[3])))
                if float(rc[3]) < 8.0 * dpi_sc:
                    continue
                widx = int(item.get("word_idx") or 0)
                raw_text = str(item.get("text") or "").strip()

                kind_hit = ""
                val_hit = ""
                if widx == 0:
                    tok_blk = raw_text.strip("|,;[]()\"'▾▼•*$").upper()
                    if has_zero and (raw_text.strip("|,;[]()\"'") == "0" or (tok_blk in ("0", "O") and tok_blk not in whitelist)):
                        kind_hit, val_hit = "BLOCK", "0"
                    elif non_eq_raw_map:
                        stem_b = raw_text.lstrip("/$. ").split(":", 1)[0].strip("|,;[]()\"'").upper()
                        if stem_b in non_eq_raw_map:
                            kind_hit, val_hit = "BLOCK", non_eq_raw_map[stem_b]

                if not kind_hit:
                    if whitelist:
                        sym = match_ocr_word_to_whitelist(
                            raw_text, widx, (float(rc[2]), float(rc[3])), whitelist, reject_trailing_comma=reject_commas,
                        )
                    elif not trusted and widx == 0:
                        sym = parse_strict_uppercase_ticker_token(raw_text)
                        if sym and sym.split(".", 1)[0] in (_FREEFORM_OCR_STOPWORDS | _COMPANY_SUFFIX_NOISE):
                            sym = ""
                    else:
                        sym = ""
                    if sym:
                        kind_hit, val_hit = "SYM", sym

                if not kind_hit:
                    continue
                hit_pt = wintypes.POINT(wx_scr + ww_w // 2, wy_scr + wh_w // 2)
                h_at = user32.WindowFromPoint(hit_pt)
                r_at = int(user32.GetAncestor(h_at, GA_ROOT) or 0) if h_at else 0
                if r_at != win.hwnd and (not r_at or get_hwnd_pid(r_at) != own_pid):
                    continue
                score = abs(float(cy - wy_scr)) + 0.15 * max(0.0, float(cx - wx_scr))
                candidates.append((score, kind_hit, val_hit))

        for bx, by, bw, bh in crop_boxes[:2]:
            ob = sidecar.ocr_screen_rect(int(bx), int(by), int(bw), int(bh), scale=3, mode="both", timeout=1.6)
            if ob.get("ok"):
                _collect_crop(ob, bx, by, "words_inverted", "chrome_inverted")
                _collect_crop(ob, bx, by, "words_normal", "chrome_normal")
            if candidates:
                break

        if not candidates:
            return "miss", "", "Couldn't read hovered TOS tile — bring grid to front & retry"

        candidates.sort(key=lambda t: t[0])
        _, top_kind, top_val = candidates[0]
        if top_kind == "BLOCK":
            return "empty", "", describe_empty_or_nonequity_slot(top_val)

        if trusted and cand_dims and whitelist:
            sym_ok_in_any_layout = False
            for rows, cols, _ in cand_dims:
                nom_cw = float(v_w) / cols
                nom_ch = usable_h / rows
                cc = max(0, min(cols - 1, int((cx - v_left) / max(1.0, nom_cw))))
                rr = max(0, min(rows - 1, int((cy - (v_top + nom_top_pad)) / max(1.0, nom_ch))))
                idx_rm = rr * cols + cc
                idx_cm = cc * rows + rr
                if (idx_rm < len(slots) and slots[idx_rm] == top_val) or (idx_cm < len(slots) and slots[idx_cm] == top_val):
                    sym_ok_in_any_layout = True
                    break
            if not sym_ok_in_any_layout:
                return "miss", "", "Couldn't read hovered TOS tile — bring grid to front & retry"

        return "hit", top_val, f"{top_val} ← TOS OCR ✓"

    def resolve_grid_at_cursor_ex(
        self,
        win: WindowInfo,
        cursor_pt: tuple[int, int] | None,
        sidecar: UiaOcrSidecar | None,
        hud_exclude_rect: tuple[int, int, int, int] | None = None,
        require_ocr_confirm: bool = False,
    ) -> tuple[str, str, str]:
        """
        3-state interactive hover resolver: returns `(state, ticker, detail)`
        where `state` is `"hit"`, `"empty"`, or `"miss"`.
        NEVER runs a multi-second full-window Pass 2 vertical sweep inside the worker lock.
        """
        if win.is_iconic:
            return "miss", "", "TOS grid window is minimized"
        t_list, diff_sym, diff_age = self.observe_tos_window(win)
        raw_slots, trusted = extract_tos_raw_title_slots(win.title)
        if not t_list and trusted and not raw_slots:
            return "miss", "", ""

        sc_state, sc_sym, sc_msg = self._check_single_chart_shortcut(t_list, raw_slots, trusted)
        if sc_state in ("hit", "empty"):
            return sc_state, sc_sym, sc_msg

        if cursor_pt is None:
            return "miss", "", "Point cursor at a TOS chart tile"
        cx, cy = int(cursor_pt[0]), int(cursor_pt[1])
        wl, wt, ww, wh = win.rect
        if not (wl <= cx <= wl + ww and wt <= cy <= wt + wh):
            if diff_sym and diff_age <= 4.0 and not self.is_recent_own_push(diff_sym, 10.0):
                return "hit", diff_sym, f"{diff_sym} ← TOS Grid (typed) ✓"
            return "miss", "", "Cursor outside TOS grid"

        t_tup = tuple(t_list)
        raw_tup = tuple(raw_slots)
        unver_cell: GridCellRect | None = None

        if trusted and t_list:
            with self._lock:
                self._fail_backoff.pop(win.hwnd, None)
                cached = self._grid_cache.get(win.hwnd)
                if cached is not None and cached[0] == win.rect and cached[1] == win.dpi and len(cached[2]) == len(t_tup):
                    cells = cached[4]
                    if cached[2] != t_tup or cached[3] != raw_tup:
                        if cached[7]:
                            proj = self._project_cells_in_place(cells, t_tup, raw_tup)
                            if proj is not None:
                                cells = proj
                                self._grid_cache[win.hwnd] = (
                                    cached[0], cached[1], t_tup, raw_tup, cells, cached[5], cached[6], True,
                                )
                            else:
                                cells = []
                        else:
                            cells = []
                    for c in cells:
                        if c.left <= cx <= c.right and c.top <= cy <= c.bottom:
                            if c.verified:
                                if not c.ticker:
                                    return "empty", "", describe_empty_or_nonequity_slot(c.raw_slot)
                                if not require_ocr_confirm or c.ocr_confirmed:
                                    return "hit", c.ticker, f"{c.ticker} ← TOS Grid ✓"
                            unver_cell = c
                            break

        if sidecar is None:
            return "miss", "", "OCR sidecar unavailable"

        force_scan = False
        if unver_cell is not None and unver_cell.ticker:
            sym = self._verify_or_invalidate_cell_sync(win, t_list, unver_cell, sidecar, hud_exclude_rect)
            if sym:
                return "hit", sym, f"{sym} ← TOS Grid ✓"
            force_scan = True

        if trusted and t_list and any(t_list):
            cells = self._scan_grid_synchronously(
                win, t_list, sidecar, hud_exclude_rect,
                abort_on_fg=False, allow_full_sweep=False, raw_slots=raw_slots, force_rescan=force_scan,
            )
            for sc in cells:
                if sc.left <= cx <= sc.right and sc.top <= cy <= sc.bottom:
                    if sc.verified:
                        if not sc.ticker:
                            return "empty", "", describe_empty_or_nonequity_slot(sc.raw_slot)
                        if not require_ocr_confirm or sc.ocr_confirmed:
                            return "hit", sc.ticker, f"{sc.ticker} ← TOS Grid ✓"
                    if sc.ticker:
                        sym2 = self._verify_or_invalidate_cell_sync(win, t_list, sc, sidecar, hud_exclude_rect)
                        if sym2:
                            return "hit", sym2, f"{sym2} ← TOS Grid ✓"

        return self._ocr_targeted_cursor_header(
            win, t_list, raw_slots, trusted, cx, cy, sidecar, hud_exclude_rect=hud_exclude_rect,
        )

    def resolve_grid_at_cursor(
        self, win: WindowInfo, cursor_pt: tuple[int, int] | None, sidecar: UiaOcrSidecar | None,
        hud_exclude_rect: tuple[int, int, int, int] | None = None,
    ) -> tuple[str, str]:
        _, sym, det = self.resolve_grid_at_cursor_ex(win, cursor_pt, sidecar, hud_exclude_rect=hud_exclude_rect)
        return sym, det

    def format_grid_diagnostics(self, wins: list[WindowInfo]) -> list[str]:
        lines: list[str] = []
        by_hwnd = {w.hwnd: w for w in wins}
        with self._lock:
            items = list(self._grid_cache.items())
            own_sym, own_ts = self._last_own_push
        if own_sym:
            lines.append(f"- Last Own TOS Push: {own_sym} ({time.monotonic() - own_ts:.1f}s ago)")
        if not items:
            lines.append("- (No cached TOS Flexible Grid layouts yet)")
            return lines
        now = time.monotonic()
        for hwnd, (wrect, wdpi, t_tup, raw_tup, cells, occ_mask, scan_ts, locked) in items:
            wt = redact_sensitive_text(by_hwnd[hwnd].title) if hwnd in by_hwnd else f"hwnd=0x{hwnd:X}"
            lines.append(
                f"- Grid hwnd=0x{hwnd:X} ({wt}) | rect={wrect} dpi={wdpi} locked={locked} "
                f"occ=0x{occ_mask:04x} age={now - scan_ts:.1f}s | slots={list(raw_tup or t_tup)}"
            )
            for ci, c in enumerate(cells):
                lbl = c.ticker if c.ticker else (f"EMPTY({c.raw_slot or '0'})")
                lines.append(
                    f"    [{ci}] {lbl} (tidx={c.title_idx}, ver={c.verified}, conf={c.ocr_confirmed}) "
                    f"rect=({c.left},{c.top},{c.right},{c.bottom})"
                )
        return lines


def grab_tos_2pass(
    config: dict,
    fg_before: int,
    recent_tos_hwnd: int,
    own_root_hwnd: int,
    cal_hwnd: int,
) -> tuple[str, str, int]:
    """
    2-Pass Live-Trading-Safe Thinkorswim Grab (ZERO `Ctrl+<letter>`):
      - Pass 1: Zero-click `Ctrl+Insert` on currently focused/recent TOS symbol box.
      - Wrong-Window Guard: Never jumps from a Flexible Grid to Main TOS.
      - Pass 2: Calibrated Main TOS Box (`Click -> Home -> Shift+End -> Ctrl+Insert -> Escape`)
        strictly wrapped in `HardwareInputGuard`.
    Returns `(ticker_or_empty, status_or_err, last_clip_seq)`.
    """
    fg_is_tos = bool(fg_before and is_tos_process_hwnd(fg_before))
    if fg_is_tos and (not is_tos_frame_hwnd(fg_before) or has_modal_or_popup(fg_before, "tos")):
        return "", "Close TOS dialog first!", 0

    active_tos_hwnd = fg_before if fg_is_tos else (recent_tos_hwnd or cal_hwnd)
    last_seq = 0
    clip_modified = False
    saved_ok, saved_txt, _, saved_is_text = read_clipboard_unicode(max_bytes=16384)

    def _restore_prev_clip() -> None:
        if clip_modified and saved_ok and saved_is_text and saved_txt is not None:
            try:
                write_clipboard_unicode(saved_txt, exclude_from_history=True)
            except Exception:
                pass

    def _restore_caller_focus() -> None:
        if not fg_is_tos and fg_before and fg_before != own_root_hwnd and user32.IsWindow(fg_before):
            time.sleep(0.025)
            focus_window_no_alt(fg_before, max_attempts=6)

    try:
        # Pass 1: Zero-click Ctrl+Insert
        if active_tos_hwnd and (fg_is_tos or active_tos_hwnd != cal_hwnd):
            if has_modal_or_popup(active_tos_hwnd, "tos"):
                return "", "Close TOS dialog first!", last_seq
            foc_ok = True
            if not fg_is_tos:
                foc_ok = focus_window_no_alt(active_tos_hwnd, max_attempts=10) and wait_for_stable_foreground(
                    active_tos_hwnd, settle_ms=100, max_total_ms=500
                )
            if foc_ok and int(user32.GetForegroundWindow() or 0) == active_tos_hwnd:
                _, seq0 = write_clipboard_unicode("", exclude_from_history=True)
                last_seq = seq0
                clip_modified = True
                with HardwareInputGuard():
                    if int(user32.GetForegroundWindow() or 0) == active_tos_hwnd:
                        send_vk_chord_atomic(
                            VK_CONTROL, VK_INSERT, key_extended=True,
                            expected_hwnd=active_tos_hwnd, label="Ctrl+Insert",
                        )
                t0 = time.perf_counter()
                while (time.perf_counter() - t0) < 0.28:
                    cs = int(user32.GetClipboardSequenceNumber())
                    if cs != seq0:
                        last_seq = cs
                        c_ok, raw_t, opid, _ = read_clipboard_unicode(max_bytes=256)
                        owner_ok = is_tos_pid(opid) if opid else is_tos_process_hwnd(int(user32.GetForegroundWindow() or 0))
                        tkr = parse_tos_ticker(raw_t or "") if (c_ok and owner_ok) else ""
                        if tkr and int(user32.GetForegroundWindow() or 0) == active_tos_hwnd:
                            with HardwareInputGuard():
                                if int(user32.GetForegroundWindow() or 0) == active_tos_hwnd:
                                    send_vk_tap_atomic(VK_ESCAPE, expected_hwnd=active_tos_hwnd, label="Escape")
                            _restore_caller_focus()
                            return tkr, f"{tkr} ← TOS ✓", last_seq
                        break
                    time.sleep(0.015)

        if active_tos_hwnd and cal_hwnd and active_tos_hwnd != cal_hwnd:
            _restore_caller_focus()
            return "", "Click thumbnail symbol box or hover + Alt+Q", last_seq

        rel_x = int(config.get("tos_symbol_rel_x") or 0)
        rel_y = int(config.get("tos_symbol_rel_y") or 0)
        saved_title = str(config.get("tos_window_title") or "").strip()
        if rel_x <= 0 or rel_y <= 0 or not saved_title:
            return "", "Pick Main TOS box in ⚙ (🎯)", last_seq
        if not cal_hwnd:
            return "", f"TOS '{saved_title[:12]}' not open!", last_seq
        if has_modal_or_popup(cal_hwnd, "tos"):
            return "", "Close TOS dialog first!", last_seq
        if clip_modified:
            wait_for_clipboard_quiet(quiet_ms=130, max_wait_ms=350)

        was_ic = bool(user32.IsIconic(cal_hwnd))
        if int(user32.GetForegroundWindow() or 0) != cal_hwnd or was_ic:
            if not focus_window_no_alt(cal_hwnd, max_attempts=12):
                return "", "Could not focus Main TOS!", last_seq
            if not wait_for_stable_foreground(cal_hwnd, settle_ms=(220 if was_ic else 110), max_total_ms=650):
                return "", "Main TOS focus unstable!", last_seq

        if has_modal_or_popup(cal_hwnd, "tos"):
            return "", "Main TOS dialog blocked!", last_seq

        saved_z = config.get("tos_window_maximized")
        if saved_z is not None and bool(user32.IsZoomed(cal_hwnd)) != bool(saved_z):
            return "", "TOS max state changed — re-pick ⚙", last_seq
        saved_dpi = int(config.get("tos_window_dpi") or 0)
        if saved_dpi > 0 and get_window_dpi(cal_hwnd) != saved_dpi:
            return "", "TOS monitor DPI changed — re-pick ⚙", last_seq

        rect = wintypes.RECT()
        user32.GetWindowRect(cal_hwnd, ctypes.byref(rect))
        cur_w, cur_h = int(rect.right - rect.left), int(rect.bottom - rect.top)
        s_rect = config.get("tos_window_rect")
        if isinstance(s_rect, list) and len(s_rect) == 4:
            if abs(cur_w - int(s_rect[2])) > 4 or abs(cur_h - int(s_rect[3])) > 4:
                return "", "Main TOS resized — re-pick in ⚙", last_seq

        click_x, click_y = int(rect.left + rel_x), int(rect.top + rel_y)
        if not (rect.left + 4 <= click_x <= rect.right - 4 and rect.top + 4 <= click_y <= rect.bottom - 4):
            return "", "TOS box out of bounds — re-pick ⚙", last_seq

        _, seq1 = write_clipboard_unicode("", exclude_from_history=True)
        last_seq = seq1
        clip_modified = True
        with HardwareInputGuard():
            click_screen_point_safe(click_x, click_y, expected_hwnd=cal_hwnd, restore_cursor=True)
            send_vk_tap_atomic(VK_HOME, extended=True, expected_hwnd=cal_hwnd, label="Home")
            time.sleep(0.018)
            send_vk_chord_atomic(VK_SHIFT, VK_END, key_extended=True, expected_hwnd=cal_hwnd, label="Shift+End")
            time.sleep(0.018)
            send_vk_chord_atomic(VK_CONTROL, VK_INSERT, key_extended=True, expected_hwnd=cal_hwnd, label="Ctrl+Insert")

        t2 = time.perf_counter()
        raw2: str | None = None
        opid2 = 0
        c2_ok = False
        while (time.perf_counter() - t2) < 0.28:
            cs = int(user32.GetClipboardSequenceNumber())
            if cs != seq1:
                last_seq = cs
                c2_ok, raw2, opid2, _ = read_clipboard_unicode(max_bytes=256)
                break
            time.sleep(0.014)

        with HardwareInputGuard():
            if int(user32.GetForegroundWindow() or 0) == cal_hwnd:
                send_vk_tap_atomic(VK_ESCAPE, expected_hwnd=cal_hwnd, label="Escape")

        _restore_caller_focus()
        owner2_ok = is_tos_pid(opid2) if opid2 else True
        tkr2 = parse_tos_ticker(raw2 or "") if (c2_ok and owner2_ok and raw2) else ""
        if tkr2:
            return tkr2, f"{tkr2} ← TOS ✓", last_seq
        return "", "No TOS ticker — check Pick in ⚙", last_seq
    except FocusLostError as fe:
        return "", f"TOS grab aborted: {fe}", last_seq
    finally:
        _restore_prev_clip()


# ==============================================================================
# 12. WIN32 REGISTERHOTKEY MANAGER (THREAD-OWNED, PAUSE-FOR-BIND, F13-F24 READY)
# ==============================================================================
_FKEY_VKS = {f"f{i}": 0x70 + (i - 1) for i in range(1, 25)}
_SPECIAL_VKS = {
    **_FKEY_VKS,
    "space": 0x20,
    "tab": 0x09,
    "insert": VK_INSERT,
    "home": VK_HOME,
    "end": VK_END,
    "grave": 0xC0,
    "`": 0xC0,
}
_TRADING_DANGEROUS_CTRL_LETTERS = {"c", "v", "x", "z", "a", "t", "l", "w", "q", "r"}


def explain_hotkey_rejection(spec: str) -> str:
    if not spec or not spec.strip():
        return ""
    parts = [p.strip().lower().strip("<>") for p in spec.split("+") if p.strip()]
    if not parts:
        return "Empty shortcut"
    mods = 0
    key_tok = ""
    for p in parts:
        if p in ("alt", "menu"):
            mods |= MOD_ALT
        elif p in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif p == "shift":
            mods |= MOD_SHIFT
        elif p in ("win", "super", "cmd"):
            mods |= MOD_WIN
        else:
            if key_tok:
                return f"Invalid shortcut '{spec}'"
            key_tok = p
    if not key_tok:
        return "Press a key along with the modifier"
    if key_tok == "f10":
        return "F10 activates Windows menu bars — pick F13–F24 or Alt/Ctrl+Shift!"
    if key_tok == "f12":
        return "F12 is reserved by Windows debugger — pick F13–F24 or Alt/Ctrl+Shift!"
    if key_tok in {f"f{i}" for i in range(13, 25)}:
        return ""
    if key_tok in {f"f{i}" for i in range(1, 12)}:
        if mods in (0, MOD_SHIFT):
            return f"Bare {key_tok.upper()} is used by apps — use F13–F24, Alt+{key_tok.upper()}, or Ctrl+Shift+{key_tok.upper()}!"
        return ""
    if len(key_tok) == 1 and ("a" <= key_tok <= "z" or "0" <= key_tok <= "9"):
        if mods in (0, MOD_SHIFT):
            return f"Bare '{key_tok.upper()}' cannot be a global hotkey — add Alt, Win, or Ctrl+Shift!"
        if mods == MOD_CONTROL and key_tok in _TRADING_DANGEROUS_CTRL_LETTERS:
            return f"Blocked 'Ctrl+{key_tok.upper()}': collides with active trading/clipboard shortcuts!"
        if (mods & MOD_CONTROL) and not (mods & (MOD_ALT | MOD_WIN | MOD_SHIFT)):
            return f"Use Alt+{key_tok.upper()}, Ctrl+Shift+{key_tok.upper()}, or F13–F24!"
        return ""
    if key_tok in _SPECIAL_VKS and (mods & (MOD_ALT | MOD_WIN | MOD_CONTROL)):
        return ""
    return f"Unsupported key '{key_tok}' — use F13–F24, Alt+<Key>, or Ctrl+Shift+<Key>"


def parse_win32_hotkey(spec: str) -> tuple[int, int, str, tuple[int, ...]] | None:
    if not spec or not spec.strip():
        return None
    if explain_hotkey_rejection(spec) != "":
        return None
    parts = [p.strip().lower().strip("<>") for p in spec.split("+") if p.strip()]
    mods = 0
    disp: list[str] = []
    key_tok = ""
    for p in parts:
        if p in ("alt", "menu"):
            mods |= MOD_ALT
            disp.append("Alt")
        elif p in ("ctrl", "control"):
            mods |= MOD_CONTROL
            disp.append("Ctrl")
        elif p == "shift":
            mods |= MOD_SHIFT
            disp.append("Shift")
        elif p in ("win", "super", "cmd"):
            mods |= MOD_WIN
            disp.append("Win")
        else:
            key_tok = p
    if len(key_tok) == 1 and ("a" <= key_tok <= "z" or "0" <= key_tok <= "9"):
        vk = ord(key_tok.upper())
        disp.append(key_tok.upper())
    elif key_tok in _SPECIAL_VKS:
        vk = _SPECIAL_VKS[key_tok]
        disp.append(key_tok.upper())
    else:
        return None
    return (mods | MOD_NOREPEAT, vk, "+".join(disp), (vk,))


def format_hotkey_display(spec: str) -> str:
    parsed = parse_win32_hotkey(spec)
    return parsed[2] if parsed is not None else spec.strip().replace("<", "").replace(">", "")


def tk_keypress_to_hotkey_spec(keycode: int, state: int) -> tuple[str, str]:
    if keycode in (
        VK_SHIFT, VK_LSHIFT, VK_RSHIFT,
        VK_CONTROL, VK_LCONTROL, VK_RCONTROL,
        VK_MENU, VK_LMENU, VK_RMENU,
        VK_LWIN, VK_RWIN,
    ):
        return "", ""
    mods: list[str] = []
    if state & 0x0004:
        mods.append("<ctrl>")
    if state & 0x20000 or state & 0x0008:
        mods.append("<alt>")
    if state & 0x0001:
        mods.append("<shift>")

    key_name = ""
    if 0x70 <= keycode <= 0x87:
        key_name = f"f{keycode - 0x70 + 1}"
    elif ord("A") <= keycode <= ord("Z") or ord("0") <= keycode <= ord("9"):
        key_name = chr(keycode).lower()
    elif keycode == 0x20:
        key_name = "space"
    elif keycode == 0xC0:
        key_name = "`"
    else:
        return "", f"Key (VK {hex(keycode)}) isn't supported — use F13–F24, Alt+<Key>, or Ctrl+Shift+<Key>!"

    spec = "+".join(mods + [key_name])
    reason = explain_hotkey_rejection(spec)
    return ("", reason) if reason else (spec, "")


WM_APP_APPLY_BINDINGS = 0x8010
WM_APP_PAUSE_ALL = 0x8011
WM_APP_RESUME_ALL = 0x8012
WM_APP_REG_TEMP_ESC = 0x8013
WM_APP_UNREG_TEMP_ESC = 0x8014
HOTKEY_ID_TEMP_ESC = 199
HOTKEY_REPEAT_DEBOUNCE_SEC = 0.30


class HotkeyManager:
    """
    Single persistent Win32 `RegisterHotKey` owner thread:
      - Guarantees all `RegisterHotKey` / `UnregisterHotKey` calls happen ONLY on its own thread.
      - Supports atomic `apply_bindings`, `pause_all` (during bind capture), and `resume_all`.
      - Sends atomic `0xE8` MenuMaskKey down+up on `MOD_ALT` / `MOD_WIN` while modifier is held.
      - Captures `(cursor_pt, fg_at_press, cached_hover_snap)` at `WM_HOTKEY` press time!
    """

    def __init__(self, snapshot_fn=None) -> None:
        self._tid: int = 0
        self._thread: threading.Thread | None = None
        self._closing: bool = False
        self._snapshot_fn = snapshot_fn
        self._cmd_lock = threading.Lock()
        self._cmd_evt = threading.Event()
        self._desired_bindings: list[tuple[int, str, object]] = []
        self._temp_esc_cb = None
        self._temp_esc_ok: bool = False
        self.status_by_id: dict[int, tuple[bool, str]] = {}
        self._ensure_loop_started()

    def _ensure_loop_started(self) -> None:
        if self._thread is not None and self._thread.is_alive() and self._tid:
            return
        self._closing = False
        ready = threading.Event()

        def _loop():
            tid = int(kernel32.GetCurrentThreadId())
            registered_ids: set[int] = set()
            cb_map: dict[int, tuple[object, str, tuple[int, ...], int]] = {}
            last_fire: dict[int, float] = {}
            esc_registered = False

            # Force Windows message queue creation before signaling ready
            msg = wintypes.MSG()
            user32.PostThreadMessageW(tid, WM_APP_RESUME_ALL, 0, 0)

            def _unregister_all():
                for hk_id in list(registered_ids):
                    try:
                        user32.UnregisterHotKey(None, hk_id)
                    except Exception:
                        pass
                registered_ids.clear()
                cb_map.clear()

            def _register_from_desired():
                _unregister_all()
                seen_combos: dict[tuple[int, int], str] = {}
                for hk_id, spec, cb in list(self._desired_bindings):
                    s = (spec or "").strip()
                    if not s:
                        self.status_by_id[hk_id] = (False, "Off")
                        continue
                    err_r = explain_hotkey_rejection(s)
                    parsed = parse_win32_hotkey(s)
                    if parsed is None:
                        self.status_by_id[hk_id] = (False, err_r or f"Invalid '{s}'")
                        continue
                    mods, vk, disp, extra_vks = parsed
                    combo_key = (mods & ~MOD_NOREPEAT, vk)
                    if combo_key in seen_combos:
                        self.status_by_id[hk_id] = (False, f"✗ Duplicate ({disp})")
                        continue
                    seen_combos[combo_key] = disp
                    if user32.RegisterHotKey(None, hk_id, mods, vk):
                        registered_ids.add(hk_id)
                        cb_map[hk_id] = (cb, disp, extra_vks, mods)
                        self.status_by_id[hk_id] = (True, disp)
                    else:
                        err = ctypes.get_last_error() or int(kernel32.GetLastError())
                        if err == ERROR_HOTKEY_ALREADY_REGISTERED:
                            self.status_by_id[hk_id] = (False, f"✗ {disp} taken (1409)")
                        else:
                            self.status_by_id[hk_id] = (False, f"✗ {disp} err={err}")

            self._tid = tid
            ready.set()
            try:
                while not self._closing:
                    ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                    if ret <= 0:
                        break
                    m = msg.message
                    if m == WM_APP_APPLY_BINDINGS or m == WM_APP_RESUME_ALL:
                        _register_from_desired()
                        self._cmd_evt.set()
                    elif m == WM_APP_PAUSE_ALL:
                        _unregister_all()
                        self._cmd_evt.set()
                    elif m == WM_APP_REG_TEMP_ESC:
                        if esc_registered:
                            self._temp_esc_ok = True
                        else:
                            self._temp_esc_ok = bool(
                                user32.RegisterHotKey(None, HOTKEY_ID_TEMP_ESC, MOD_NOREPEAT, VK_ESCAPE)
                            )
                            esc_registered = self._temp_esc_ok
                        self._cmd_evt.set()
                    elif m == WM_APP_UNREG_TEMP_ESC:
                        if esc_registered:
                            try:
                                user32.UnregisterHotKey(None, HOTKEY_ID_TEMP_ESC)
                            except Exception:
                                pass
                            esc_registered = False
                        self._temp_esc_ok = False
                        self._cmd_evt.set()
                    elif m == WM_HOTKEY:
                        wp = int(msg.wParam)
                        if wp == HOTKEY_ID_TEMP_ESC:
                            ec = self._temp_esc_cb
                            if callable(ec):
                                threading.Thread(target=ec, daemon=True).start()
                            continue
                        entry = cb_map.get(wp)
                        if entry is None:
                            continue
                        fn, disp_lbl, ex_vks, hk_mods = entry
                        tap_menu_mask_vk_if_held(hk_mods)
                        now_m = time.monotonic()
                        if (now_m - last_fire.get(wp, 0.0)) < HOTKEY_REPEAT_DEBOUNCE_SEC:
                            continue
                        last_fire[wp] = now_m
                        pt = wintypes.POINT()
                        c_pt = (int(pt.x), int(pt.y)) if user32.GetCursorPos(ctypes.byref(pt)) else None
                        fg_at_press = int(user32.GetForegroundWindow() or 0)
                        snap = None
                        if callable(self._snapshot_fn):
                            try:
                                snap = self._snapshot_fn(c_pt, fg_at_press)
                            except Exception:
                                snap = None
                        threading.Thread(
                            target=fn,
                            args=(disp_lbl, ex_vks, fg_at_press, c_pt, snap),
                            daemon=True,
                        ).start()
            finally:
                if esc_registered:
                    try:
                        user32.UnregisterHotKey(None, HOTKEY_ID_TEMP_ESC)
                    except Exception:
                        pass
                _unregister_all()
                if self._tid == tid:
                    self._tid = 0

        t = threading.Thread(target=_loop, daemon=True, name="HotkeyManagerThread")
        self._thread = t
        t.start()
        ready.wait(timeout=1.0)
        self._cmd_evt.wait(timeout=0.3)

    def _send_thread_cmd(self, msg_id: int, timeout: float = 0.8) -> bool:
        self._ensure_loop_started()
        tid = self._tid
        if not tid:
            return False
        self._cmd_evt.clear()
        if not user32.PostThreadMessageW(tid, msg_id, 0, 0):
            return False
        return self._cmd_evt.wait(timeout=timeout)

    def apply_bindings(self, bindings: list[tuple[int, str, object]]) -> dict[int, tuple[bool, str]]:
        with self._cmd_lock:
            self._desired_bindings = list(bindings)
            self._send_thread_cmd(WM_APP_APPLY_BINDINGS)
            return dict(self.status_by_id)

    def pause_all(self) -> None:
        with self._cmd_lock:
            self._send_thread_cmd(WM_APP_PAUSE_ALL)

    def resume_all(self) -> dict[int, tuple[bool, str]]:
        with self._cmd_lock:
            self._send_thread_cmd(WM_APP_RESUME_ALL)
            return dict(self.status_by_id)

    def register_temp_escape(self, callback: object) -> bool:
        with self._cmd_lock:
            self._temp_esc_cb = callback
            self._send_thread_cmd(WM_APP_REG_TEMP_ESC, timeout=0.4)
            return bool(self._temp_esc_ok)

    def unregister_temp_escape(self) -> None:
        with self._cmd_lock:
            self._temp_esc_cb = None
            self._send_thread_cmd(WM_APP_UNREG_TEMP_ESC, timeout=0.4)

    def stop(self) -> None:
        self._closing = True
        self._temp_esc_cb = None
        tid = self._tid
        t = self._thread
        if tid:
            try:
                user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
            except Exception:
                pass
        if t is not None and t.is_alive():
            t.join(timeout=0.5)
        self._tid = 0
        self._thread = None


# ==============================================================================
# 13. PRIVACY-SCRUBBED AI DIAGNOSTICS REPORT BUILDER
# ==============================================================================
def build_ai_diagnostics_report(
    app_version: str,
    config: dict,
    dpi_mode: str,
    sidecar: UiaOcrSidecar | None,
    hotkey_statuses: dict[int, tuple[bool, str]],
    grid_tracker: TosGridCacheAndTracker | None,
    bm_subscribed_tabs: list[str],
    error_log_path: str,
    *,
    staged_ticker: str = "",
    last_status: str = "",
    worker_busy: bool = False,
    pending_action: str = "",
    events_log_path: str = "",
    crash_log_path: str = "",
) -> str:
    """
    Builds a scrubbed markdown diagnostics snapshot. Strictly read-only: NEVER spawns
    `sidecar` if idle and NEVER calls any Tkinter widget methods (safe from background threads).
    """
    lines: list[str] = []
    lines.append(f"# TI Ticker Deep-Dive v{app_version} — AI Diagnostics Snapshot")
    lines.append(f"- Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- Process Elevated (Admin): {is_current_process_elevated()}")
    lines.append(f"- DPI Awareness Mode: {dpi_mode}")
    lines.append(f"- Mouse Buttons Swapped (SM_SWAPBUTTON): {is_primary_mouse_swapped()}")
    lines.append(f"- Staged Ticker: {staged_ticker or '(none)'} | Last Status: {redact_sensitive_text(last_status or '(ready)')}")
    lines.append(f"- Worker Queue: busy={bool(worker_busy)} | pending={pending_action or '(none)'}")
    lines.append(f"- Webull Integration Enabled: {bool(config.get('webull_enabled', False))}")
    lines.append(f"- Promote to Main Mode: {config.get('promote_tos_mode', 'main_only')}")
    lines.append(f"- Clipboard Policy: {config.get('clipboard_after_deep_dive', 'keep_ticker')}")
    lines.append(f"- Bookmap Subscribed Tabs Cache: {bm_subscribed_tabs}")
    if sidecar is not None:
        lines.append(
            f"- Sidecar Active: running={sidecar.is_running()} | UIA={sidecar.uia_ready} | OCR={sidecar.ocr_ready} (lang={sidecar.ocr_lang!r}) | restarts={sidecar.restart_count}"
        )
    lines.append(f"- Hotkeys: {hotkey_statuses}")
    lines.append("")
    lines.append("## Visible Relevant Windows (Account Numbers, Usernames, Emails & Non-DT Tabs Scrubbed)")

    webull_on = bool(config.get("webull_enabled", False))
    dt_hint = str(config.get("dt_tab_title_hint") or "dilution").strip().lower()
    wins = enum_visible_top_windows()
    relevant: list[tuple[str, WindowInfo]] = []
    for w in wins:
        k = classify_exe_and_class(w.exe_name, w.exe_path, w.class_name, include_webull=webull_on)
        if not k and w.exe_name in {b.lower() for b in config.get("browser_executables", [])}:
            k = "browser"
        if k:
            relevant.append((k, w))

    if not relevant:
        lines.append("- (No visible TOS, Bookmap, Browser" + (", or Webull" if webull_on else "") + " windows)")
    else:
        for k, w in relevant:
            if k == "browser" and dt_hint and dt_hint not in w.title.lower():
                t_disp = "<non-DT browser tab>"
                n_disp = "<non-DT browser tab>"
            else:
                t_disp = redact_sensitive_text(w.title)
                n_disp = redact_sensitive_text(normalize_window_title(w.title, k))
            lines.append(
                f"- [{k.upper()}] hwnd=0x{w.hwnd:X} exe={w.exe_name!r} | elevated={w.is_elevated} | dpi={w.dpi} | "
                f"zoomed={w.is_zoomed} | iconic={w.is_iconic} | rect={w.rect} | "
                f"title={t_disp!r} (norm={n_disp!r})"
            )

    if grid_tracker is not None:
        lines.append("")
        lines.append("## TOS Flexible Grid Cache")
        lines.extend(grid_tracker.format_grid_diagnostics(wins))

    lines.append("")
    traces = get_recent_traces()
    lines.append(f"## Recent Automation Trace (Last {len(traces)} Events)")
    if not traces:
        lines.append("  (No runs yet in this session)")
    else:
        for tr in traces:
            lines.append("  " + redact_sensitive_text(tr))

    lines.append("")
    lines.append("## Recent error.log (Last 20 Lines)")
    if os.path.isfile(error_log_path):
        try:
            with open(error_log_path, "r", encoding="utf-8", errors="replace") as f:
                tail = f.read().splitlines()[-20:]
            for ln in (tail or ["  (empty)"]):
                lines.append("  " + redact_sensitive_text(ln))
        except Exception as e:
            lines.append(f"  (Could not read error.log: {e})")
    else:
        lines.append("  (No errors logged)")

    if events_log_path:
        lines.append("")
        lines.append(f"## Persistent Log Files")
        lines.append(f"- automation_events.log: {redact_sensitive_text(events_log_path)} (exists={os.path.isfile(events_log_path)})")
        if crash_log_path:
            lines.append(f"- crash.log: {redact_sensitive_text(crash_log_path)} (exists={os.path.isfile(crash_log_path)})")

    return redact_sensitive_text("\n".join(lines))
