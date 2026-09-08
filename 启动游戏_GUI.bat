@echo off
rem ============================================================
rem  WordGame GUI launcher
rem  Double-click to start the GUI.
rem  Optional arg:  selftest  = GUI self test (auto close)
rem                 demo      = headless demo cruise
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  echo [ERROR] Python not found. Install Python 3.8+ with "Add to PATH".
  pause
  exit /b 1
)

set "MODE=--gui"
if "%~1"=="selftest" set "MODE=--gui-selftest"
if "%~1"=="demo"     set "MODE=--demo 120"

echo [START] %PY% -X utf8 main.py %MODE%
%PY% -X utf8 main.py %MODE%
set "rc=%errorlevel%"

echo.
if not "%rc%"=="0" (
  echo [ERROR] Game exited with code %rc%. See output above.
) else (
  echo [DONE] Game closed normally.
)
pause
exit /b %rc%
