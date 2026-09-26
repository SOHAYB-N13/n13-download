; N13 Download Manager — Inno Setup 7 script
; Produces: release/N13-Download-Manager-Setup.exe

#define MyAppName      "N13 Download Manager"
#define MyAppVersion   GetFileVersion('..\dist\N13\N13.exe')
#define MyAppPublisher "N13"
#define MyAppURL       "https://github.com/SOHAYB-N13/n13-download"
#define MyAppExeName   "N13.exe"
#define MyAppAssocName "N13 Download Manager Protocol"
#define MyAppAssocExt  "dldm"
#define MyAppAssocKey  "dldm"

[Setup]
AppId={{B4A7C9D2-5E1F-4A3B-9C8D-7E6F5A4B3C2D}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DisableDirPage=no
DisableProgramGroupPage=no
OutputDir=..\release
OutputBaseFilename=N13-Download-Manager-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName}
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
VersionInfoVersion={#MyAppVersion}
MinVersion=10.0.17763
InfoBeforeFile=..\installer\webview2_notice.txt

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\N13\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\assets\icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\icon.ico"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\icon.ico"; Tasks: desktopicon

[Registry]
; ── dldm:// protocol (per-user) ─────────────────────────────────────────────
; The command MUST launch the installed application itself and hand it the raw
; protocol URL.  {app} is resolved by the installer to THIS user's chosen
; installation directory, so the registration is correct for every install
; location / drive letter / Windows user.  No developer path is ever written.
;
;   dldm://<url>  ->  "<install dir>\N13.exe" "%1"
;
; N13.exe parses the dldm:// URL from its own command line (build\n13_entry.py).
; browser\dldm_handler.py is an internal module and is never registered here.
Root: HKCU; Subkey: "Software\Classes\{#MyAppAssocKey}";               ValueType: string; ValueName: ""; ValueData: "URL:{#MyAppAssocName}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\{#MyAppAssocKey}";               ValueType: string; ValueName: "URL Protocol"; ValueData: ""; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\{#MyAppAssocKey}\DefaultIcon";   ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"",0"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\{#MyAppAssocKey}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
{ ═══════════════════════════════════════════════════════════════════════════ }
{  dldm:// protocol registration repair                                      }
{                                                                            }
{  Upgrades must work for machines that already have N13 installed —         }
{  including installs where an older build wrote a developer command such as }
{  N13.exe <install>\_internal\browser\dldm_handler.py "%1" or a command      }
{  pointing at a previous installation directory.  Everything here is        }
{  derived from the installer's own application directory, never from a      }
{  hardcoded path, drive letter or Windows user name.                        }
{ ═══════════════════════════════════════════════════════════════════════════ }

const
  ProtocolKey        = 'Software\Classes\{#MyAppAssocKey}';
  ProtocolIconKey    = 'Software\Classes\{#MyAppAssocKey}\DefaultIcon';
  ProtocolCommandKey = 'Software\Classes\{#MyAppAssocKey}\shell\open\command';

var
  { What the PREVIOUS version had registered, captured before anything is
    rewritten, so an upgrade can report exactly what it repaired. }
  InitialProtocolCommand: String;

function ExpectedProtocolCommand(): String;
begin
  Result := '"' + ExpandConstant('{app}\{#MyAppExeName}') + '" "%1"';
end;

function ExpectedProtocolIcon(): String;
begin
  Result := '"' + ExpandConstant('{app}\{#MyAppExeName}') + '",0';
end;

function NormalizeCommand(const Value: String): String;
begin
  Result := Lowercase(Trim(Value));
  StringChangeEx(Result, '/', '\', True);
end;

{ True when a registration is NOT the correct production command: it launches
  an interpreter / developer script, or points at a different directory than
  this installation. }
function IsStaleProtocolCommand(const Command: String): Boolean;
var
  C: String;
begin
  Result := False;
  if Trim(Command) = '' then
  begin
    Result := True;
    Exit;
  end;

  C := Lowercase(Command);
  if (Pos('dldm_handler', C) > 0) or
     (Pos('python.exe', C) > 0) or
     (Pos('pythonw.exe', C) > 0) or
     (Pos('py.exe', C) > 0) or
     (Pos('pyw.exe', C) > 0) or
     (Pos('cmd.exe', C) > 0) or
     (Pos('powershell', C) > 0) or
     (Pos('pwsh.exe', C) > 0) or
     (Pos('\d.py', C) > 0) or
     (Pos('n13_entry.py', C) > 0) then
  begin
    Result := True;
    Exit;
  end;

  { Correct shape but a different install directory (moved / reinstalled). }
  Result := NormalizeCommand(Command) <> NormalizeCommand(ExpectedProtocolCommand());
end;

{ True when a machine-wide entry is recognisably ours and therefore safe to
  remove.  Entries belonging to other applications are never touched. }
function IsOurMachineRegistration(const Command: String): Boolean;
var
  C: String;
begin
  C := Lowercase(Command);
  Result := (Pos('n13', C) > 0) or (Pos('dldm_handler', C) > 0);
end;

procedure RepairProtocolRegistration();
var
  MachineWide: String;
begin
  if InitialProtocolCommand <> '' then
  begin
    if IsStaleProtocolCommand(InitialProtocolCommand) then
      Log('N13: repairing dldm:// registration left by a previous version: '
          + InitialProtocolCommand)
    else
      Log('N13: previous dldm:// registration was already correct: '
          + InitialProtocolCommand);
  end
  else
    Log('N13: no previous dldm:// registration found; creating it.');

  { Unconditional (re)write.  This is what makes an upgrade over a broken
    registration repair itself, regardless of what the previous version wrote. }
  RegWriteStringValue(HKCU, ProtocolKey, '', 'URL:{#MyAppAssocName}');
  RegWriteStringValue(HKCU, ProtocolKey, 'URL Protocol', '');
  RegWriteStringValue(HKCU, ProtocolIconKey, '', ExpectedProtocolIcon());
  RegWriteStringValue(HKCU, ProtocolCommandKey, '', ExpectedProtocolCommand());
  Log('N13: dldm:// -> ' + ExpectedProtocolCommand());

  { Remove a machine-wide (HKLM) entry left behind by a very old build: it can
    shadow the per-user registration for other Windows users.  Only removed
    when it is recognisably an N13 registration. }
  if RegQueryStringValue(HKLM, ProtocolCommandKey, '', MachineWide) then
  begin
    if IsOurMachineRegistration(MachineWide) then
    begin
      if RegDeleteKeyIncludingSubkeys(HKLM, ProtocolKey) then
        Log('N13: removed stale machine-wide dldm:// registration: ' + MachineWide)
      else
        Log('N13: could not remove machine-wide dldm:// registration (not elevated).');
    end
    else
      Log('N13: machine-wide dldm:// key belongs to another application; left untouched.');
  end;
end;

procedure RemoveProtocolRegistration();
var
  MachineWide: String;
begin
  { The [Registry] entries carry "uninsdeletekey", so the per-user key is
    removed by the uninstaller.  This is an explicit belt-and-braces pass so
    that no broken protocol registration is ever left behind. }
  RegDeleteKeyIncludingSubkeys(HKCU, ProtocolKey);

  if RegQueryStringValue(HKLM, ProtocolCommandKey, '', MachineWide) then
  begin
    if IsOurMachineRegistration(MachineWide) then
      RegDeleteKeyIncludingSubkeys(HKLM, ProtocolKey);
  end;
end;

function IsWebView2Installed(): Boolean;
var
  RegPath: String;
begin
  RegPath := 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  Result := RegKeyExists(HKEY_LOCAL_MACHINE, RegPath);
  if not Result then
  begin
    RegPath := 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
    Result := RegKeyExists(HKEY_LOCAL_MACHINE, RegPath);
  end;
end;

function InitializeSetup(): Boolean;
var
  MsgResult: Integer;
  ErrorCode: Integer;
begin
  Result := True;

  { Capture the previous registration BEFORE anything rewrites it. }
  if not RegQueryStringValue(HKCU, ProtocolCommandKey, '', InitialProtocolCommand) then
    InitialProtocolCommand := '';

  if not IsWebView2Installed() then
  begin
    MsgResult := MsgBox(
      'Microsoft Edge WebView2 Runtime was not detected on this computer.' + #13#10 +
      'N13 Download Manager requires WebView2 to run.' + #13#10 +
      'Would you like to open the WebView2 download page now?' + #13#10 +
      '(Choose No to continue installation, but the application may not launch until WebView2 is installed.)',
      mbConfirmation, MB_YESNO);
    if MsgResult = IDYES then
    begin
      ShellExec('open', 'https://go.microsoft.com/fwlink/p/?LinkId=2124703', '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
      Result := False;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    RepairProtocolRegistration();
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RemoveProtocolRegistration();

  if CurUninstallStep = usPostUninstall then
  begin
    MsgBox(
      'Application files have been removed.' + #13#10 +
      'Your downloads, settings, history, and database in %LOCALAPPDATA%\N13 have been preserved.',
      mbInformation, MB_OK);
  end;
end;
