@echo off
rem MFP Print and Scan Server - double-click to start. All logic lives in start.ps1
rem (this file stays pure ASCII: cmd.exe mangles UTF-8 batch files).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
