@echo off
rem Reelmilly launcher. Starts the Web UI and the worker (AI tasks / schedules) in separate
rem windows, then opens the browser. Close a window (or press Ctrl+C) to stop it.
rem
rem   start-reelmilly.bat            normal start (normal windows + opens the browser)
rem   start-reelmilly.bat /startup   for auto-start at logon (minimized windows, no browser)
rem
rem NOTE: keep this file ASCII-only; cmd.exe mis-parses UTF-8 multibyte text in batch files.
cd /d "%~dp0.."

set MODE=normal
set MINFLAG=
if /i "%~1"=="/startup" (
  set MODE=startup
  set MINFLAG=/min
)

if not exist ".venv\Scripts\reelmilly.exe" (
  echo .venv not found. Run the setup steps in README.md first.
  if "%MODE%"=="normal" pause
  exit /b 1
)

rem At logon, wait a little so that the network and disks are ready.
if "%MODE%"=="startup" ping -n 11 127.0.0.1 >nul

rem Do not start a second Web UI if port 8420 is already in use.
netstat -ano | find ":8420" | find "LISTENING" >nul && goto skipweb
start "Reelmilly Web" %MINFLAG% cmd /k ".venv\Scripts\reelmilly.exe web"
:skipweb

rem The worker holds a lock, so a duplicate window will not process tasks twice.
tasklist /v /fi "imagename eq cmd.exe" | find "Reelmilly Worker" >nul && goto skipworker
start "Reelmilly Worker" %MINFLAG% cmd /k ".venv\Scripts\reelmilly.exe watch"
:skipworker

if "%MODE%"=="startup" goto end
ping -n 5 127.0.0.1 >nul
start "" "http://127.0.0.1:8420/"
:end
