@echo off
rem Searches for new jobs without opening the app (used by the daily scheduled task).
set "PY=%USERPROFILE%\.jobhuntai\venv\Scripts\python.exe"
cd /d "%~dp0"
"%PY%" -m jobhunt refresh %*
