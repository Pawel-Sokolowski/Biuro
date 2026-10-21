#define MyAppName "Biuro"
#define MyAppVersion "1.3.0"
#define MyAppPublisher "Biuro"
#define MyAppExeName "Biuro.exe"

[Languages]
Name: "polish"; MessagesFile: "compiler:Languages\Polish.isl"

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
Source: "..\dist\Biuro\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Szablon konfiguracji w folderze uzytkownika (nie nadpisuje istniejacego, zostaje po odinstalowaniu)
Source: "..\.env.example"; DestDir: "{localappdata}\Biuro"; DestName: ".env.example"; Flags: onlyifdoesntexist uninsneveruninstall

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autoprograms}\{#MyAppName} - aktualizacja bazy"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--migrate"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Utwórz skrót na pulpicie"; GroupDescription: "Dodatkowe skróty:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Uruchom {#MyAppName}"; Flags: nowait postinstall skipifsilent
