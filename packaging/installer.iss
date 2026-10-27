; Inno Setup script for JobQuest.AI. Build with build_installer.cmd (needs Inno Setup 6 and a built JobQuestAI.exe).
; Installs per user (no admin prompt) into %LOCALAPPDATA%\Programs\JobQuest.AI with Start menu and optional Desktop
; shortcuts. Uninstalling removes the program but keeps your data in %USERPROFILE%\.jobhuntai.

#define AppName "JobQuest.AI"
#define AppVersion "1.0.0"
#define AppExe "JobQuestAI.exe"

[Setup]
AppId={{6F0B7C1E-3A52-4E7B-9C1D-5A2E8F4B7D31}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=striderzz
AppPublisherURL=https://github.com/striderzz/JobQuest.AI
AppSupportURL=https://github.com/striderzz/JobQuest.AI/issues
AppUpdatesURL=https://github.com/striderzz/JobQuest.AI/releases
AppComments=Your AI co-pilot for the job hunt
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=JobQuestAI-Setup-{#AppVersion}
SetupIconFile=jobquestai.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &Desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "dailysearch"; Description: "Search for new jobs automatically every 12 hours (09:00 and 21:00)"; GroupDescription: "Background:"; Flags: unchecked

[Files]
Source: "..\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Your AI co-pilot for the job hunt"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; optional 12-hour search: a per-user scheduled task that runs the app's own "refresh" command
Filename: "schtasks.exe"; Parameters: "/Create /F /TN ""JobQuest.AI search 09-00"" /SC DAILY /ST 09:00 /TR ""\""{app}\{#AppExe}\"" refresh --open-if-new"""; Flags: runhidden; Tasks: dailysearch
Filename: "schtasks.exe"; Parameters: "/Create /F /TN ""JobQuest.AI search 21-00"" /SC DAILY /ST 21:00 /TR ""\""{app}\{#AppExe}\"" refresh --open-if-new"""; Flags: runhidden; Tasks: dailysearch
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "schtasks.exe"; Parameters: "/Delete /F /TN ""JobQuest.AI search 21-00"""; Flags: runhidden; RunOnceId: "DelTaskEvening"
Filename: "schtasks.exe"; Parameters: "/Delete /F /TN ""JobQuest.AI search 09-00"""; Flags: runhidden; RunOnceId: "DelTaskMorning"
Filename: "{cmd}"; Parameters: "/c taskkill /F /IM {#AppExe} >nul 2>&1"; Flags: runhidden; RunOnceId: "StopApp"
