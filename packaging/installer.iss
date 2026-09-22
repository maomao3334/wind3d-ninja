#define MyAppName "Wind3D Ninja"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "wind3d-ninja contributors"
#define MyAppExeName "Wind3D-Ninja.exe"

[Setup]
AppId={{B8F17C10-C50C-4D43-9D25-4184EA315EF6}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Wind3D-Ninja
DefaultGroupName=Wind3D Ninja
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\installer_output
OutputBaseFilename=Wind3D-Ninja-Setup-1.0.0-x64
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\ui\{#MyAppExeName}
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
VersionInfoVersion=1.0.0.0
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}

[Files]
Source: "..\installer_stage\ui\*"; DestDir: "{app}\ui"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\installer_stage\cli\*"; DestDir: "{app}\cli"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\installer_stage\windninja\*"; DestDir: "{app}\windninja"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\installer_stage\README-FIRST.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES\*"; DestDir: "{app}\THIRD_PARTY_NOTICES"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Wind3D Ninja"; Filename: "{app}\ui\{#MyAppExeName}"; WorkingDir: "{app}\ui"
Name: "{group}\Wind3D Ninja CLI"; Filename: "{cmd}"; Parameters: "/K ""{app}\cli\wind3d-ninja.exe --help"""; WorkingDir: "{app}\cli"
Name: "{group}\卸载 Wind3D Ninja"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Wind3D Ninja"; Filename: "{app}\ui\{#MyAppExeName}"; WorkingDir: "{app}\ui"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："; Flags: checkedonce

[Run]
Filename: "{app}\ui\{#MyAppExeName}"; Description: "启动 Wind3D Ninja"; Flags: nowait postinstall skipifsilent
