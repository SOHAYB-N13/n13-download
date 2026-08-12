; N13 Download Manager — Inno Setup 7 script
; Produces: release/N13-Download-Manager-Setup.exe

#define MyAppName      "N13 Download Manager"
#define MyAppVersion   GetFileVersion('..\dist\N13\N13.exe')
#define MyAppPublisher "N13"
#define MyAppURL       "https://github.com/SOHYAB-N13/n13-download"
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
Root: HKCU; Subkey: "Software\Classes\{#MyAppAssocKey}";               ValueType: string; ValueName: ""; ValueData: "URL:{#MyAppAssocName}"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\{#MyAppAssocKey}";               ValueType: string; ValueName: "URL Protocol"; ValueData: ""; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\{#MyAppAssocKey}\DefaultIcon";   ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"",0"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\{#MyAppAssocKey}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""; Flags: uninsdeletekey

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
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

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    MsgBox(
      'Application files have been removed.' + #13#10 +
      'Your downloads, settings, history, and database in %LOCALAPPDATA%\N13 have been preserved.',
      mbInformation, MB_OK);
  end;
end;
