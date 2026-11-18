# TI Ticker Deep-Dive Automation

A lightweight, always-on-top dark-mode Windows utility that links with **Trade-Ideas Pro** External Linking (or accepts manually typed tickers) and launches a 2-step Deep-Dive sequence into **Bookmap** and **DilutionTracker**.

---

## Features
- **Silent Ticker Staging (`TI LINK:`):**
  - Exposes a native Win32 `Edit` box targetable by Trade-Ideas External Linking (`Crosshairs`).
  - Single-clicking any ticker in Trade-Ideas silently stages the symbol (`Staged: XXXX`) with zero Windows system beep.
  - Also supports clicking directly into the `TI LINK:` box to type any ticker manually.
- **Reverse Thinkorswim -> Deep-Dive Grab (`📥 TOS` or `Alt+G`):**
  - **2-Pass Live-Trading-Safe Pull** (never sends `Ctrl+C` or letter keys to TOS):
    1. **Clicked Symbol Box (Thumbnails or Main Chart):** Click directly inside any TOS `[ TICKER ]` box and hit **`Alt+G`** to stage it with zero synthetic mouse clicks.
    2. **Main Chart Fallback:** Calibrate your main TOS chart's symbol box once in **`⚙ -> 🎯 Pick (3s)`** to grab its active ticker via **`Alt+G`** or **`📥 TOS`** from anywhere (even when TOS is minimized).
- **Instant Deep-Dive Triggers (`Alt+D`, `📊 BM`, `🌐 DT`):**
  - Press **`Alt+D`** globally or click **`🚀 Deep-Dive (Alt+D)`** to run both steps (or **`📊 BM`** / **`🌐 DT`** to run either step individually):
    1. **Step 1 — Bookmap:** Restores if minimized and focuses Bookmap. If the ticker is already subscribed, automatically activates its existing Bookmap tab (skipping `Ctrl+T` and avoiding *"Already subscribed"* notifications); otherwise opens `Ctrl+T`, pastes the staged ticker, and presses `Enter`.
    2. **Step 2 — DilutionTracker:** Restores if minimized, focuses your browser window, switches to your configured pinned DilutionTracker tab (`Ctrl+1` through `Ctrl+8`, default `Ctrl+6`), focuses the address bar (`Ctrl+L`), pastes `https://dilutiontracker.com/app/search/{ticker}`, and presses `Enter`.
- **Persistent Configuration (`config.json`):**
  - Stores your pinned tab number, window position, always-on-top preference, delays, and optional Bookmap fallback relative coordinates.

---

## Quick Setup on a New Machine (Laptop / Desktop)

1. **Install Python Dependencies:**
   ```powershell
   pip install -r requirements.txt
   ```

2. **Create the "Run as Administrator" Desktop Shortcut:**
   - Double-click [`create_desktop_shortcut.bat`](create_desktop_shortcut.bat) to generate the **`TI Ticker Deep-Dive`** shortcut on your Desktop pre-configured to always run as Administrator.
   - *(Note: Running as Administrator is required so Trade-Ideas can link to the `TI LINK:` box and global hotkeys/focus work across elevated apps.)*

3. **Link Trade-Ideas:**
   - Launch **`TI Ticker Deep-Dive`** from your Desktop shortcut.
   - In Trade-Ideas (`Tools -> External Linking -> Add Link`), drag the crosshairs onto the **`TI LINK:`** input box.

---

## Updating Between Machines

- **Pull latest changes:**
  ```powershell
  git pull
  ```
- **Push local changes:**
  ```powershell
  git add -A
  git commit -m "Update settings/code"
  git push
  ```

