# Removes what register_service.ps1 set up: stops and deletes the scheduled
# task and removes the firewall rules. Job history, scans and logs are kept.
# Run via unregister_service.bat (asks for administrator rights).

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

function Say($text, $color = 'Gray') { Write-Host "[MFP] $text" -ForegroundColor $color }
function Done($code) {
    Read-Host 'Press Enter to close this window' | Out-Null
    exit $code
}

$TaskName = 'MFP Print & Scan Server'
$FirewallGroup = 'MFP Print & Scan Server'

try {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Say 'Administrator rights are required - run unregister_service.bat.' Red
        Done 1
    }

    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Say "Task '$TaskName' stopped and removed." Green
    } else {
        Say "Task '$TaskName' not found - nothing to remove."
    }

    # The venv's python.exe is only a launcher that spawns the real Python as a
    # child; stopping the task may leave that child holding the ports. Match
    # the service's command line (run.py of this folder + --log-file).
    $runPy = (Join-Path $root 'run.py').ToLower()
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { $_.CommandLine -and $_.CommandLine.ToLower().Contains($runPy) -and $_.CommandLine -match '--log-file' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -Confirm:$false -ErrorAction SilentlyContinue }

    $rules = Get-NetFirewallRule -Group $FirewallGroup -ErrorAction SilentlyContinue
    if ($rules) {
        $rules | Remove-NetFirewallRule
        Say 'Firewall rules removed.' Green
    }

    Say 'Job history (data\), scans (scans\) and logs (logs\) are kept.'
    Done 0
}
catch {
    Say "Error: $($_.Exception.Message)" Red
    Done 1
}
