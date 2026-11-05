# TI Ticker Deep-Dive Automation

A lightweight, always-on-top dark-mode Windows utility that links with **Trade-Ideas Pro** External Linking (or accepts manually typed tickers) and launches a 2-step Deep-Dive sequence into **Bookmap** and **DilutionTracker**.

---

## Features
- **Silent Ticker Staging (`TI LINK:`):**
  - Exposes a native Win32 `Edit` box targetable by Trade-Ideas External Linking (`Crosshairs`).
  - Single-clicking any ticker in Trade-Ideas silently stages the symbol (`Staged: XXXX`) with zero Windows system beep.
  - Also supports clicking directly into the `TI LINK:` box to type any ticker manually.
- **Instant Deep-Dive Trigger (`Alt+D` or GUI Button):**
  - Press **`Alt+D`** globally or click **`🚀 Deep-Dive (Alt+D)`** to run the 2-step sequence:
    1. **Step 1 — Bookmap:** Focuses the main Bookmap chart window, sends `Ctrl+T` to open the Subscribe dialog, pastes the staged ticker, and presses `Enter`.
    2. **Step 2 — DilutionTracker:** Focuses your browser window, switches to your configured pinned DilutionTracker tab (`Ctrl+1` through `Ctrl+8`, default `Ctrl+6`), focuses the address bar (`Ctrl+L`), pastes `https://dilutiontracker.com/app/search/{ticker}`, and presses `Enter`.
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
