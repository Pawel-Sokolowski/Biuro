@echo off
rem Run this launcher as Administrator. -NoExit also keeps execution-policy and parser errors visible.
powershell.exe -NoProfile -NoExit -File "%~dp0install_background_worker.ps1" %*
