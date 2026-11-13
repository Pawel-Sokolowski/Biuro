#define MyAppName "Biuro"
#define VersionHandle FileOpen(SourcePath + "..\app\VERSION")
#define MyAppVersion Trim(FileRead(VersionHandle))
#expr FileClose(VersionHandle)
#define MyAppPublisher "Biuro"
#define MyAppExeName "Biuro.exe"
#define MyBuildDir SourcePath + "..\dist\Biuro"
#define MyOdbcMsi SourcePath + "prerequisites\msodbcsql18-x64.msi"

#ifnexist MyBuildDir + "\Biuro.exe"
  #error "Build dist\Biuro first: python -m PyInstaller --clean --noconfirm packaging\biuro.spec"
#endif

#ifnexist MyOdbcMsi
  #error "Prepare the signed driver first: powershell -File scripts\prepare_odbc_prerequisite.ps1"
#endif

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
ArchitecturesAllowed=x64os
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\{#MyAppExeName}

[Types]
Name: "program"; Description: "Program Biuro"
Name: "custom"; Description: "Wybór programu i workera"; Flags: iscustom

[Components]
Name: "program"; Description: "Program Biuro — stanowisko użytkownika"; Types: program
Name: "worker"; Description: "Worker na tym serwerze — zadania w tle i panel konfiguracji"

[Files]
Source: "..\scripts\export_platnik_schema.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Components: worker
Source: "..\scripts\export_platnik_schema.cmd"; DestDir: "{app}\scripts"; Flags: ignoreversion; Components: worker
Source: "..\scripts\install_background_worker.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion; Components: worker
Source: "..\scripts\install_background_worker.cmd"; DestDir: "{app}\scripts"; Flags: ignoreversion; Components: worker
Source: "..\scripts\WORKER-SETUP.txt"; DestDir: "{app}\scripts"; Flags: ignoreversion; Components: worker
; Original signed Microsoft MSI, extracted only for the selected prerequisite.
Source: "{#MyOdbcMsi}"; Flags: dontcopy
Source: "{#MyBuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Szablon konfiguracji w folderze uzytkownika (nie nadpisuje istniejacego, zostaje po odinstalowaniu)
Source: "..\.env.example"; DestDir: "{localappdata}\Biuro"; DestName: ".env.example"; Flags: onlyifdoesntexist uninsneveruninstall

[Icons]
Name: "{autoprograms}\Biuro - eksport struktury Płatnika"; Filename: "{app}\scripts\export_platnik_schema.cmd"; WorkingDir: "{app}"; Components: worker
Name: "{autoprograms}\{#MyAppName} - konfiguracja workera"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--worker-setup"; WorkingDir: "{app}"; Components: worker
Name: "{autoprograms}\{#MyAppName} Worker"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--worker-panel"; WorkingDir: "{app}"; Components: worker
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; AppUserModelID: "Biuro.OfficeManager"; Components: program
Name: "{autoprograms}\{#MyAppName} - aktualizacja bazy"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--migrate"; Components: program
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; Components: program

Name: "{autoprograms}\{#MyAppName} - aktualizacja bazy workera"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--worker-migrate"; Components: worker

[Tasks]
Name: "odbcdriver"; Description: "Zainstaluj Microsoft ODBC Driver 18 dla połączenia Enova (wymaga uprawnień administratora)"; GroupDescription: "Połączenie Enova:"; Check: NeedsOdbcDriver
Name: "desktopicon"; Components: program; Description: "Utwórz skrót na pulpicie"; GroupDescription: "Dodatkowe skróty:"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Uruchom {#MyAppName}"; Flags: nowait postinstall skipifsilent; Components: program; Check: CanLaunchBiuro
Filename: "{app}\{#MyAppExeName}"; Parameters: "--worker-setup"; Description: "Skonfiguruj i zainstaluj worker"; Flags: nowait postinstall skipifsilent; Components: worker; Check: CanLaunchBiuro

[Code]
var
  OdbcRestartRequired: Boolean;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpSelectComponents) and
     not WizardIsComponentSelected('program') and not WizardIsComponentSelected('worker') then
  begin
    MsgBox('Wybierz program Biuro, worker lub oba składniki.', mbError, MB_OK);
    Result := False;
  end;
end;

function DriverInstalled(const DriverName: String): Boolean;
var
  Status: String;
begin
  Result := RegQueryStringValue(HKLM64,
    'SOFTWARE\ODBC\ODBCINST.INI\ODBC Drivers', DriverName, Status);
  if Result then
    Result := CompareText(Status, 'Installed') = 0;
end;

function NeedsOdbcDriver: Boolean;
begin
  Result := not (DriverInstalled('ODBC Driver 18 for SQL Server') or
                 DriverInstalled('ODBC Driver 17 for SQL Server'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Arguments: String;
  ExitCode: Integer;
begin
  Result := '';
  if not NeedsOdbcDriver then
    Exit;
  if not WizardIsTaskSelected('odbcdriver') then
    Exit;
  ExtractTemporaryFile('msodbcsql18-x64.msi');
  Arguments := '/i "' + ExpandConstant('{tmp}\msodbcsql18-x64.msi') + '" /norestart';
  { Microsoft's interactive wizard handles licence acceptance. Silent deployment
    explicitly accepts the licence for the prerequisite. }
  if WizardSilent then
    Arguments := Arguments + ' /qn IACCEPTMSODBCSQLLICENSETERMS=YES';
  Log('Installing the Microsoft ODBC prerequisite with elevation.');
  if not ShellExec('runas', ExpandConstant('{sys}\msiexec.exe'), Arguments,
                   '', SW_SHOWNORMAL, ewWaitUntilTerminated, ExitCode) then
  begin
    Result := 'Nie udało się uruchomić instalatora sterownika ODBC. Zezwól na uprawnienia administratora lub odznacz instalację sterownika.';
    Exit;
  end;
  Log(Format('Microsoft ODBC installer exit code: %d', [ExitCode]));
  if (ExitCode <> 0) and (ExitCode <> 3010) then
  begin
    Result := Format('Instalacja sterownika ODBC została anulowana lub nie powiodła się (kod %d). Ponów instalację lub odznacz tę opcję.', [ExitCode]);
    Exit;
  end;
  OdbcRestartRequired := OdbcRestartRequired or (ExitCode = 3010);
  if NeedsOdbcDriver then
    Result := 'Po instalacji nie wykryto sterownika ODBC x64. Sprawdź instalację Microsoft ODBC Driver 18.';
end;

function NeedRestart: Boolean;
begin
  Result := OdbcRestartRequired;
end;

function CanLaunchBiuro: Boolean;
begin
  Result := not OdbcRestartRequired;
end;
