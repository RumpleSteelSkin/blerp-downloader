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
