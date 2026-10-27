@echo off
rem Builds JobQuestAI.exe in this folder (one file: Python, server and UI inside). Build files go to %USERPROFILE%\.jobhuntai\build.
setlocal
set "PY=%USERPROFILE%\.jobhuntai\venv\Scripts\python.exe"
if not exist "%PY%" ( echo Run setup.cmd first. & pause & exit /b 1 )
set "SRC=%~dp0"
"%PY%" -m pip install --disable-pip-version-check -q pyinstaller pillow || exit /b 1
"%PY%" "%SRC%packaging\make_icon.py" || exit /b 1
"%PY%" -m PyInstaller --noconfirm --clean --onefile --noconsole --name JobQuestAI ^
  --icon "%SRC%packaging\jobquestai.ico" ^
  --add-data "%SRC%jobhunt\static;jobhunt\static" ^
  --add-data "%SRC%config.toml;." ^
  --collect-submodules uvicorn --collect-data docx ^
  --paths "%SRC%." ^
  --workpath "%USERPROFILE%\.jobhuntai\build" --specpath "%USERPROFILE%\.jobhuntai\build" --distpath "%SRC%." ^
  "%SRC%packaging\launcher.py" || exit /b 1
echo.
echo Built: %SRC%JobQuestAI.exe
