@echo off
rem One-time setup: creates the Python environment in %USERPROFILE%\.jobhuntai (outside OneDrive) and installs packages.
setlocal
set "HOMEDIR=%USERPROFILE%\.jobhuntai"
if not exist "%HOMEDIR%\venv\Scripts\python.exe" (
  echo Creating Python environment in %HOMEDIR%\venv ...
  py -3 -m venv "%HOMEDIR%\venv" || (echo Python 3.11+ is needed: https://www.python.org/downloads/ & pause & exit /b 1)
)
"%HOMEDIR%\venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r "%~dp0requirements.txt" || (pause & exit /b 1)
echo.
echo Done. Double-click start.cmd to open JobQuest.AI.
pause
