-- Run in SSMS in a representative client database; change this date range.
-- Diagnostic aggregates only. This is NOT a worker adapter.
-- No names, NIPs, document numbers, amounts, XML bodies or UPO contents returned.
-- Numeric types/statuses must be compared with the corresponding Enova screen.
SET NOCOUNT ON;
DECLARE @From date = '2026-01-01';
DECLARE @To date = '2027-01-01';

-- Which form definitions and electronic statuses occur together?
-- Count declaration headers separately from electronic records (retries/corrections).
SELECT COALESCE(def.Symbol, N'Bez definicji') AS definition_symbol,
       d.Typ AS declaration_type, d.Wersja AS declaration_version,
       d.PodmiotType AS subject_type, d.ZrodloType AS source_type,
       d.Bufor AS buffer, d.Poprawna AS valid,
       e.TypDeklaracji AS electronic_type, e.StatusEDeklaracji AS electronic_status,
       COUNT_BIG(e.ID) AS electronic_rows,
       COUNT(DISTINCT d.ID) AS declaration_headers,
       SUM(CAST(CASE WHEN NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(max),e.Potwierdzenie))),N'') IS NOT NULL
                     THEN 1 ELSE 0 END AS bigint)) AS with_confirmation,
       SUM(CAST(CASE WHEN d.OkresFrom IS NULL OR d.OkresTo IS NULL THEN 1 ELSE 0 END AS bigint)) AS missing_period
FROM dbo.Deklaracje d
LEFT JOIN dbo.DefDokumentow def ON def.ID = d.Definicja
LEFT JOIN dbo.EDeklaracje e ON e.Deklaracja = d.ID
WHERE d.Data >= @From AND d.Data < @To
GROUP BY def.Symbol, d.Typ, d.Wersja, d.PodmiotType, d.ZrodloType,
         d.Bufor, d.Poprawna, e.TypDeklaracji, e.StatusEDeklaracji
ORDER BY definition_symbol, declaration_type, electronic_status;

-- Confirmation XML structure only; local names/namespaces establish a parser
-- without disclosing element text or attribute values. No business XML selected.
WITH Confirmations AS (
    SELECT e.StatusEDeklaracji,
           TRY_CONVERT(xml, CONVERT(nvarchar(max),e.Potwierdzenie)) AS Confirmation
    FROM dbo.EDeklaracje e
    JOIN dbo.Deklaracje d ON d.ID = e.Deklaracja
    WHERE d.Data >= @From AND d.Data < @To
),
Shapes AS (
    SELECT c.StatusEDeklaracji,
           n.Node.value('local-name(.)', 'nvarchar(128)') AS element_name,
           n.Node.value('namespace-uri(.)', 'nvarchar(512)') AS namespace_uri,
           n.Node.value('local-name(..)', 'nvarchar(128)') AS parent_element
    FROM Confirmations c
    CROSS APPLY c.Confirmation.nodes('//*') n(Node)
)
SELECT DISTINCT StatusEDeklaracji AS electronic_status, parent_element,
       element_name, namespace_uri
FROM Shapes
ORDER BY electronic_status, parent_element, element_name, namespace_uri;

-- Missing/invalid XML must not silently become accepted or not_sent.
SELECT e.StatusEDeklaracji AS electronic_status,
       CASE WHEN NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(max),e.Potwierdzenie))),N'') IS NULL THEN N'empty'
            WHEN TRY_CONVERT(xml,CONVERT(nvarchar(max),e.Potwierdzenie)) IS NULL THEN N'not_xml'
            ELSE N'xml' END AS confirmation_format,
       COUNT_BIG(*) AS electronic_rows
FROM dbo.EDeklaracje e
JOIN dbo.Deklaracje d ON d.ID = e.Deklaracja
WHERE d.Data >= @From AND d.Data < @To
GROUP BY e.StatusEDeklaracji,
       CASE WHEN NULLIF(LTRIM(RTRIM(CONVERT(nvarchar(max),e.Potwierdzenie))),N'') IS NULL THEN N'empty'
            WHEN TRY_CONVERT(xml,CONVERT(nvarchar(max),e.Potwierdzenie)) IS NULL THEN N'not_xml'
            ELSE N'xml' END;
