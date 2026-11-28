; Inno Setup script for the Nickland ERP Windows installer.
;
;   iscc /DAppVersion=0.1.0 packaging\installer.iss
;
; Installs the frozen application from dist\NicklandERP (built by packaging\nickland.spec)
; into Program Files, with Start menu shortcuts for the application and the LAN host tools.
; Business data lives in each user's local application data folder and is never touched by
; uninstalling.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "Nickland ERP"
#define AppPublisher "Nicholas Nickland Enterprise"
#define AppExe "NicklandERP.exe"
#define HostExe "nickland-host.exe"

[Setup]
AppId={{6F3C2A8E-4B1D-4C7A-9E5F-2D8B7A1C9E40}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\release
OutputBaseFilename=NicklandERP-Setup-{#AppVersion}
SetupIconFile=assets\nickland.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#AppName}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} installer

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\NicklandERP\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\docs\modules\lan.md"; DestDir: "{app}\docs"; DestName: "LAN host installation.md"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{group}\{#AppName} host tools"; Filename: "{cmd}"; Parameters: "/K ""cd /d ""{app}"" && {#HostExe} --help"""; WorkingDir: "{app}"; IconFilename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Open {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
const
  WebViewClient = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';

function WebViewInstalled(): Boolean;
var
  Version: String;
begin
  Result :=
    RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\' + WebViewClient, 'pv', Version) or
    RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\' + WebViewClient, 'pv', Version) or
    RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\' + WebViewClient, 'pv', Version);
  Result := Result and (Version <> '') and (Version <> '0.0.0.0');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and not WebViewInstalled() then
    MsgBox('Nickland ERP draws its window with Microsoft Edge WebView2, which this computer ' +
      'does not appear to have. Windows 10 and 11 normally include it; if the application ' +
      'opens a blank window, install the WebView2 Runtime from ' +
      'https://developer.microsoft.com/microsoft-edge/webview2/ and open it again.',
      mbInformation, MB_OK);
end;
