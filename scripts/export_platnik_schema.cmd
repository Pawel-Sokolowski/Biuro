@echo off
rem ExecutionPolicy applies to this process only; the machine policy is not changed.
rem Keep parser errors visible too.
powershell.exe -NoProfile -ExecutionPolicy Bypass -NoExit -File "%~dp0export_platnik_schema.ps1"
