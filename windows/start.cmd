@echo off
REM ===========================================================
REM  Qoder2API - double-click to start (Windows).
REM
REM  Runs in the FOREGROUND: closing this window stops the
REM  service. Background mode: windows\service-tools.ps1 start
REM
REM  Optional: start.cmd [port]   (default 8790)
REM
REM  KEEP THIS FILE ASCII-ONLY: cmd.exe parses batch files using
REM  the system ANSI codepage (936/GBK on Chinese Windows), so
REM  non-ASCII comments get mangled and executed as commands.
REM ===========================================================
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
echo.
if not "%QD_NO_PAUSE%"=="1" pause
