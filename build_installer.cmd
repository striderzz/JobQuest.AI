@echo off
rem Builds dist\JobQuestAI-Setup-<version>.exe from JobQuestAI.exe (run build_exe.cmd first). Needs Inno Setup 6:
rem   winget install JRSoftware.InnoSetup
setlocal
set "SRC=%~dp0"
set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" ( echo Inno Setup 6 not found. Install it with: winget install JRSoftware.InnoSetup & exit /b 1 )
if not exist "%SRC%JobQuestAI.exe" ( echo Build the app first: build_exe.cmd & exit /b 1 )
"%ISCC%" /Q "%SRC%packaging\installer.iss" || exit /b 1
echo Built installer in %SRC%dist
