[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ConfigFile,
    [string]$ProjectDirectory = (Split-Path $PSScriptRoot -Parent),
    [string]$LogDirectory,
    [switch]$Apply
)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath $ProjectDirectory).Path
$config = (Resolve-Path -LiteralPath $ConfigFile).Path
$python = Join-Path $project '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Project Python environment not found.' }
if (-not $LogDirectory) { $LogDirectory = Join-Path $project 'logs\enova' }
New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
$log = Join-Path $LogDirectory ((Get-Date -Format 'yyyy-MM-dd_HH-mm-ss-fff') + '.log')
$arguments = @('-m', 'app.integrations.enova.document_counts', '--config', $config)
if ($Apply) { $arguments += '--apply' }
Push-Location -LiteralPath $project
try {
    & $python @arguments 2>&1 | Tee-Object -FilePath $log
    $result = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $result
