@echo off
powershell.exe -NoProfile -File "%~dp0scripts\Start-Clipping.ps1"
if errorlevel 1 (
  pause
  exit /b 1
)
start "" "http://localhost:5175/local-clips"
