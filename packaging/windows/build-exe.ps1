# Builds the standalone Windows program: dist\mfp-server\ with mfp-server.exe,
# its _internal\ folder, and next to them what the exe-based install needs
# (config example, start/autostart scripts, docs). No Python needed on the
# machine it's copied to. Then packaging\windows\mfp-print-scan-server.iss
# wraps that folder into an installer.
#
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build-exe.ps1
#
# Needs the project's .venv with requirements.txt and pyinstaller installed:
#   .venv\Scripts\python -m pip install -r requirements.txt pyinstaller

$ErrorActionPreference = 'Stop'
$root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
Set-Location $root
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw "No .venv - run start.bat once (or create .venv) first." }

& $python -m PyInstaller --noconfirm --clean --distpath dist --workpath build\pyinstaller packaging\windows\mfp-server.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit code $LASTEXITCODE)." }

$out = Join-Path $root 'dist\mfp-server'
foreach ($f in 'config.example.ini', 'README.md', 'README.ru.md', 'LICENSE', 'VERSION',
               'start.bat', 'start.ps1', 'register_service.bat', 'unregister_service.bat') {
    Copy-Item $f $out -Force
}
New-Item -ItemType Directory -Force (Join-Path $out 'scripts') | Out-Null
Copy-Item scripts\*.ps1 (Join-Path $out 'scripts') -Force

$size = (Get-ChildItem $out -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("Built {0} ({1:N0} MB)" -f (Join-Path $out 'mfp-server.exe'), $size) -ForegroundColor Green
