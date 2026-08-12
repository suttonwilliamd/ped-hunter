#define MyAppName "PED Hunter"
#define MyAppVersion "0.3.31"
#define MyAppPublisher "suttonwilliamd"
#define MyAppExeName "PED-Hunter.exe"

[Setup]
AppId={{B5E0A6E2-3F02-4EF6-9A35-8D5B4C5A5B30}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\PED Hunter
DefaultGroupName={#MyAppName}
OutputDir=..\dist
OutputBaseFilename=PED-Hunter-Setup-v{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#MyAppExeName}

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autodesktop}\PED Hunter"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"
Name: "{group}\PED Hunter"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch PED Hunter"; Flags: nowait postinstall skipifsilent
