; installer.iss — Blerp Downloader kurulum betiği (Inno Setup 6)
; İmza / yayıncı: RumpleSteelSkin
;
; Derlemek için:
;   1) python build.py          (dist/ içine exe'leri üretir)
;   2) Inno Setup 6 kur (winget install JRSoftware.InnoSetup) ve:
;        ISCC installer.iss
;   Çıktı:  dist/installer/BlerpDownloader-Setup-1.0.0.exe

#define MyAppName "Blerp Downloader"
#define MyAppVersion "1.0.0"
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
; Per-user kurulum: UAC gerekmez VE winget kullanıcı bağlamında güvenilir çalışır.
PrivilegesRequired=lowest

[Languages]
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

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

[Code]
{ ffmpeg yoksa kurulum sırasında winget ile kurar (uygulamanın tek dış bağımlılığı). }
{ NOT: kod içi metinler bilinçli ASCII — .iss BOM'suz olduğunda Türkçe karakter bozulmasın diye. }

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
    Exit;  { ffmpeg zaten PATH'te }

  if CmdSucceeds('where winget') then
  begin
    WizardForm.StatusLabel.Caption :=
      'ffmpeg kuruluyor (winget) - birkac dakika surebilir...';
    WizardForm.Refresh;
    Exec(ExpandConstant('{cmd}'),
         '/C winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements',
         '', SW_HIDE, ewWaitUntilTerminated, rc);
  end
  else
    MsgBox('ffmpeg bulunamadi ve winget yok.' + #13#10 +
           'Uygulamanin video uretebilmesi icin ffmpeg kurup PATH e ekleyin:' + #13#10 +
           'https://ffmpeg.org/download.html',
           mbInformation, MB_OK);
end;
