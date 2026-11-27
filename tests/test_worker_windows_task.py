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

    def execute(self,mode,*,running=True,foreign=False,arguments='--worker',agent=False):
        stubs = r'''
$script:events = New-Object 'System.Collections.Generic.List[string]'
$script:state = 'STATE_VALUE'
$script:foreign = FOREIGN_VALUE
function Get-ScheduledTask {
    param($TaskName,$ErrorAction)
    $taskExe = if ($script:foreign) { 'C:\AnotherInstallation\Biuro.exe' } else { $p.exe }
    if ($TaskName -eq 'Biuro-Platnik-Worker') {
        if (-not AGENT_PRESENT) { return $null }
        return [pscustomobject]@{
            Principal = [pscustomobject]@{ UserId = $p.sid }
            Actions = @([pscustomobject]@{ Execute = $p.exe; Arguments = '--platnik-agent' })
            State = $script:state
        }
    }
    return [pscustomobject]@{
        Principal = [pscustomobject]@{ UserId = $p.sid }
        Actions = @([pscustomobject]@{ Execute = $taskExe; Arguments = 'ARGUMENTS_VALUE' })
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
# Registration cmdlets record the tasks the installer creates.
function New-ScheduledTaskAction { param($Execute,$Argument,$WorkingDirectory) return [pscustomobject]@{ Execute=$Execute; Arguments=$Argument } }
function New-ScheduledTaskTrigger { param($AtStartup,[switch]$AtLogOn,$User) return [pscustomobject]@{ User=$User } }
function New-ScheduledTaskSettingsSet { param([switch]$StartWhenAvailable,[string]$MultipleInstances,$RestartCount,$RestartInterval,$ExecutionTimeLimit,[switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries) return [pscustomobject]@{} }
function New-ScheduledTaskPrincipal { param($UserId,$LogonType,$RunLevel) return [pscustomobject]@{ UserId=$UserId; LogonType=$LogonType } }
function Register-ScheduledTask { param($TaskName,$Action,$Trigger,$Settings,$Principal,$User,$Password) $script:events.Add('register:' + $TaskName) }
function Set-ScheduledTask { param($TaskName,$User,$Password) $script:events.Add('update:' + $TaskName) }
# Start-BiuroTask launches the start in a child process; record it as a child that
# exits immediately would do, so the order of stop/start is still observable.
function Start-Process {
    param($FilePath,$WindowStyle,[switch]$PassThru,$ArgumentList)
    Start-ScheduledTask -TaskName 'Biuro-Background-Worker'
    $runner = New-Object psobject
    $runner | Add-Member -MemberType NoteProperty -Name ExitCode -Value 0
    $runner | Add-Member -MemberType ScriptMethod -Name WaitForExit -Value { param($milliseconds) return $true }
    return $runner
}
'''.replace('STATE_VALUE','Running' if running else 'Ready').replace('FOREIGN_VALUE','$true' if foreign else '$false').replace('ARGUMENTS_VALUE',arguments).replace('AGENT_PRESENT','$true' if agent else '$false')
        command = TASK_COMMAND.replace("[Console]::Out.Write('ok')",
            '[Console]::Out.Write((ConvertTo-Json -InputObject ($script:events.ToArray()) -Compress))')
        return subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',stubs+command],
            input=json.dumps({'mode':mode,'sid':self.sid,'exe':r'C:\Biuro\Biuro.exe','password':''}),
            text=True,capture_output=True,timeout=30)

    def test_reinstall_stops_then_starts_the_existing_running_worker(self):
        result = self.execute('install')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),[
            'stop','update:Biuro-Background-Worker','start',
        ])

    def test_existing_stopped_worker_is_started_without_recreating_the_task(self):
        result = self.execute('install',running=False)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),[
            'update:Biuro-Background-Worker','start',
        ])

    def test_explicit_restart_waits_for_stop_before_start(self):
        result = self.execute('restart')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),[
            'stop','update:Biuro-Background-Worker','start',
        ])

    def test_source_environment_task_is_managed_not_treated_as_foreign(self):
        result = self.execute('install',running=False,arguments='-m app.worker')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),[
            'update:Biuro-Background-Worker','start',
        ])

    def test_stopping_an_already_stopped_worker_does_not_issue_a_stop(self):
        result = self.execute('stop',running=False)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),[])

    def test_another_installation_is_neither_stopped_nor_started(self):
        result = self.execute('install',foreign=True)
        self.assertEqual(result.returncode,1)
        self.assertEqual(result.stdout,'failed')

    def test_restart_manages_only_the_main_worker_when_a_legacy_agent_exists(self):
        result = self.execute('restart', agent=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ['stop', 'update:Biuro-Background-Worker', 'start'])
