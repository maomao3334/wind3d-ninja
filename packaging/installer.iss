#define MyAppName "Wind3D"
#ifndef MyAppVersion
#define MyAppVersion "1.1.1"
#endif
#define MyAppPublisher "wind3d-ninja contributors"
#define MyAppExeName "Wind3D.exe"

[Setup]
AppId={{DD205901-6002-4EC4-9B73-24F3302BE57D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Wind3D
DefaultGroupName=Wind3D
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\installer_output
OutputBaseFilename=Wind3D-Ninja-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
VersionInfoVersion={#MyAppVersion}.0
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Files]
Source: "..\installer_stage\Wind3D\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Wind3D"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{group}\Wind3D User Guide"; Filename: "{app}\docs\user_guide_zh.md"; WorkingDir: "{app}\docs"
Name: "{autodesktop}\Wind3D"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: checkedonce

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Wind3D"; Flags: nowait postinstall skipifsilent
