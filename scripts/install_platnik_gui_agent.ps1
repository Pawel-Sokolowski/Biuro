# Run as the Windows user whose interactive desktop hosts Płatnik.
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$Config,
    [string]$PythonExecutable = '',
    [string]$AgentHome = (Join-Path $env:LOCALAPPDATA 'Biuro\PlatnikAgent'),
    [switch]$CheckOnly,
    [switch]$Start
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$configPath = (Resolve-Path -LiteralPath $Config).Path
$taskName = 'Biuro-Platnik-GUI-Agent'
$venv = Join-Path $AgentHome 'venv'
$python = Join-Path $venv 'Scripts\python.exe'
$pythonw = Join-Path $venv 'Scripts\pythonw.exe'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$sid = $identity.User.Value

Push-Location -LiteralPath $root
try {
    if (-not $CheckOnly) {
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
            if ($PythonExecutable) {
                & $PythonExecutable -m venv $venv
            } else {
                & py.exe -3.14 -m venv $venv
            }
            if ($LASTEXITCODE -ne 0) { throw 'Could not create the agent virtual environment.' }
        }
        & $python -m pip install -r (Join-Path $root 'requirements-platnik-agent.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Could not install the agent dependencies.' }
        & $python -m pip check
        if ($LASTEXITCODE -ne 0) { throw 'Inconsistent agent dependencies.' }
    }
    if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or
        -not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
        throw 'The agent virtual environment is missing.'
    }
    & $python -m app.integrations.platnik.gui_agent probe --config $configPath
    if ($LASTEXITCODE -ne 0) { throw 'The Płatnik agent preflight failed.' }
    if ($CheckOnly) { return }

    $arguments = '-m app.integrations.platnik.gui_agent run --config "' + $configPath + '"'
    $current = Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue
    if ($current) {
        $registeredUser = $current.Principal.UserId
        if ($registeredUser -notmatch '^S-1-') {
            $account = [Security.Principal.NTAccount]::new($registeredUser)
            $registeredUser = $account.Translate([Security.Principal.SecurityIdentifier]).Value
        }
        if ($registeredUser -ne $sid -or $current.Principal.LogonType -ne 'Interactive' -or
            $current.Actions.Count -ne 1 -or $current.Actions[0].Execute -ne $pythonw -or
            $current.Actions[0].Arguments -ne $arguments -or $current.Actions[0].WorkingDirectory -ne $root) {
            throw 'An existing task with that name has different settings.'
        }
    } else {
        $action = New-ScheduledTaskAction -Execute $pythonw -Argument $arguments -WorkingDirectory $root
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity.Name
        $principal = New-ScheduledTaskPrincipal -UserId $sid -LogonType Interactive -RunLevel Limited
        $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
            -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
        Register-ScheduledTask -TaskName $taskName -TaskPath '\' -Action $action -Principal $principal `
            -Trigger $trigger -Settings $settings | Out-Null
    }
    if ($Start) { Start-ScheduledTask -TaskName $taskName -TaskPath '\' }
    Write-Output ('Interactive task registered: ' + $taskName)
} finally {
    Pop-Location
}
