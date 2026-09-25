@echo off
rem MFP Print and Scan Server - remove the autostart task and firewall rules.
rem Double-click; asks for administrator rights. Logic: scripts\unregister_service.ps1
rem (this file stays pure ASCII: cmd.exe mangles UTF-8 batch files).
net session >nul 2>&1
if errorlevel 1 (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\unregister_service.ps1"
