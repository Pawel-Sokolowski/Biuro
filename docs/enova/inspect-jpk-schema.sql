-- Run in SSMS in an ENOVA company database. Metadata only; no business data.
-- Use the result to identify the installed version's JPK sending/UPO schema.
SELECT s.name AS schema_name, t.name AS object_name, c.name AS column_name,
       ty.name AS data_type
FROM sys.objects t
JOIN sys.schemas s ON s.schema_id=t.schema_id
JOIN sys.columns c ON c.object_id=t.object_id
JOIN sys.types ty ON ty.user_type_id=c.user_type_id
WHERE t.type IN ('U','V') AND t.is_ms_shipped=0
  AND (t.name LIKE '%JPK%' OR t.name LIKE '%Deklarac%' OR t.name LIKE '%Wysyl%'
       OR EXISTS (SELECT 1 FROM sys.columns x WHERE x.object_id=t.object_id
                  AND (x.name LIKE '%UPO%' OR x.name LIKE '%JPK%')))
ORDER BY s.name,t.name,c.column_id;
