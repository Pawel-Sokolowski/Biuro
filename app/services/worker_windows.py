"""Windows Task Scheduler bridge. Secrets travel only over a redirected stdin pipe."""
import csv
import json
import os
from pathlib import Path
import subprocess
import sys


def identity():
    output = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
    return next(csv.reader([output.strip()]))[:2]


TASK_COMMAND = r'''
$ErrorActionPreference = 'Stop'
$p = [Console]::In.ReadToEnd() | ConvertFrom-Json
$deadline = [DateTime]::UtcNow.AddSeconds(180)
function Stop-BiuroTask {
    Stop-ScheduledTask -TaskName 'Biuro-Background-Worker'
    for ($attempt = 0; $attempt -lt 50; $attempt++) {
        if ((Get-ScheduledTask -TaskName 'Biuro-Background-Worker').State -ne 'Running') { return }
        Start-Sleep -Milliseconds 200
    }
    throw 'still_running'
}
function Start-BiuroTask {
    # A process that outlives the caller's own wait keeps this script from hanging.
    $runner = Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -PassThru -ArgumentList @(
        '-NoProfile', '-NonInteractive', '-Command', 'Start-ScheduledTask -TaskName Biuro-Background-Worker')
    $left = ($deadline - [DateTime]::UtcNow).TotalSeconds
    if ($left -lt 1) { $left = 1 }
    if (-not $runner.WaitForExit([int][Math]::Ceiling($left) * 1000)) { throw 'start_timeout' }
    if ($runner.ExitCode -ne 0) { throw 'start_failed' }
}
try {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    if ($id.User.Value -ne $p.sid) { throw 'identity' }
    $name = 'Biuro-Background-Worker'
    # Both shapes this app registers: an installed EXE (--worker) and a source
    # environment (-m app.worker). Anything else belongs to another installation.
    $accepted = @('--worker', '-m app.worker')
    $old = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($old) {
        $sid = $old.Principal.UserId
        if ($sid -notmatch '^S-1-') { $sid = (New-Object Security.Principal.NTAccount($sid)).Translate([Security.Principal.SecurityIdentifier]).Value }
        if ($old.Actions.Count -ne 1 -or $old.Actions[0].Execute -ne $p.exe -or
            $accepted -notcontains $old.Actions[0].Arguments -or $sid -ne $id.User.Value) { throw 'foreign_task' }
    }
    if ($p.mode -eq 'stop') {
        if (-not $old) { throw 'missing_task' }
        if ($old.State -eq 'Running') { Stop-BiuroTask }
    } elseif ($p.mode -eq 'restart') {
        if (-not $old) { throw 'missing_task' }
        if ($old.State -eq 'Running') { Stop-BiuroTask }
        Set-ScheduledTask -TaskName $name -User $id.Name -Password $p.password | Out-Null
        Start-BiuroTask
    } elseif ($p.mode -eq 'install') {
        if ($old) {
            if ($old.State -eq 'Running') { Stop-BiuroTask }
            Set-ScheduledTask -TaskName $name -User $id.Name -Password $p.password | Out-Null
            Start-BiuroTask
        } else {
            $action = New-ScheduledTaskAction -Execute $p.exe -Argument '--worker' -WorkingDirectory ([IO.Path]::GetDirectoryName($p.exe))
            $trigger = New-ScheduledTaskTrigger -AtStartup
            $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
            Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -User $id.Name -Password $p.password | Out-Null
            Start-BiuroTask
        }
    } else { throw 'mode' }
    [Console]::Out.Write('ok')
} catch {
    # Never send Windows/credential exception text back into UI or logs.
    [Console]::Out.Write('failed')
    exit 1
} finally { $p = $null }
'''


def manage_task(mode, password='', *, expected_sid):
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        raise ValueError('Autostart wymaga zainstalowanego Biuro EXE na Windows.')
    if identity()[1] != expected_sid:
        raise ValueError('UAC użył innego konta Windows. Otwórz konfigurację i UAC na tym samym koncie.')
    if mode not in ('install', 'restart', 'stop'):
        raise ValueError('Nieprawidłowa operacja workera.')
    if mode != 'stop':
        from app.services.worker_setup import worker_env_path, preflight
        if not worker_env_path().is_file():
            raise ValueError('Najpierw zapisz konfigurację workera na tym koncie Windows.')
        preflight()
    payload = json.dumps({'mode': mode, 'sid': expected_sid, 'exe': str(Path(sys.executable).resolve()),
                          'password': password})
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', TASK_COMMAND],
                                input=payload, text=True, capture_output=True, timeout=240,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    finally:
        payload = None
    if result.returncode or result.stdout.strip() != 'ok':
        raise ValueError('Nie udało się wykonać operacji. Sprawdź hasło konta Windows, uprawnienia '
                         'do logowania wsadowego i zadanie Biuro-Background-Worker w Harmonogramie Windows. '
                         'Zadanie przypisane do innej instalacji lub konta nie jest nadpisywane.')
