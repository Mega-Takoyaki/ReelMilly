@echo off
rem Runs "claude remote-control" (Remote Control server mode) in the project folder and restarts it if it exits.
rem This lets the Claude mobile app / claude.ai/code reach this PC right after logon, without typing /rc.
rem Output goes to data\logs\claude-remote.log.
rem   run-claude-remote.bat            run now
rem   run-claude-remote.bat /startup   for auto-start at logon (waits a little for the network first)
rem NOTE: keep this file ASCII-only.
cd /d "%~dp0.."
if not exist "data\logs" mkdir "data\logs"
set LOG=data\logs\claude-remote.log
set CLAUDE=%USERPROFILE%\.local\bin\claude.exe
if not exist "%CLAUDE%" (
  echo [%date% %time%] claude.exe not found: %CLAUDE% >> "%LOG%"
  exit /b 1
)

rem Do not start a second one.
tasklist /v /fi "imagename eq cmd.exe" | find "Reelmilly Claude Remote" >nul && exit /b 0
title Reelmilly Claude Remote
if /i "%~1"=="/startup" ping -n 31 127.0.0.1 >nul

:loop
echo [%date% %time%] ===== start: claude remote-control ===== >> "%LOG%"
echo Claude Remote Control is running. Output goes to %LOG%  (close this window to stop it)
"%CLAUDE%" remote-control --name "ReelMilly" --permission-mode acceptEdits --spawn=same-dir >> "%LOG%" 2>&1
echo [%date% %time%] ===== exited: claude remote-control (code %errorlevel%), restarting in 30 seconds ===== >> "%LOG%"
echo claude remote-control exited (code %errorlevel%). Restarting in 30 seconds...
ping -n 31 127.0.0.1 >nul
goto loop
