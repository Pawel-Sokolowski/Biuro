-- Run in SSMS against a representative Enova client database.
-- Metadata only: no invoices, payroll, balances, users or passwords are selected.
SET NOCOUNT ON;
SELECT DB_NAME() AS database_name,
       s.name AS schema_name,
       t.name AS object_name,
       t.type_desc AS object_type,
       c.column_id,
       c.name AS column_name,
       ty.name AS data_type,
       c.max_length,
       c.is_nullable
FROM sys.objects t
JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.columns c ON c.object_id = t.object_id
JOIN sys.types ty ON ty.user_type_id = c.user_type_id
WHERE t.type IN ('U', 'V') AND t.is_ms_shipped = 0
ORDER BY s.name, t.name, c.column_id;

SELECT s.name AS schema_name, t.name AS object_name, c.name AS column_name,
       rs.name AS referenced_schema, rt.name AS referenced_object,
       rc.name AS referenced_column
FROM sys.foreign_key_columns f
JOIN sys.tables t ON t.object_id = f.parent_object_id
JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.columns c ON c.object_id = t.object_id AND c.column_id = f.parent_column_id
JOIN sys.tables rt ON rt.object_id = f.referenced_object_id
JOIN sys.schemas rs ON rs.schema_id = rt.schema_id
JOIN sys.columns rc ON rc.object_id = rt.object_id AND rc.column_id = f.referenced_column_id
ORDER BY s.name, t.name, c.column_id;
