[CmdletBinding()]
param(
    [string]$Destination = (Join-Path (Split-Path $PSScriptRoot -Parent) 'installer\prerequisites\msodbcsql18-x64.msi')
)
$ErrorActionPreference = 'Stop'
# Microsoft Learn's x64 link for ODBC Driver 18.7.1.1; pinned, not a moving alias.
# https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server
$downloadUrl = 'https://download.microsoft.com/download/d624e1c6-293b-4d6f-91b8-6e515a5d6a77/amd64/1033/msodbcsql.msi'
$expectedVersion = '18.7.1.1'

function Assert-OdbcPackage([string]$PackagePath) {
    $signature = Get-AuthenticodeSignature -FilePath $PackagePath
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(^|,\s*)O=Microsoft Corporation(,|$)') {
        throw 'ODBC prerequisite does not have a valid Microsoft Corporation signature.'
    }
    $installer = $database = $view = $record = $summary = $null
    try {
        $installer = New-Object -ComObject WindowsInstaller.Installer
        $database = $installer.OpenDatabase($PackagePath, 0)
        $view = $database.OpenView('SELECT `Value` FROM `Property` WHERE `Property` = ''ProductVersion''')
        $view.Execute()
        $record = $view.Fetch()
        if (-not $record -or $record.StringData(1) -ne $expectedVersion) {
            throw "Expected Microsoft ODBC version $expectedVersion. Review the pinned download before updating it."
        }
        $summary = $database.SummaryInformation(0)
        $template = [string]$summary.Property(7)
        if ($template.Split(';')[0] -ne 'x64') { throw 'Expected the x64 ODBC MSI.' }
    } finally {
        foreach ($comObject in @($record, $view, $summary, $database, $installer)) {
            if ($null -ne $comObject) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($comObject) }
        }
    }
}

$destinationPath = [IO.Path]::GetFullPath($Destination)
$directory = Split-Path $destinationPath -Parent
New-Item -ItemType Directory -Path $directory -Force | Out-Null
if (Test-Path -LiteralPath $destinationPath) {
    Assert-OdbcPackage $destinationPath
} else {
    $temporary = Join-Path $directory ([Guid]::NewGuid().ToString() + '.msi')
    try {
        Invoke-WebRequest -Uri $downloadUrl -OutFile $temporary
        Assert-OdbcPackage $temporary
        Move-Item -LiteralPath $temporary -Destination $destinationPath
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
    }
}
Write-Output "Prepared Microsoft ODBC Driver $expectedVersion (x64): $destinationPath"
Get-FileHash -Algorithm SHA256 -LiteralPath $destinationPath
