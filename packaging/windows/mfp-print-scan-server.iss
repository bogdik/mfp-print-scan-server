; Inno Setup script for MFP Print & Scan Server (Windows).
;
; Packs the standalone PyInstaller build, so the target machine needs no
; Python. Build it first, then compile this with Inno Setup 6 or 7
; (https://jrsoftware.org/isinfo.php):
;   powershell -ExecutionPolicy Bypass -File packaging\windows\build-exe.ps1
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\windows\mfp-print-scan-server.iss
; Output: dist\mfp-print-scan-server-setup-<version>.exe
;
; This wraps the *existing*, already-working scripts (start.ps1,
; scripts\register_service.ps1, scripts\unregister_service.ps1 — they run
; mfp-server.exe when it's next to them) instead of reimplementing
; task/firewall setup in Pascal Script: one source of truth for that logic,
; whether someone runs it by hand or through this installer.
;
; Tested: compiled with Inno Setup 7.1, installed and uninstalled on
; Windows 10 (task, firewall rules, shortcuts, server start; uninstall keeps
; config.ini and the user's data).

#define MyAppName "MFP Print & Scan Server"
#define MyAppPublisher "bogdik"
#define MyAppURL "https://github.com/bogdik/mfp-print-scan-server"
#define MyAppVersion Trim(FileRead(FileOpen(SourcePath + "..\..\VERSION")))
#define BuildDir "..\..\dist\mfp-server"

#if !FileExists(SourcePath + BuildDir + "\mfp-server.exe")
  #error "dist\mfp-server\mfp-server.exe not found - run packaging\windows\build-exe.ps1 first"
#endif

[Setup]
; Fixed AppId (a random GUID, generated once for this project) so upgrades
; over an existing install are recognized as upgrades, not a second install.
AppId={{B36F1A0E-6E9E-4C7A-9F2A-2B9E7B7C4E11}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
; Per-user AppData, not Program Files: the server writes its own config.ini,
; data/, scans/, uploads/ and logs/ next to mfp-server.exe at run time, as
; the signed-in user (the scheduled task register_service.ps1 sets up runs as
; that user, not as SYSTEM) — it needs to be writable without admin rights
; every time it starts, not just during setup.
; Caveat: combined with PrivilegesRequired=admin below, {localappdata} is the
; profile of whoever approves the UAC prompt. On the common single-admin-user
; PC that's the same person installing it, so this is fine; on a shared
; machine where a *different* account approves elevation, files would land
; in that account's profile instead.
DefaultDirName={localappdata}\MFP Print & Scan Server
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; register_service.ps1 (Scheduled Task + firewall rules) needs admin; ask
; for it once, up front, rather than mid-install.
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist
OutputBaseFilename=mfp-print-scan-server-setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\mfp-server.exe
DisableWelcomePage=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Files]
; Everything build-exe.ps1 put together: mfp-server.exe, _internal\, the
; scripts, config.example.ini and docs. config.ini itself isn't shipped — the
; server creates it from config.example.ini on first start and an upgrade
; must never overwrite the user's copy.
Source: "{#BuildDir}\*"; DestDir: "{app}"; Excludes: "config.ini"; Flags: recursesubdirs ignoreversion

[InstallDelete]
; Upgrading over the older Python-based install: its code and venv aren't
; used any more (the scripts prefer mfp-server.exe). Data stays.
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\app"
Type: files; Name: "{app}\run.py"
Type: files; Name: "{app}\requirements.txt"
; _internal\ of a previous version: replaced wholesale, no stale modules.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{group}\Open web UI"; Filename: "{app}\open-web-ui.url"
Name: "{group}\Start (console window)"; Filename: "{app}\start.bat"; WorkingDir: "{app}"
Name: "{group}\Start automatically with Windows"; Filename: "{app}\register_service.bat"; WorkingDir: "{app}"
Name: "{group}\Stop starting automatically"; Filename: "{app}\unregister_service.bat"; WorkingDir: "{app}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

[Run]
; The background task (scheduled task, firewall, start) is set up from
; CurStepChanged below rather than here, so its exit code can be checked.
Filename: "{app}\open-web-ui.url"; Description: "Open the web UI"; Flags: postinstall shellexec skipifsilent

[UninstallRun]
; -NoPause: the script otherwise waits for Enter, in a window that's hidden.
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\unregister_service.ps1"" -NoPause"; \
    WorkingDir: "{app}"; RunOnceId: "UnregisterMfpService"; Flags: runhidden waituntilterminated

[UninstallDelete]
; config.ini, data/, scans/ and uploads/ are the user's own settings and
; print/scan history and are deliberately NOT listed here, so uninstalling
; never deletes them silently (matching the .deb/.rpm postrm/postun).
Type: filesandordirs; Name: "{app}\logs"
Type: files; Name: "{app}\open-web-ui.url"

[Code]
function RunScript(const Script: String; var ResultCode: Integer): Boolean;
begin
  // -NoPause: the scripts would otherwise wait for Enter in a hidden window.
  Result := Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
                 '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\scripts\') + Script + '" -NoPause',
                 ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Result := '';
  // Upgrade: a running server holds its files (mfp-server.exe, _internal\)
  // open, so stop it and drop its task first; CurStepChanged sets it up again.
  if FileExists(ExpandConstant('{app}\scripts\unregister_service.ps1')) then
    RunScript('unregister_service.ps1', ResultCode);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep <> ssPostInstall then
    Exit;
  // A small .url shortcut target for the "Open web UI" Start Menu icon —
  // written directly rather than shelled out to cmd.exe, so there's no
  // quoting to get wrong. Port 8000 is this project's documented default
  // (config.example.ini); a custom port needs its own bookmark.
  SaveStringToFile(ExpandConstant('{app}\open-web-ui.url'),
    '[InternetShortcut]' + #13#10 + 'URL=http://localhost:8000/' + #13#10, False);

  // Register the scheduled task and firewall rules and start the server —
  // exactly register_service.bat's own logic.
  WizardForm.StatusLabel.Caption := 'Setting up the background service...';
  if not RunScript('register_service.ps1', ResultCode) or (ResultCode <> 0) then
    MsgBox('The files are installed, but setting up the background service failed ' +
           '(exit code ' + IntToStr(ResultCode) + '). Most often port 8000 is already in use, ' +
           'e.g. by the server still running in a start.bat window.' + #13#10#13#10 +
           'Run "Start automatically with Windows" from the Start menu to retry and see the error.',
           mbError, MB_OK);
end;
