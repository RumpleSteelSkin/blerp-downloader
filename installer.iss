; installer.iss — Blerp Downloader setup script (Inno Setup 6)
; Signature / publisher: RumpleSteelSkin
;
; Requires Inno Setup 6.3+ (ArchitecturesAllowed=x64compatible).
;
; To build:
;   1) python build.py          (produces the exes into dist/ and writes dist/VERSION.txt)
;   2) Install Inno Setup 6 (winget install JRSoftware.InnoSetup) and run:
;        ISCC installer.iss
;   Output:  dist/installer/BlerpDownloader-Setup-<version>.exe

#define MyAppName "Blerp Downloader"

; The version comes from blerp_downloader/__init__.py, which build.py writes to
; dist/VERSION.txt. CI overrides it explicitly:  ISCC /DMyAppVersion=1.2.3 installer.iss
#ifndef MyAppVersion
  #if FileExists("dist\VERSION.txt")
    #define VerFile FileOpen("dist\VERSION.txt")
    #define MyAppVersion Trim(FileRead(VerFile))
    #expr FileClose(VerFile)
  #else
    ; Obviously-wrong sentinel: run `python build.py` first, or pass /DMyAppVersion=x.y.z
    #define MyAppVersion "0.0.0-local"
  #endif
#endif

#define MyAppPublisher "RumpleSteelSkin"
#define MyAppURL "https://github.com/RumpleSteelSkin/blerp-downloader"
#define MyAppExe "BlerpDownloader.exe"

[Setup]
AppId={{B1E2D0AD-0000-4C0F-9A11-BLERP0000001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppCopyright=By {#MyAppPublisher}
VersionInfoCompany={#MyAppPublisher}
VersionInfoCopyright=By {#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=dist\installer
OutputBaseFilename=BlerpDownloader-Setup-{#MyAppVersion}
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExe}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Per-user install: no UAC prompt, AND winget runs reliably in the user's own context.
PrivilegesRequired=lowest
; In-app updates run Setup silently while the app may still be open: let Restart
; Manager close it cleanly (its WM_CLOSE handler saves settings), but don't let RM
; relaunch it - the /UPDATED=1 [Run] entry below does that deterministically instead.
CloseApplications=yes
RestartApplications=no
SetupMutex=BlerpDownloaderSetupMutex

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\BlerpDownloader.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\blerp.exe";           DestDir: "{app}"; Flags: ignoreversion
Source: "README.md";                DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\{#MyAppName}";        Filename: "{app}\{#MyAppExe}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}";  Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
; In-app update path: the updater runs Setup with /SILENT, which suppresses the
; skipifsilent entry above, so this one relaunches the app instead.
Filename: "{app}\{#MyAppExe}"; Flags: nowait; Check: LaunchAfterUpdate

[Code]
{ If ffmpeg is missing, installs it via winget during setup (the app's one external dependency). }

function LaunchAfterUpdate: Boolean;
begin
  { Set by the in-app updater: BlerpDownloader-Setup-x.y.z.exe /SILENT ... /UPDATED=1 }
  Result := ExpandConstant('{param:UPDATED|0}') = '1';
end;

function CmdSucceeds(const Cmd: string): Boolean;
var
  rc: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'), '/C ' + Cmd + ' >nul 2>&1', '',
                 SW_HIDE, ewWaitUntilTerminated, rc) and (rc = 0);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  rc: Integer;
begin
  if CurStep <> ssPostInstall then
    Exit;
  if CmdSucceeds('where ffmpeg') then
    Exit;  { ffmpeg is already on PATH }

  if CmdSucceeds('where winget') then
  begin
    WizardForm.StatusLabel.Caption :=
      'Installing ffmpeg (winget) - this can take a few minutes...';
    WizardForm.Refresh;
    Exec(ExpandConstant('{cmd}'),
         '/C winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements',
         '', SW_HIDE, ewWaitUntilTerminated, rc);
  end
  else
    MsgBox('ffmpeg not found (winget is not available either).' + #13#10 + #13#10 +
           'This app needs ffmpeg to produce video:' + #13#10 +
           '  - if winget is available:  winget install Gyan.FFmpeg' + #13#10 +
           '  - or download it from:  https://ffmpeg.org/download.html  (add it to PATH)' + #13#10 + #13#10 +
           'Note: the app still opens without ffmpeg; it will guide you again if it is missing.',
           mbInformation, MB_OK);
end;
