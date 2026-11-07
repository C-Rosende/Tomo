@echo off
setlocal
choice /c YN /m "Create text for videos without subtitles? Optional, about 300 MB. Downloads work without it"
if errorlevel 2 (set ASR=) else (set ASR=-WithASR)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Deps %ASR% %*
echo.
pause
