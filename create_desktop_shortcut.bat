@echo off
title Create Admin Desktop Shortcut - TI Ticker Deep-Dive
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$WshShell = New-Object -ComObject WScript.Shell; ^
   $Desktop = [Environment]::GetFolderPath('Desktop'); ^
   $ShortcutPath = Join-Path $Desktop 'TI Ticker Deep-Dive.lnk'; ^
   $Shortcut = $WshShell.CreateShortcut($ShortcutPath); ^
   $Shortcut.TargetPath = Join-Path '%~dp0' 'run_ticker_automation.bat'; ^
   $Shortcut.WorkingDirectory = '%~dp0'; ^
   $Shortcut.WindowStyle = 7; ^
   $Shortcut.Description = 'Launch TI Ticker Deep-Dive Automation (Admin)'; ^
   $Shortcut.IconLocation = 'shell32.dll,23'; ^
   $Shortcut.Save(); ^
   $bytes = [System.IO.File]::ReadAllBytes($ShortcutPath); ^
   $bytes[0x15] = $bytes[0x15] -bor 0x20; ^
   [System.IO.File]::WriteAllBytes($ShortcutPath, $bytes); ^
   Write-Host 'Created Admin Desktop Shortcut:' $ShortcutPath -ForegroundColor Green"

pause
