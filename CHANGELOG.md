# TI Ticker Deep-Dive — Changelog

## v3.0 — Zero-Click TOS Grid Hover, Move-to-Main, Pure-Win32 Hotkey Matrix & Reliability Overhaul
- **Zero-Click TOS Thumbnail Hover Deep-Dive (`Alt+Q` / Macro Keys `F13`–`F24`):** Ported `TosGridCacheAndTracker` (`DWMWA_EXTENDED_FRAME_BOUNDS` frame bounds, column/row-major lattice solver, in-place `title_idx` remapping, own-push echo suppression) and lazy `UiaOcrSidecar` (`uia_ocr_bridge.ps1` bound inside a `KILL_ON_JOB_CLOSE` Win32 Job Object). Point at any TOS Flexible Grid tile and tap once — zero clicks and zero keystrokes sent to Thinkorswim.
- **Move to Main TOS (`Alt+M`):** Promotes hovered TOS thumbnails (or staged tickers when outside non-Main grids) to `Main@thinkorswim` safely (ding-free `Shift+Insert`, no `VK_DELETE`, clipboard readback verification, zero `Ctrl+<letter>`). Includes strict stale-ticker abort guard if an uncalibrated/unreadable grid tile cannot be read. Selectable mode in `⚙`: `Main TOS Only` (default), `Main TOS + BM Only`, or `Main TOS + Full Deep-Dive`.
- **Unified 7-Slot Pure-Win32 `RegisterHotKey` Matrix (`F13`–`F24` Ready):** Replaced `pynput` with `HotkeyManager` (thread-owned `RegisterHotKey`/`UnregisterHotKey`, pause-for-bind capture, atomic `0xE8` `MenuMaskKey` down+up on `MOD_ALT`/`MOD_WIN`, press-time hover snapshot, 1-slot latest-wins rapid queue). Dedicated configurable hotkeys for `Deep-Dive`, `Hover Deep-Dive`, `Move to Main TOS`, `TOS Grab`, `Bookmap Only`, `DilutionTracker Only`, and `Webull Only`.
- **Retired External Input/Clipboard Dependencies:** Removed `pynput`, `pyautogui`, and `pyperclip` in favor of Per-Monitor DPI v2 aware pure-Win32 `SendInput`, `SM_SWAPBUTTON`-aware `click_screen_point_safe`, and `Win+V`-history-excluded Win32 clipboard helpers (`requirements.txt` -> `psutil` only).
- **Critical Live-Trading & Browser Fixes:**
  - Added `dt_tab_title_hint` (`"dilution"`) tab verification after `Ctrl+{tab_idx}` so missing/shifted tabs never clobber an unrelated browser tab (with optional `dt_open_new_tab_if_missing`).
  - Added off-lock generation-counted async clipboard finalizer (`500ms` floor + title-change poll, supporting `Keep Ticker` or `Restore Previous Text` without overwriting images/files).
  - Added Bookmap->DT Subscribe-dialog closure guard before overwriting clipboard with the DT URL.
  - Added case-sensitive same-ticker DT skip (double-tap within 4s forces refresh).
  - Replaced blind `Escape` popup dismisser with verified Subscribe/Instrument `SunAwtDialog` filtering; event-driven Bookmap `Ctrl+T` dialog poll; zero dead sleeps in `wait_for_input_settled`; Tk main-thread-only `_sync_entry_to_staged_ticker` marshaling.
- **Optional Webull Desktop Integration (Off by Default):** Added master `webull_enabled` kill switch (`False` by default = zero Webull scans/hotkeys), Webull Deep-Dive/Hover checkboxes, and Webull Hold-Still calibration.
- **UI Polish (`339x109` Bar Preserved):** Right-click `Staged: XXXX` for 10-ticker Recent History menu; detached monitor-clamped `⚙` `Toplevel` settings window; Hold-Still `🎯 Pick (1.2s)` overlay coach; privacy-scrubbed `🔍 Copy Diagnostics`.

## v2.4 (`4f45c6b`) — Smart Existing Bookmap Tab Auto-Switching
- Added 3-stage Bookmap flow: active chart title check (`<SYMBOL>        Bookmap`), incremental `log_<UTC>-common-*.txt`/`.gz` session tracking with `Ctrl+Tab`/`Ctrl+Shift+Tab` direction optimization, and `onInstrumentAlreadySubscribed` post-`Enter` recovery.

## v2.3 (`05161a1`) — Reverse Thinkorswim -> Deep-Dive Grab (`📥 TOS` & `Alt+G`)
- Added 2-pass `Ctrl+Insert` reverse grab (`BlockInput`-guarded Pass 2 fallback on calibrated `Main@thinkorswim` symbol box, `Win32 RegisterHotKey` for `Alt+G`).

## v2.2 (`630e3f4`) — Stability & Multi-Monitor Hardening
- Fixed `TI LINK` prefix strip race, removed `pywinauto` cursor warps, added minimized/modal/multi-monitor guards and Bookmap focus stabilization (`0x0E` without `VK_MENU`).

## v2.1 (`82779c3`) — Bookmap Only (`📊 BM`) & DilutionTracker Only (`🌐 DT`)
- Added compact side-by-side `📊 BM` and `🌐 DT` buttons to the `339x109` bar.

## v1.0–v2.0 — Initial Trade-Ideas External Linking + Bookmap + DilutionTracker Bridge
- Native Win32 `Edit` control (`ES_MULTILINE | ES_WANTRETURN | ES_AUTOVSCROLL`) with 25ms Tkinter poll, silent staging (`Staged: XXXX`), `Alt+D` trigger, and configurable pinned browser tab (`Ctrl+1`–`Ctrl+8`).
