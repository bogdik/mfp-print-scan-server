; Inno Setup script for MFP Print & Scan Server (Windows).
;
; Build with Inno Setup 6 (https://jrsoftware.org/isinfo.php):
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" packaging\windows\mfp-print-scan-server.iss
; Output: dist\mfp-print-scan-server-setup-<version>.exe
;
; This wraps the *existing*, already-working scripts (start.ps1,
; scripts\register_service.ps1, scripts\unregister_service.ps1) instead of
; reimplementing service/task/firewall setup in Pascal Script: one source of
; truth for that logic, whether someone runs it by hand or through this
; installer.
;
; Python itself is NOT bundled — same prerequisite as the manual install
; (README, "Windows"): python.org's Python 3.11+, "Add python.exe to PATH"
; ticked. setup checks for it before copying anything (see InitializeSetup
; below) so a missing Python fails fast with a clear message instead of
; installing files that won't run.
;
; NOT built or run here — there is no Windows machine or Inno Setup install
; in the environment this was written in. Written carefully and against the
; documented Inno Setup 6 behavior, but treat it as untested until someone
; actually compiles and runs the installer on real Windows.

#define MyAppName "MFP Print & Scan Server"
#define MyAppPublisher "bogdik"
#define MyAppURL "https://github.com/bogdik/mfp-print-scan-server"
#define MyAppVersion Trim(FileRead(FileOpen(SourcePath + "..\..\VERSION")))

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
; data/, logs/ and .venv/ into this same folder at run time, as the signed-in
; user (the scheduled task register_service.ps1 sets up runs as that user,
; not as SYSTEM) — it needs to be writable without admin rights every time
; it starts, not just during setup.
; Caveat: combined with PrivilegesRequired=admin below, {localappdata} is the
; profile of whoever approves the UAC prompt. On the common single-admin-user
; PC that's the same person installing it, so this is fine; on a shared
; machine where a *different* account approves elevation, files would land
; in that account's profile instead — unverified on real Windows either way.
DefaultDirName={localappdata}\MFP Print & Scan Server
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; register_service.ps1 (Scheduled Task + firewall rules) needs admin; ask
; for it once, up front, rather than mid-install.
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist
OutputBaseFilename=mfp-print-scan-server-setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#MyAppName}
DisableWelcomePage=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Files]
Source: "..\..\app\*"; DestDir: "{app}\app"; Excludes: "__pycache__,*.pyc"; Flags: recursesubdirs ignoreversion
Source: "..\..\scripts\*"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "..\..\run.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\config.example.ini"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\start.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\start.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\register_service.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\unregister_service.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\README.ru.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Open web UI"; Filename: "{app}\open-web-ui.url"
Name: "{group}\Start (console window)"; Filename: "{app}\start.bat"; WorkingDir: "{app}"
Name: "{group}\Start automatically with Windows"; Filename: "{app}\register_service.bat"; WorkingDir: "{app}"
Name: "{group}\Stop starting automatically"; Filename: "{app}\unregister_service.bat"; WorkingDir: "{app}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

[Run]
; First-time setup (venv, dependencies, scheduled task, firewall, start) runs
; from CurStepChanged below rather than here, so its exit code can be checked.
Filename: "{app}\open-web-ui.url"; Description: "Open the web UI"; Flags: postinstall shellexec skipifsilent

[UninstallRun]
; -NoPause: the script otherwise waits for Enter, in a window that's hidden.
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; \
    Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\unregister_service.ps1"" -NoPause"; \
    WorkingDir: "{app}"; RunOnceId: "UnregisterMfpService"; Flags: runhidden waituntilterminated

[UninstallDelete]
; .venv (installed dependencies) is safe to remove; config.ini, data/, scans/
; and uploads/ are the user's own settings and print/scan history and are
; deliberately NOT listed here, so uninstalling never deletes them silently
; (matching the .deb/.rpm postrm/postun — see packaging docs).
Type: filesandordirs; Name: "{app}\.venv"
; Only the program's own code, but Python leaves __pycache__ folders in it at
; run time that the uninstaller didn't install and so wouldn't remove.
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\logs"
Type: files; Name: "{app}\open-web-ui.url"

[Code]
function PythonFound(): Boolean;
var
  ResultCode: Integer;
begin
  // The same interpreters start.ps1 tries (the python.org launcher py, then
  // "python"), actually run: "where python.exe" would also find the
  // Microsoft Store stub, which isn't Python and exits 9009 when run.
  // Exits 0 only for a real Python 3.11+.
  Result := Exec('py.exe', '-3 -c "import sys; sys.exit(sys.version_info < (3, 11))"', '',
                 SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
  if not Result then
    Result := Exec('python.exe', '-c "import sys; sys.exit(sys.version_info < (3, 11))"', '',
                   SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not PythonFound() then
  begin
    MsgBox('Python 3.11+ wasn''t found. Install it from https://www.python.org/downloads/windows/ ' +
           '(tick "Add python.exe to PATH" during installation), then run this setup again.',
           mbError, MB_OK);
    Result := False;
  end;
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

  // First-time setup mirrors "Steps" in README, "Windows": create the venv
  // and install dependencies (needs internet access), then register the
  // scheduled task, firewall rules, and start the server — exactly
  // register_service.bat's own logic. -NoPause: it would otherwise wait for
  // Enter in a hidden window, and setup would hang forever.
  WizardForm.StatusLabel.Caption := 'Setting up the Python environment and the background service...';
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
              ExpandConstant('-NoProfile -ExecutionPolicy Bypass -File "{app}\scripts\register_service.ps1" -NoPause'),
              ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode) or (ResultCode <> 0) then
    MsgBox('The files are installed, but setting up the background service failed ' +
           '(exit code ' + IntToStr(ResultCode) + '). Most often that''s no internet access for ' +
           'installing the Python packages, or port 8000 already in use.' + #13#10#13#10 +
           'Run "Start automatically with Windows" from the Start menu to retry and see the error.',
           mbError, MB_OK);
end;
