[CmdletBinding()]
param(
    [string]$ProjectDirectory = (Split-Path $PSScriptRoot -Parent),
    [string]$TaskName = 'Biuro-Background-Worker'
)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath $ProjectDirectory).Path
$python = Join-Path $project '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Install the project Python environment first.' }
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    throw 'Task already exists. Edit the existing task rather than install a duplicate.'
}
$credential = Get-Credential -Message 'Windows account for Biuro worker (DB_URL and shared BIO_ENCRYPTION_KEY required)'
$action = New-ScheduledTaskAction -Execute $python -Argument '-m app.worker' -WorkingDirectory $project
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
$password = $credential.GetNetworkCredential().Password
try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -User $credential.UserName -Password $password | Out-Null
} finally { $password = $null }
Start-ScheduledTask -TaskName $TaskName
Write-Output "Installed and started $TaskName. It starts at boot and runs without an interactive logon."
