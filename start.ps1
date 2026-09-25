# MFP Print & Scan Server - Windows launcher (usually started via start.bat).
# First run creates .venv and installs dependencies; later they're
# reinstalled only when requirements.txt changes.
# -SetupOnly: just prepare .venv and exit (used by scripts\register_service.ps1).
param([switch]$SetupOnly)
Set-Location $PSScriptRoot
$Host.UI.RawUI.WindowTitle = 'MFP Print & Scan Server'

function Fail($message) {
    Write-Host "[MFP] $message" -ForegroundColor Red
    if (-not $SetupOnly) {  # the caller shows its own prompt
        Read-Host 'Press Enter to close this window' | Out-Null
    }
    exit 1
}

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $python)) {
    Write-Host '[MFP] Creating virtual environment .venv ...'
    # py.exe is the python.org launcher; "python" may be the Microsoft Store stub.
    if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m venv .venv }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { & python -m venv .venv }
    if (-not (Test-Path $python)) {
        Fail ('Python 3 not found. Install it from https://www.python.org/downloads/ ' +
              '(tick "Add python.exe to PATH") and run this again.')
    }
}

$marker = Join-Path $PSScriptRoot '.venv\requirements.installed'
$current = (Get-FileHash requirements.txt).Hash
$installed = if (Test-Path $marker) { Get-Content $marker } else { '' }
if ($current -ne $installed) {
    Write-Host '[MFP] Installing dependencies ...'
    & $python -m pip install --disable-pip-version-check -q -r requirements.txt
    if ($LASTEXITCODE -ne 0) { Fail "Couldn't install dependencies - see the error above." }
    Set-Content -Path $marker -Value $current
}
if ($SetupOnly) { exit 0 }

# Ports as the server will see them: config.ini, overridden by MFP_* variables.
$port, $ippPort = (& $python -c "from app.config import settings; print(settings.port, settings.ipp_port)") -split ' '
Write-Host ''
Write-Host "[MFP] Web UI:         http://localhost:$port" -ForegroundColor Green
Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
    ForEach-Object {
        $line = "[MFP] On the network: http://$($_.IPAddress):$port"
        if ($ippPort -ne '0') { $line += "   IPP printer: ipp://$($_.IPAddress):$ippPort/ipp/print" }
        Write-Host $line
    }
Write-Host '[MFP] Stop the server: Ctrl+C or close this window.'
Write-Host ''

& $python run.py
if ($LASTEXITCODE -ne 0) {
    Fail ("The server exited with an error (code $LASTEXITCODE). Most often port $port or $ippPort " +
          'is already in use: is the server already running in another window?')
}
