-- Read-only ODBC query: inclusive start and exclusive end date parameters.
-- Returns all requested months, including zero, plus the company NIP from Enova.
WITH Parameters AS (
    SELECT CAST(? AS date) AS DateFrom, CAST(? AS date) AS DateTo
),
Months AS (
    SELECT DateFrom AS MonthStart FROM Parameters WHERE DateFrom < DateTo
    UNION ALL
    SELECT DATEADD(month, 1, m.MonthStart)
    FROM Months AS m CROSS JOIN Parameters AS p
    WHERE DATEADD(month, 1, m.MonthStart) < p.DateTo
),
RawNips AS (
    SELECT REPLACE(REPLACE(UPPER(LTRIM(RTRIM(DeklaracjeNIP))), N'-', N''), N' ', N'') AS Value
    FROM dbo.OddzialyFirmy
),
NormalizedNips AS (
    SELECT CASE WHEN LEFT(Value, 2) = N'PL' THEN SUBSTRING(Value, 3, 32) ELSE Value END AS nip
    FROM RawNips
),
Company AS (
    -- Zero or several distinct NIPs will fail worker validation instead of guessing.
    SELECT DISTINCT nip FROM NormalizedNips
    WHERE LEN(nip) = 10 AND nip NOT LIKE N'%[^0-9]%'
),
Documents AS (
    SELECT DATEFROMPARTS(YEAR(d.DataEwidencji), MONTH(d.DataEwidencji), 1) AS MonthStart,
           d.Dokument, d.DokumentType,
           CASE WHEN def.Symbol = N'WBEW' THEN 1 ELSE 0 END AS IsBankStatement
    FROM dbo.DokEwidencja AS d
    LEFT JOIN dbo.DefDokumentow AS def ON def.ID = d.Definicja
    CROSS JOIN Parameters AS p
    WHERE d.DataEwidencji >= p.DateFrom AND d.DataEwidencji < p.DateTo
),
OtherDocuments AS (
    -- Include every other definition, including DR, LPEW and RKVE.
    SELECT MonthStart, COUNT_BIG(*) AS documents
    FROM Documents WHERE IsBankStatement = 0 GROUP BY MonthStart
),
Statements AS (
    -- Do not multiply positions when several headers refer to the same statement.
    SELECT DISTINCT MonthStart, Dokument, DokumentType
    FROM Documents WHERE IsBankStatement = 1
),
BadStatements AS (
    SELECT DISTINCT s.MonthStart
    FROM Statements AS s
    WHERE s.DokumentType IS NULL OR s.DokumentType <> N'RaportyESP'
       OR NOT EXISTS (SELECT 1 FROM dbo.RaportyESP AS r WHERE r.ID = s.Dokument)
),
BankPositions AS (
    -- Includes manually entered payments. Imported bank rows are NOT added again.
    SELECT s.MonthStart, COUNT_BIG(z.ID) AS positions
    FROM Statements AS s
    JOIN dbo.Zaplaty AS z ON z.Raport = s.Dokument AND z.RaportType = N'RaportyESP'
    GROUP BY s.MonthStart
)
SELECT c.nip, CONVERT(char(7), m.MonthStart, 120) AS miesiac,
       COALESCE((SELECT '{' + STRING_AGG(CAST('"' + STRING_ESCAPE(t.symbol, 'json') + '":' + CAST(t.cnt AS varchar(20)) AS nvarchar(max)), ',') + '}'
                 FROM (SELECT COALESCE(def.Symbol,N'Bez definicji') AS symbol, COUNT_BIG(*) AS cnt
                       FROM dbo.DokEwidencja x LEFT JOIN dbo.DefDokumentow def ON def.ID=x.Definicja
                       WHERE x.DataEwidencji >= m.MonthStart AND x.DataEwidencji < DATEADD(month,1,m.MonthStart) AND (def.Symbol IS NULL OR def.Symbol <> N'WBEW')
                       GROUP BY COALESCE(def.Symbol,N'Bez definicji')) t), N'{}') AS rodzaje,
       COALESCE(d.documents, 0) AS dokumenty,
       -- A broken statement link must not silently become a zero bank contribution.
       CASE WHEN bad.MonthStart IS NOT NULL THEN NULL ELSE COALESCE(b.positions, 0) END AS pozycje_bankowe
FROM Months AS m CROSS JOIN Company AS c
LEFT JOIN OtherDocuments AS d ON d.MonthStart = m.MonthStart
LEFT JOIN BankPositions AS b ON b.MonthStart = m.MonthStart
LEFT JOIN BadStatements AS bad ON bad.MonthStart = m.MonthStart
ORDER BY m.MonthStart
OPTION (MAXRECURSION 24);
