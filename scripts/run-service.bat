@echo off
rem Runs "reelmilly <command>" and restarts it if it exits, writing all output to data\logs\<command>.log.
rem Used by start-reelmilly.bat. The log also records each start/exit, so a crash (exit code) can be told
rem apart from the window being closed or the process being killed (a start line with no exit line).
rem   run-service.bat web | watch
rem NOTE: keep this file ASCII-only.
cd /d "%~dp0.."
if "%~1"=="" exit /b 1
if not exist "data\logs" mkdir "data\logs"
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
set LOG=data\logs\%~1.log

:loop
echo [%date% %time%] ===== start: reelmilly %~1 ===== >> "%LOG%"
echo Reelmilly %~1 is running. Output goes to %LOG%  (close this window to stop it)
".venv\Scripts\reelmilly.exe" %~1 >> "%LOG%" 2>&1
echo [%date% %time%] ===== exited: reelmilly %~1 (code %errorlevel%), restarting in 10 seconds ===== >> "%LOG%"
echo reelmilly %~1 exited (code %errorlevel%). Restarting in 10 seconds...
ping -n 11 127.0.0.1 >nul
goto loop
