[CmdletBinding()]
param(
    [string]$ProjectDirectory = (Split-Path $PSScriptRoot -Parent),
    [ValidatePattern('^([01][0-9]|2[0-3]):[0-5][0-9]$')][string]$Time = '02:00',
    [string]$TaskName = 'Biuro-Enova-Daily'
)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath $ProjectDirectory).Path
$runner = Join-Path $project 'scripts\run_enova_import.ps1'
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    throw 'Task already exists. Edit it in Windows Task Scheduler or choose another TaskName.'
}
# Prompt locally: credentials never enter chat, source control, or command arguments.
$credential = Get-Credential -Message 'Windows account for daily ENOVA import (access to Biuro DB, encryption key and SQL Server)'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -File `"$runner`" -SavedSettings -Apply" -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
$password = $credential.GetNetworkCredential().Password
try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -User $credential.UserName -Password $password | Out-Null
} finally {
    $password = $null
}
Write-Output "Installed $TaskName daily at $Time (server local time)."
