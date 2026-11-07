@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Deps %*
if errorlevel 1 (
  echo Installation failed. Keep this window open and see https://github.com/C-Rosende/Tomo#report-a-problem
  pause
  exit /b 1
)
pause
