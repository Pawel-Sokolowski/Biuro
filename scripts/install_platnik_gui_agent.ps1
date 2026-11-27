# Run as the HR Windows user who operates Płatnik.
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
$venv = Join-Path $AgentHome 'venv'
$python = Join-Path $venv 'Scripts\python.exe'

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
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw 'The agent virtual environment is missing.'
    }
    & $python -m app.integrations.platnik.gui_agent probe --config $configPath
    if ($LASTEXITCODE -ne 0) { throw 'The Płatnik agent preflight failed.' }
    if ($CheckOnly) { return }

    if ($Start) {
        & $python -m app.integrations.platnik.gui_agent daily --config $configPath
        if ($LASTEXITCODE -ne 0) { throw 'The manual Płatnik agent run did not finish.' }
    } else {
        Write-Output 'Agent ready. Start the end-of-day run from Biuro on the HR workstation.'
    }
} finally {
    Pop-Location
}
