# PyInstaller spec: MFP Print & Scan Server as a standalone Windows program
# (no Python install needed on the target machine).
#
# Build (from the repo root, with the project's .venv):
#   .venv\Scripts\python -m pip install pyinstaller
#   powershell -File packaging\windows\build-exe.ps1
# Output: dist\mfp-server\mfp-server.exe plus its _internal\ folder.
#
# One folder, not --onefile: starts instantly (onefile unpacks itself to
# %TEMP% on every start) and antivirus tools flag it far less often.
# config.ini, data\, scans\, uploads\ and logs\ live next to the .exe
# (app/config.py's ROOT), the code and static files inside _internal\.

from PyInstaller.utils.hooks import collect_submodules

ROOT = SPECPATH + "\\..\\.."

hiddenimports = (
    # Loaded by name at run time: uvicorn's protocol/loop/lifespan
    # implementations, the log config's formatter classes, multipart parsing.
    collect_submodules("uvicorn")
    + collect_submodules("app")
    + ["multipart", "python_multipart", "win32timezone"]
)

a = Analysis(
    [ROOT + "\\run.py"],
    pathex=[ROOT],
    datas=[
        (ROOT + "\\app\\static", "app\\static"),
        (ROOT + "\\app\\templates", "app\\templates"),
    ],
    hiddenimports=hiddenimports,
    excludes=["tkinter", "unittest", "pydoc_data"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="mfp-server",
    console=True,  # start.bat shows the log in its window; the autostart task runs it hidden with --log-file
    version=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="mfp-server")
