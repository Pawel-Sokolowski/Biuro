[CmdletBinding()]
param([switch]$CheckParameters, [switch]$CheckWindowsAuthentication)
$ErrorActionPreference = 'Stop'
$connection = $null
$securePassword = $null
$stage = 'Inicjalizacja'
$exitCode = 0
try {
    if ($PSVersionTable.PSVersion -lt [version]'5.1') { throw 'PowerShell 5.1 required' }
    Add-Type -AssemblyName System.Data
    Write-Host 'PLATNIK - eksport struktury SQL (bez rekordow i bez zmian w bazie)'
    Write-Host 'Podaj dane Microsoft SQL Server PLATNIKA, nie PostgreSQL ani login Biuro.'
    if ($CheckParameters) {
        $sqlServer = 'localhost'
        $database = 'platnik_parameter_check'
        $windowsAuth = [bool]$CheckWindowsAuthentication
        $trustCertificate = $true
    } else {
        $sqlServer = Read-Host 'Serwer / instancja (np. SERWER\PLATNIK lub IP,port)'
        $database = Read-Host 'Nazwa bazy Platnika'
        if (-not $sqlServer.Trim() -or -not $database.Trim()) { throw 'Server and database required' }
        $windowsAuth = (Read-Host 'Uzyc konta Windows? T/N [N]') -match '^(t|tak|y|yes)$'
        $trustCertificate = (Read-Host 'Zaufac wlasnemu certyfikatowi SQL? T/N [N]') -match '^(t|tak|y|yes)$'
    }
    $stage = 'Pobieranie danych logowania'
    if (-not $windowsAuth) {
        $sqlLogin = if ($CheckParameters) { 'test_only' } else { Read-Host 'Uzytkownik SQL (np. sa; nie login do programu Platnik)' }
        if ([string]::IsNullOrWhiteSpace($sqlLogin)) { throw 'SQL login required' }
        $securePassword = if ($CheckParameters) { ConvertTo-SecureString 'parameter-test-only' -AsPlainText -Force } else { Read-Host 'Haslo SQL (nie zostanie zapisane)' -AsSecureString }
        $securePassword.MakeReadOnly()
    }
    $stage = 'Budowanie parametrow SQL'
    $builder = New-Object System.Data.SqlClient.SqlConnectionStringBuilder
    # PowerShell treats this builder as a dictionary. Use SQL keyword names,
    # not CLR property names such as DataSource/InitialCatalog.
    $builder['Data Source'] = $sqlServer.Trim()
    $builder['Initial Catalog'] = $database.Trim()
    $builder['Application Name'] = 'Biuro - metadata-only Platnik export'
    $builder['Connect Timeout'] = 15
    $builder['Encrypt'] = $true
    $builder['TrustServerCertificate'] = $trustCertificate
    $builder['ApplicationIntent'] = 'ReadOnly'
    $stage = 'Tworzenie polaczenia SQL'
    if ($windowsAuth) {
        $builder['Integrated Security'] = $true
        $connection = [System.Data.SqlClient.SqlConnection]::new($builder.get_ConnectionString())
    } else {
        $credential = [System.Data.SqlClient.SqlCredential]::new($sqlLogin.Trim(), $securePassword)
        $connection = [System.Data.SqlClient.SqlConnection]::new($builder.get_ConnectionString(), $credential)
    }
    if ($CheckParameters) {
        Write-Output 'SQL parameters and credential object constructed; no connection opened.'
        return
    }
    $stage = 'Laczenie z SQL Server'
    $connection.Open()
    $stage = 'Odczyt metadanych'
    function Read-Metadata([string]$Query) {
        $command = $connection.CreateCommand()
        $command.CommandTimeout = 60
        $command.CommandText = $Query
        $reader = $null
        try {
            $reader = $command.ExecuteReader()
            $rows = New-Object 'System.Collections.Generic.List[object]'
            while ($reader.Read()) {
                if ($rows.Count -ge 100000) { throw 'Metadata limit exceeded' }
                $item = [ordered]@{}
                for ($i = 0; $i -lt $reader.FieldCount; $i++) {
                    $item[$reader.GetName($i)] = if ($reader.IsDBNull($i)) { $null } else { $reader.GetValue($i) }
                }
                $rows.Add([pscustomobject]$item)
            }
            return ,($rows.ToArray())
        } finally {
            if ($reader) { $reader.Dispose() }
            $command.Dispose()
        }
    }
    $version = Read-Metadata "SELECT CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)) AS sql_version"
    $columns = Read-Metadata @'
SELECT s.name AS schema_name,o.name AS object_name,o.type_desc AS object_type,
    c.column_id,c.name AS column_name,t.name AS data_type,c.max_length,
    c.precision,c.scale,c.is_nullable,c.is_identity,c.is_computed
FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id
JOIN sys.columns c ON c.object_id=o.object_id
JOIN sys.types t ON t.user_type_id=c.user_type_id
WHERE o.type IN ('U','V') AND o.is_ms_shipped=0
ORDER BY s.name,o.name,c.column_id
'@
    if ($columns.Count -eq 0) { throw 'No visible metadata; check database and VIEW DEFINITION permissions' }
    $keys = Read-Metadata @'
SELECT s.name AS schema_name,t.name AS object_name,i.name AS index_name,
    i.is_primary_key,i.is_unique,ic.key_ordinal,ic.is_included_column,c.name AS column_name
FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id
JOIN sys.indexes i ON i.object_id=t.object_id
JOIN sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id
JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id
WHERE t.is_ms_shipped=0 AND i.index_id>0
ORDER BY s.name,t.name,i.name,ic.index_column_id
'@
    $relations = Read-Metadata @'
SELECT fk.name AS relation_name,ps.name AS schema_name,p.name AS object_name,
    pc.name AS column_name,rs.name AS referenced_schema,r.name AS referenced_object,
    rc.name AS referenced_column,f.constraint_column_id
FROM sys.foreign_key_columns f JOIN sys.foreign_keys fk ON fk.object_id=f.constraint_object_id
JOIN sys.tables p ON p.object_id=f.parent_object_id
JOIN sys.schemas ps ON ps.schema_id=p.schema_id
JOIN sys.columns pc ON pc.object_id=p.object_id AND pc.column_id=f.parent_column_id
JOIN sys.tables r ON r.object_id=f.referenced_object_id
JOIN sys.schemas rs ON rs.schema_id=r.schema_id
JOIN sys.columns rc ON rc.object_id=r.object_id AND rc.column_id=f.referenced_column_id
ORDER BY ps.name,p.name,fk.name,f.constraint_column_id
'@
    $directory = [Environment]::GetFolderPath('Desktop')
    if (-not $directory -or -not (Test-Path -LiteralPath $directory)) { $directory = (Get-Location).Path }
    $outputPath = Join-Path $directory ('platnik-schema-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.json')
    $payload = [ordered]@{ format_version=1; database=$database.Trim(); sql_version=$version[0].sql_version;
        exported_at=(Get-Date -Format o); columns=@($columns); indexes=@($keys); foreign_keys=@($relations) }
    $json = ConvertTo-Json -InputObject $payload -Depth 8
    $stage = 'Zapisywanie JSON'
    [IO.File]::WriteAllText($outputPath, $json, [Text.UTF8Encoding]::new($false))
    Write-Host "Gotowe: $outputPath" -ForegroundColor Green
    Write-Host 'Przeslij plik JSON. Zawiera tylko nazwy obiektow, kolumn, typy i relacje.'
    Write-Host 'Nazwy baz i obiektow sa metadanymi; sprawdz plik przed udostepnieniem.'
} catch {
    $exitCode = 1
    Write-Host 'Eksport nie powiodl sie. Nie zmieniono bazy.' -ForegroundColor Red
    $reason = $_.Exception.GetBaseException()
    Write-Host ("Etap: " + $stage + "; typ: " + $reason.GetType().FullName + "; linia: " + $_.InvocationInfo.ScriptLineNumber)
    if ($reason -is [System.Data.SqlClient.SqlException]) {
        Write-Host ("Numer bledu SQL: " + $reason.Number)
        Write-Host 'Sprawdz serwer, baze, konto SQL/Windows, siec i certyfikat. Metadane wymagaja widocznosci obiektow (VIEW DEFINITION).'
    } else {
        Write-Host 'Sprawdz PowerShell 5.1, nazwe bazy i widocznosc metadanych. Limit eksportu: 100000 wierszy na sekcje.'
    }
} finally {
    if ($connection) { $connection.Dispose() }
    if ($securePassword) { $securePassword.Dispose() }
    if ($CheckParameters) {
        if ($exitCode -ne 0) { exit $exitCode }
    } else {
        [void](Read-Host 'Nacisnij Enter, aby zakonczyc')
    }
}
