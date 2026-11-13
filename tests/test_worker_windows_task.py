"""Exercise the actual PowerShell upgrade logic with stubbed Scheduler cmdlets."""
import json
import os
import subprocess
import unittest

from app.services.worker_windows import TASK_COMMAND


@unittest.skipUnless(os.name=='nt','Windows PowerShell task upgrade regression')
class WindowsTaskUpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sid = subprocess.check_output(['powershell.exe','-NoProfile','-NonInteractive','-Command',
            '([Security.Principal.WindowsIdentity]::GetCurrent()).User.Value'],text=True).strip()

    def execute(self,mode,*,running=True,foreign=False):
        stubs = r'''
$script:events = New-Object 'System.Collections.Generic.List[string]'
$script:state = 'STATE_VALUE'
$script:foreign = FOREIGN_VALUE
function Get-ScheduledTask {
    param($TaskName,$ErrorAction)
    $taskExe = if ($script:foreign) { 'C:\AnotherInstallation\Biuro.exe' } else { $p.exe }
    return [pscustomobject]@{
        Principal = [pscustomobject]@{ UserId = $p.sid }
        Actions = @([pscustomobject]@{ Execute = $taskExe; Arguments = '--worker' })
        State = $script:state
    }
}
function Stop-ScheduledTask {
    param($TaskName)
    if ($script:foreign) { [Console]::Out.Write('unsafe_stop') }
    $script:events.Add('stop')
    $script:state = 'Ready'
}
function Start-ScheduledTask {
    param($TaskName)
    if ($script:foreign) { [Console]::Out.Write('unsafe_start') }
    $script:events.Add('start')
}
'''.replace('STATE_VALUE','Running' if running else 'Ready').replace('FOREIGN_VALUE','$true' if foreign else '$false')
        command = TASK_COMMAND.replace("[Console]::Out.Write('ok')",
            '[Console]::Out.Write((ConvertTo-Json -InputObject ($script:events.ToArray()) -Compress))')
        return subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',stubs+command],
            input=json.dumps({'mode':mode,'sid':self.sid,'exe':r'C:\Biuro\Biuro.exe','password':''}),
            text=True,capture_output=True,timeout=30)

    def test_reinstall_stops_then_starts_the_existing_running_worker(self):
        result = self.execute('install')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),['stop','start'])

    def test_existing_stopped_worker_is_started_without_recreating_the_task(self):
        result = self.execute('install',running=False)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),['start'])

    def test_explicit_restart_waits_for_stop_before_start(self):
        result = self.execute('restart')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),['stop','start'])

    def test_another_installation_is_neither_stopped_nor_started(self):
        result = self.execute('install',foreign=True)
        self.assertEqual(result.returncode,1)
        self.assertEqual(result.stdout,'failed')
