@echo off
rem Single supported entry point. Right-click and Run as administrator.
rem Pass an absolute directory explicitly; never rely on param-default PSScriptRoot.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NoExit -File "%~dp0install_background_worker.ps1" -ProjectDirectory "%~dp0.." %*
