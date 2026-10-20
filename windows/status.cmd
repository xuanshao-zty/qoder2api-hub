@echo off
REM ===========================================================
REM  Qoder2API - show service status (Windows).
REM ===========================================================
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0service-tools.ps1" status %*
echo.
if not "%QD_NO_PAUSE%"=="1" pause
