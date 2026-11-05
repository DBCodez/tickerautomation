@echo off
title TI Ticker Deep-Dive Automation
cd /d "%~dp0"

REM Close any previously running instance of ticker_automation.py so old builds never linger
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*ticker_automation.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

if exist "C:\Users\Ian\AppData\Local\Python\pythoncore-3.15-64\pythonw.exe" (
    start "" "C:\Users\Ian\AppData\Local\Python\pythoncore-3.15-64\pythonw.exe" "%~dp0ticker_automation.py"
) else (
    start "" pythonw "%~dp0ticker_automation.py"
)
