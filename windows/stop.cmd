@echo off
REM ===========================================================
REM  Qoder2API - stop the local service (Windows).
REM  Stops only the process whose command line contains
REM  qoder_proxy.py - other Python processes are never touched.
REM
REM  Optional: stop.cmd [port]
REM ===========================================================
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1" %*
echo.
if not "%QD_NO_PAUSE%"=="1" pause
