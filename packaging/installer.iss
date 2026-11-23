; Inno Setup script. Build with:  iscc /DAppVersion=1.2.3 packaging\installer.iss
; (packaging\build.ps1 does everything, including PyInstaller)

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#define AppName "Refract"
#define AppExe "Refract.exe"

[Setup]
; Never change this id: it is how an update recognises the installed copy and replaces it.
AppId={{6C1E7A52-0B84-4D3E-9F5A-2B7D1C8A4E91}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\Refract
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist-installer
OutputBaseFilename=Refract-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}
SetupIconFile=icon.ico
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\Refract\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; offer to start it
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent
; the in-app updater runs this setup silently with /RELAUNCH=1 and wants the app back afterwards
Filename: "{app}\{#AppExe}"; Flags: nowait runasoriginaluser; Check: RelaunchRequested

[Code]
function RelaunchRequested: Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;

procedure StopRunningApp;
var
  ResultCode: Integer;
begin
  { the app has no window to close, so end the process (running simulations are marked interrupted) }
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM {#AppExe}', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(500);
end;

{ The app window is shown by the Microsoft Edge WebView2 runtime: part of Windows 11 and of up-to-date
  Windows 10, otherwise installed here. Without it the app still works, in the default browser. }
function WebView2Installed: Boolean;
var
  Version: String;
  Key: String;
begin
  Key := 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  Result :=
    (RegQueryStringValue(HKLM32, Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKLM64, Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0')) or
    (RegQueryStringValue(HKCU, Key, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;

procedure InstallWebView2IfMissing;
var
  ResultCode: Integer;
  Setup: String;
begin
  if WebView2Installed then
    Exit;
  Setup := ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe');
  try
    WizardForm.StatusLabel.Caption := 'Downloading the Microsoft Edge WebView2 runtime...';
    DownloadTemporaryFile('https://go.microsoft.com/fwlink/p/?LinkId=2124703', 'MicrosoftEdgeWebview2Setup.exe', '', nil);
    WizardForm.StatusLabel.Caption := 'Installing the Microsoft Edge WebView2 runtime...';
    Exec(Setup, '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  except
    { offline or blocked: carry on, the app falls back to the browser }
    Log('WebView2 runtime could not be installed: ' + GetExceptionMessage);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopRunningApp;
  InstallWebView2IfMissing;
  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    StopRunningApp;
end;


