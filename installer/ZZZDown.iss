#define MyAppName "ZZZDown"
#define MyAppVersion "0.1.0"
#define MyAppExeName "ZZZDown.exe"

[Setup]
AppId={{7FCDB1A1-B46E-4A32-B0D7-D7F4A9F6E1B4}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=..\dist
OutputBaseFilename=ZZZDown-Windows-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}

[Files]
Source: "..\dist\ZZZDown\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\ZZZDown"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\ZZZDown"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch ZZZDown"; Flags: nowait postinstall skipifsilent

