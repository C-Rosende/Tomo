@echo off
setlocal
choice /c YN /m "Also install offline speech transcription (about 300 MB)"
if errorlevel 2 (set ASR=) else (set ASR=-WithASR)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Deps %ASR% %*
echo.
pause
