# Registers MFP Print & Scan Server to start with Windows (run via register_service.bat,
# which asks for administrator rights).
#
# It's a Task Scheduler task rather than a classic Windows service: python.exe
# can't act as a service binary, and a service running as SYSTEM wouldn't see
# the user's default printer and per-user printer settings. The task:
#   - starts at system startup, before anyone logs on;
#   - runs as the logged-on user without storing a password (S4U logon), so
#     printers, settings and the default printer are the user's own;
#   - restarts the server if it exits, with no run-time limit;
#   - writes its log to logs\server.log.
# It also opens the web and IPP ports in Windows Firewall (private/domain networks).

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

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
        Say 'Administrator rights are required - run register_service.bat.' Red
        Done 1
    }

    # The task runs as the user sitting at the console, not as the (possibly
    # different) administrator account that approved the UAC prompt.
    $user = (Get-CimInstance Win32_ComputerSystem).UserName
    if (-not $user) { $user = "$env:USERDOMAIN\$env:USERNAME" }
    Say "Account for the server: $user"

    # 1. Virtual environment and dependencies (same as start.bat).
    Say 'Preparing the Python environment ...'
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'start.ps1') -SetupOnly
    if ($LASTEXITCODE -ne 0) {
        Say "Couldn't prepare .venv - see the error above." Red
        Done 1
    }
    $python = Join-Path $root '.venv\Scripts\python.exe'

    # Ports as the server will see them: config.ini, overridden by MFP_* variables.
    $ports = (& $python -c "from app.config import settings; print(settings.port, settings.ipp_port)") -split ' '
    $webPort, $ippPort = [int]$ports[0], [int]$ports[1]

    # 2. A server already running (start.bat window or an old task) would hold the ports.
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Say 'The task already exists - updating it.'
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        # The venv's python.exe is a launcher with the real Python as a child,
        # which may outlive the stopped task - stop it by its command line.
        $runPy = (Join-Path $root 'run.py').ToLower()
        Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
            Where-Object { $_.CommandLine -and $_.CommandLine.ToLower().Contains($runPy) -and $_.CommandLine -match '--log-file' } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -Confirm:$false -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 2
    }
    if (Get-NetTCPConnection -LocalPort $webPort -State Listen -ErrorAction SilentlyContinue) {
        Say "Port $webPort is busy - close the start.bat window (or another server) and run this again." Red
        Done 1
    }

    # 3. The scheduled task.
    $logFile = Join-Path $root 'logs\server.log'
    $action = New-ScheduledTaskAction -Execute $python `
        -Argument "`"$(Join-Path $root 'run.py')`" --log-file `"$logFile`"" -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $taskPrincipal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew
    $settings.Priority = 5  # default for tasks is 7 (below normal)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $taskPrincipal `
        -Settings $settings -Description 'MFP Print & Scan Server: web printing/scanning (port 8000) and IPP printer (port 631).' `
        -Force | Out-Null
    Say "Task '$TaskName' registered (starts with Windows)." Green

    # 4. Firewall: allow other devices on the local network.
    Get-NetFirewallRule -Group $FirewallGroup -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    foreach ($rule in @(@{Name = 'Web'; Port = $webPort}, @{Name = 'IPP'; Port = $ippPort})) {
        if ($rule.Port -eq 0) { continue }
        New-NetFirewallRule -DisplayName "MFP Print & Scan Server ($($rule.Name))" -Group $FirewallGroup -Direction Inbound `
            -Protocol TCP -LocalPort $rule.Port -Action Allow -Profile Private, Domain | Out-Null
    }
    Say "Firewall: TCP $webPort and $ippPort allowed on private networks." Green
    if (Get-NetConnectionProfile -ErrorAction SilentlyContinue | Where-Object NetworkCategory -eq 'Public') {
        Say ("Your network is marked 'Public', so other devices won't reach the server. Switch it to 'Private': " +
             'Settings -> Network & Internet -> (your network) -> Network profile.') Yellow
    }

    # 5. Start it now and check it came up.
    Start-ScheduledTask -TaskName $TaskName
    Say 'Starting the server ...'
    $up = $false
    foreach ($i in 1..20) {
        Start-Sleep -Seconds 1
        if (Get-NetTCPConnection -LocalPort $webPort -State Listen -ErrorAction SilentlyContinue) { $up = $true; break }
    }
    if ($up) {
        Say "Server is running: http://localhost:$webPort" Green
    } else {
        Say "The server didn't start within 20 s - see $logFile" Yellow
    }
    Say "Log: $logFile"
    Say 'To remove: unregister_service.bat'
    Done 0
}
catch {
    Say "Error: $($_.Exception.Message)" Red
    Done 1
}
