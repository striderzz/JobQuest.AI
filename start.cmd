@echo off
rem Opens JobQuest.AI in your browser. Keep this window open while you use it; close it to stop.
title JobQuest.AI
set "PY=%USERPROFILE%\.jobhuntai\venv\Scripts\python.exe"
if not exist "%PY%" ( echo First run setup.cmd once. & pause & exit /b 1 )
cd /d "%~dp0"
"%PY%" -m jobhunt serve
if errorlevel 1 pause
