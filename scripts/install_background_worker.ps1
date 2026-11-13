[CmdletBinding()]
param(
    [string]$ProjectDirectory = '',
    [string]$ExecutablePath,
    [string]$TaskName = 'Biuro-Background-Worker',
    [switch]$CheckOnly,
    [switch]$NonInteractive,
    [switch]$UseWorkerConfig,
    [string]$ExpectedUserSid,
    [switch]$NoStart
)
$ErrorActionPreference = 'Stop'
$installerLog = $null
$password = $null
try {
    if ($PSVersionTable.PSVersion -lt [version]'5.1') {
        throw 'Use Windows PowerShell 5.1 or newer. Run the .cmd launcher, not an older PowerShell host.'
    }
    $logRoot = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Biuro\logs'
    New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
    $installerLog = Join-Path $logRoot 'worker-install.log'
    Add-Content -LiteralPath $installerLog -Value "$(Get-Date -Format o) Worker installation started" -Encoding UTF8

    # Resolve defaults only after parameter binding. Some Windows PowerShell
    # entry points do not populate script-scope variables inside param defaults.
    if ([string]::IsNullOrWhiteSpace($ProjectDirectory)) {
        $scriptDirectory = ''
        $scriptFile = $PSCommandPath
        if ([string]::IsNullOrWhiteSpace($scriptFile)) { $scriptFile = $MyInvocation.MyCommand.Path }
        if (-not [string]::IsNullOrWhiteSpace($scriptFile)) {
            $scriptDirectory = [IO.Path]::GetDirectoryName($scriptFile)
        }
        if (-not [string]::IsNullOrWhiteSpace($scriptDirectory)) {
            $ProjectDirectory = [IO.Path]::GetFullPath((Join-Path $scriptDirectory '..'))
        } elseif ($ExecutablePath) {
            $ProjectDirectory = [IO.Path]::GetDirectoryName((Resolve-Path -LiteralPath $ExecutablePath).Path)
        } else {
            $ProjectDirectory = (Get-Location).Path
        }
    }
    $project = (Resolve-Path -LiteralPath $ProjectDirectory).Path
    $program = $null
    $arguments = '--worker'
    $mode = 'installed EXE'
    if ($ExecutablePath) {
        $program = (Resolve-Path -LiteralPath $ExecutablePath).Path
        if ([IO.Path]::GetExtension($program) -ne '.exe') { throw 'ExecutablePath must point to Biuro.exe.' }
    } elseif (Test-Path -LiteralPath (Join-Path $project 'Biuro.exe')) {
        $program = Join-Path $project 'Biuro.exe'
    } elseif ((Test-Path -LiteralPath (Join-Path $project '.venv\Scripts\python.exe')) -and
              (Test-Path -LiteralPath (Join-Path $project 'app\worker.py'))) {
        $program = Join-Path $project '.venv\Scripts\python.exe'
        $arguments = '-m app.worker'
        $mode = 'Python source environment'
    } elseif (Test-Path -LiteralPath (Join-Path $project 'dist\Biuro\Biuro.exe')) {
        $program = Join-Path $project 'dist\Biuro\Biuro.exe'
    } else {
        $installed = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Programs\Biuro\Biuro.exe'
        if (Test-Path -LiteralPath $installed) { $program = $installed }
    }
    if (-not $program -and -not $CheckOnly -and -not $NonInteractive) {
        Add-Type -AssemblyName System.Windows.Forms
        $picker = New-Object System.Windows.Forms.OpenFileDialog
        try {
            $picker.Title = 'Select the installed Biuro.exe'
            $picker.Filter = 'Biuro application (Biuro.exe)|Biuro.exe'
            $picker.CheckFileExists = $true
            if ($picker.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
                $program = $picker.FileName
            }
        } finally { $picker.Dispose() }
    }
    if (-not $program) {
        throw 'Biuro.exe or a source installation with .venv was not found. Install Biuro first, or pass -ExecutablePath "C:\path\Biuro.exe". An installed EXE does not require Python or .venv.'
    }
    $workingDirectory = if ($mode -eq 'installed EXE') { Split-Path $program -Parent } else { $project }
    Write-Output "PowerShell: $($PSVersionTable.PSVersion)"
    Write-Output "Mode: $mode"
    Write-Output "Program: $program"
    Write-Output "Arguments: $arguments"
    Write-Output "Working directory: $workingDirectory"
    Write-Output "Installer log: $installerLog"
    if ($CheckOnly) {
        Write-Output 'Path check complete. No task registered or started; no background jobs executed.'
        return
    }
    if ($NonInteractive) {
        throw 'Task registration requires the local Windows credential prompt. Use -CheckOnly for unattended path validation.'
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if ($ExpectedUserSid -and $identity.User.Value -ne $ExpectedUserSid) {
        throw 'UAC selected a different Windows account. Run worker setup and elevation under the same Windows account so the configuration remains available.'
    }
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run PowerShell or install_background_worker.cmd as Administrator to register a startup task for a Windows account.'
    }
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        throw "Task '$TaskName' already exists. Edit it in Task Scheduler or choose another -TaskName. It was not replaced."
    }
    Write-Output 'The task account needs access to the executable, PostgreSQL and its own Biuro configuration.'
    Write-Output 'Use Biuro - konfiguracja workera to save PostgreSQL and shared key in this Windows account''s Biuro\worker.env.'
    Write-Output 'For sources, configure the project .env. Passwords are requested locally and are not written to the installer log.'
    if ($UseWorkerConfig) {
        $configPath = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Biuro\worker.env'
        if (-not (Test-Path -LiteralPath $configPath)) { throw 'Worker configuration not found for this Windows account. Open Biuro - konfiguracja workera under the same account used for UAC.' }
        $check = Start-Process -FilePath $program -ArgumentList '--worker --check-config' -WorkingDirectory $workingDirectory -Wait -PassThru
        if ($check.ExitCode -ne 0) { throw 'Worker preflight failed. Open worker setup and check database, schema and shared encryption key. No task was registered.' }
    }
    $credential = Get-Credential -UserName $identity.Name -Message 'Windows account for Biuro worker (same shared DB_URL and encryption key as Biuro)'
    if ($null -eq $credential) { throw 'Windows credential prompt was cancelled.' }
    if ($UseWorkerConfig) {
        $account = New-Object Security.Principal.NTAccount($credential.UserName)
        $accountSid = $account.Translate([Security.Principal.SecurityIdentifier]).Value
        if ($accountSid -ne $identity.User.Value) { throw 'Select the same Windows account that configured the worker. Another account has a different profile and configuration.' }
    }
    $action = New-ScheduledTaskAction -Execute $program -Argument $arguments -WorkingDirectory $workingDirectory
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
    $password = $credential.GetNetworkCredential().Password
    try {
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -User $credential.UserName -Password $password | Out-Null
    } catch {
        $registrationError = $_.Exception.Message
        if ($password) { $registrationError = $registrationError.Replace($password, '[redacted]') }
        throw $registrationError
    } finally { $password = $null }
    Add-Content -LiteralPath $installerLog -Value "$(Get-Date -Format o) Registered task: $TaskName; mode: $mode" -Encoding UTF8
    if (-not $NoStart) {
        Start-ScheduledTask -TaskName $TaskName
        Write-Output "Task '$TaskName' registered and launch requested. This does not yet confirm that the worker is healthy."
    } else {
        Write-Output "Task '$TaskName' registered. Start it in Task Scheduler after completing configuration."
    }
    Write-Output 'Confirm the heartbeat in Biuro > Settings > Worker w tle. If absent, inspect Task Scheduler and the task account''s Biuro\logs\biuro.log.'
    if (-not $NonInteractive) { [void](Read-Host 'Press Enter to finish') }
} catch {
    $message = $_.Exception.Message
    if ($password) { $message = $message.Replace($password, '[redacted]') }
    Write-Host "Worker installation failed: $message" -ForegroundColor Red
    if ($installerLog) {
        try {
            Add-Content -LiteralPath $installerLog -Value "$(Get-Date -Format o) ERROR: $message" -Encoding UTF8
            Write-Host "Error saved to: $installerLog"
        } catch { Write-Host 'Could not save the installer log.' }
    }
    if (-not $NonInteractive -and -not $CheckOnly) { [void](Read-Host 'Press Enter after reading or copying the error') }
    exit 1
} finally { $password = $null }
