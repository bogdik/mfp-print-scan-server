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
# It also opens what's needed in Windows Firewall (private/domain networks):
#   - TCP for the web port and, if enabled, the IPP port and the extra eSCL port (80);
#   - UDP 5353 (mDNS/Bonjour), if mdns = yes - needed for AirPrint, AirScan/eSCL
#     and "Add Printer" discovery to find the server at all. The web/IPP ports
#     above are still what the actual print/scan traffic uses afterwards.

# -NoPause: don't wait for Enter at the end (the installer runs this hidden).
param([switch]$NoPause)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Say($text, $color = 'Gray') { Write-Host "[MFP] $text" -ForegroundColor $color }
function Done($code) {
    if (-not $NoPause) { Read-Host 'Press Enter to close this window' | Out-Null }
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

    # 1. What to run: the standalone mfp-server.exe (packaging\windows build),
    # or run.py with the project's .venv, prepared the same way as start.bat.
    $exe = Join-Path $root 'mfp-server.exe'
    if (Test-Path $exe) {
        $server, $serverArgs = $exe, @()
    } else {
        Say 'Preparing the Python environment ...'
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'start.ps1') -SetupOnly
        if ($LASTEXITCODE -ne 0) {
            Say "Couldn't prepare .venv - see the error above." Red
            Done 1
        }
        $server, $serverArgs = (Join-Path $root '.venv\Scripts\python.exe'), @(Join-Path $root 'run.py')
    }

    # Ports/settings as the server will see them: config.ini, overridden by MFP_* variables.
    $vals = (& $server @serverArgs --print-ports) -split ' '
    $webPort, $ippPort, $mdnsEnabled, $esclPort = [int]$vals[0], [int]$vals[1], [int]$vals[2], [int]$vals[3]

    # 2. A server already running (start.bat window or an old task) would hold the ports.
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Say 'The task already exists - updating it.'
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        # The venv's python.exe is a launcher with the real Python as a child,
        # which may outlive the stopped task - stop it (or mfp-server.exe) by
        # its command line.
        $runPy = (Join-Path $root 'run.py').ToLower()
        Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'mfp-server.exe'" |
            Where-Object { $_.CommandLine -and $_.CommandLine -match '--log-file' -and
                           ($_.CommandLine.ToLower().Contains($runPy) -or $_.CommandLine.ToLower().Contains($exe.ToLower())) } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force -Confirm:$false -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 2
    }
    if (Get-NetTCPConnection -LocalPort $webPort -State Listen -ErrorAction SilentlyContinue) {
        Say "Port $webPort is busy - close the start.bat window (or another server) and run this again." Red
        Done 1
    }

    # 3. The scheduled task.
    $logFile = Join-Path $root 'logs\server.log'
    $action = New-ScheduledTaskAction -Execute $server `
        -Argument ((@($serverArgs | ForEach-Object { "`"$_`"" }) + "--log-file `"$logFile`"") -join ' ') -WorkingDirectory $root
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
    $rules = @(
        @{Name = 'Web'; Port = $webPort; Protocol = 'TCP'},
        @{Name = 'IPP'; Port = $ippPort; Protocol = 'TCP'},
        @{Name = 'eSCL'; Port = $esclPort; Protocol = 'TCP'}
    )
    if ($mdnsEnabled) {
        # One rule regardless of how many services announce themselves (IPP,
        # eSCL): they all use the same UDP 5353 multicast port.
        $rules += @{Name = 'mDNS'; Port = 5353; Protocol = 'UDP'}
    }
    $rules = $rules | Where-Object { $_.Port -ne 0 }
    foreach ($rule in $rules) {
        New-NetFirewallRule -DisplayName "MFP Print & Scan Server ($($rule.Name))" -Group $FirewallGroup -Direction Inbound `
            -Protocol $rule.Protocol -LocalPort $rule.Port -Action Allow -Profile Private, Domain | Out-Null
    }
    $opened = ($rules | ForEach-Object { "$($_.Protocol) $($_.Port)" }) -join ', '
    Say "Firewall: $opened allowed on private networks." Green
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
