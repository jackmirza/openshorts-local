@echo off
powershell.exe -NoProfile -File "%~dp0scripts\Stop-Clipping.ps1"
if errorlevel 1 (
  pause
  exit /b 1
)
