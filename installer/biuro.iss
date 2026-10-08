#define MyAppName "Biuro"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Biuro"
#define MyAppExeName "Biuro.exe"

[Setup]
AppId={{E1D2A7C9-8B4A-4DB7-9F6D-5A6F0A3C1B20}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=output
OutputBaseFilename=Biuro-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#MyAppExeName}

[Files]
; Kopiuje CAŁY folder dist/Biuro
Source: "..\dist\Biuro\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Gwarantuje, że .env trafi do katalogu instalacji
Source: "..\dist\Biuro\.env"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Uruchom {#MyAppName}"; Flags: nowait postinstall skipifsilent
