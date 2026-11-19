# TI Ticker Deep-Dive Automation (`v3.0`)

A lightweight, always-on-top dark-mode Windows utility (`339x109` main bar) that links **Trade-Ideas Pro**, **Thinkorswim** (Main chart + multi-chart Flexible Grid thumbnails), **Bookmap**, and **DilutionTracker** (plus optional **Webull Desktop**).

---

## Key Features (`v3.0`)

- **Silent Ticker Staging (`TI LINK:`):**
  - Native Win32 `Edit` box targetable by Trade-Ideas External Linking (`Crosshairs`).
  - Single-clicking any ticker in Trade-Ideas silently stages `Staged: XXXX` with zero Windows system beep.
  - Also supports clicking the `TI LINK:` box (or focusing the bar and typing) to type/paste any ticker manually.
  - **Right-click `Staged: XXXX`** to access your **10 most recent tickers** (1-click Deep-Dive, Stage Only, Bookmap Only, or Move to Main TOS).

- **Zero-Click TOS Thumbnail Hover Deep-Dive (`Alt+Q` or Macro Key `F13`–`F24`):**
  - Hover your mouse over **any chart tile on your Thinkorswim Flexible Grid** (or detached single chart) and press **`Alt+Q`** -> instantly resolves the symbol with **zero clicks and zero keystrokes to Thinkorswim**, stages it, and launches **Bookmap + DilutionTracker** (leaving `Main@thinkorswim` untouched!).
  - Supports clearing unused Flexible Grid slots to `0` (`0, SPRC, INHD, ...`) without losing grid alignment, with `0.05ms` slot projection and targeted header-crop OCR fallback.

- **Move to Main TOS (`Alt+M` or Macro Key `F13`–`F24`):**
  - Hover a TOS thumbnail tile (or use your staged ticker when outside grids) and press **`Alt+M`** to safely push that symbol into your calibrated **`Main@thinkorswim`** symbol box (ding-free, readback-verified, zero `Ctrl+<letter>`).
  - Configurable in **`⚙`** as **Main TOS Only** (default), **Main TOS + BM Only**, or **Main TOS + Full Deep-Dive**.

- **Reverse Thinkorswim Grab (`📥 TOS` or `Alt+G`):**
  - Zero-click resolution on multi-chart Flexible Grids, plus 2-Pass `Ctrl+Insert` live-trading-safe pull from clicked symbol boxes or calibrated `Main@thinkorswim`.

- **Instant Deep-Dive Triggers (`Alt+D`, `📊 BM`, `🌐 DT`):**
  - Press **`Alt+D`** globally or click **`🚀 Deep-Dive`** (or bind dedicated hotkeys / **`F13`–`F24`** macro keys in **`⚙`** for `Bookmap Only` and `DilutionTracker Only`):
    1. **Step 1 — Bookmap:** Un-minimizes & focuses Bookmap (`SunAwtFrame`), automatically switches to existing subscribed tabs (skipping `Ctrl+T` & *"Already subscribed"* toasts), or opens `Ctrl+T` with event-driven popup detection.
    2. **Step 2 — DilutionTracker:** Focuses your browser, switches to your pinned tab (`Ctrl+1`–`Ctrl+8`, default `Ctrl+6`), verifies the tab title matches **`DT Title Hint`** (`dilution`) so unrelated tabs are never overwritten, skips redundant reloads when already viewing the same ticker (double-tap within 4s forces refresh), and finalizes clipboard off-lock without `Win+V` spam.

- **Optional Webull Desktop Integration (Off by Default):**
  - Fully disabled by default (**`Enable Webull Integration`** unchecked in `⚙` = zero Webull window scans or hotkeys). Turn on anytime to include Webull in Deep-Dive, hover Webull charts, or bind a dedicated Webull hotkey.

- **Pure-Win32 Engine & Self-Diagnostics:**
  - Zero `pynput`, `pyautogui`, or `pyperclip` dependencies (`psutil` only). Uses Per-Monitor DPI v2, atomic `SendInput`, OS-consuming `RegisterHotKey` with `0xE8` `MenuMaskKey`, a detached **`⚙`** settings window, Hold-Still **`🎯 Pick (1.2s)`**, **`🔍 Copy Diagnostics`**, and auto-captured privacy-scrubbed **`live_diagnostics.md`** + rolling **`automation_events.log`**.

---

## Quick Setup

1. **Install Python Dependency:**
   ```powershell
   pip install -r requirements.txt
   ```
   *(Uses built-in Windows 10/11 PowerShell WinRT OCR + UI Automation when needed; no external OCR downloads required.)*

2. **Launch as Administrator:**
   - Run [`run_ticker_automation.bat`](run_ticker_automation.bat) (or generate a Desktop shortcut via [`create_desktop_shortcut.bat`](create_desktop_shortcut.bat)).

3. **First-Time Calibration (`⚙`):**
   - Link **Trade-Ideas (`Tools -> External Linking -> Add Link`)** crosshairs onto **`TI LINK:`**.
   - Click **`⚙ -> 🎯 Pick (Hold 1.2s)`** and hover (do not click) over **`Main@thinkorswim`**'s symbol box for 1.2 seconds.
   - Check `Thinkorswim -> Setup -> Hotkeys` to confirm `Alt+D`, `Alt+Q`, `Alt+M`, and `Alt+G` do not conflict with any active order hotkeys (or rebind any slot in `⚙`).

---

## Updating Between Machines

```powershell
git pull
```
