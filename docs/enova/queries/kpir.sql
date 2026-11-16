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
    SELECT REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(UPPER(LTRIM(RTRIM(DeklaracjeNIP))),
           N'-', N''), N' ', N''), NCHAR(9), N''), NCHAR(10), N''), NCHAR(13), N''), NCHAR(160), N'') AS Value
    FROM dbo.OddzialyFirmy
),
NormalizedNips AS (
    SELECT CASE WHEN LEFT(Value, 2) = N'PL' THEN SUBSTRING(Value, 3, 32) ELSE Value END AS nip
    FROM RawNips
),
ValidNips AS (
    SELECT DISTINCT nip FROM NormalizedNips
    WHERE LEN(nip) = 10 AND nip NOT LIKE N'%[^0-9]%'
),
Company AS (
    SELECT nip FROM ValidNips
    UNION ALL
    -- Worker supplies the saved company mapping only when Enova has no valid NIP.
    -- Never reinterpret PESEL or replace a conflicting valid NIP.
    SELECT mapped.nip
    FROM (SELECT CONVERT(nvarchar(32), SESSION_CONTEXT(N'biuro_company_nip')) AS nip) mapped
    WHERE LEN(mapped.nip) = 10 AND mapped.nip NOT LIKE N'%[^0-9]%'
      AND NOT EXISTS (SELECT 1 FROM ValidNips)
),
Entries AS (
    SELECT DATEFROMPARTS(YEAR(d.Data), MONTH(d.Data), 1) AS MonthStart,
           COUNT_BIG(*) AS documents
    FROM dbo.Dziennik AS d CROSS JOIN Parameters AS p
    WHERE d.Data >= p.DateFrom AND d.Data < p.DateTo
      AND d.KPiRLp > 0
    GROUP BY DATEFROMPARTS(YEAR(d.Data), MONTH(d.Data), 1)
)
SELECT c.nip, CONVERT(char(7), m.MonthStart, 120) AS miesiac,
       COALESCE((SELECT '{' + STRING_AGG(CAST('"' + STRING_ESCAPE(t.symbol, 'json') + '":' + CAST(t.cnt AS varchar(20)) AS nvarchar(max)), ',') + '}'
                 FROM (SELECT COALESCE(def.Symbol,N'Bez definicji') AS symbol, COUNT_BIG(*) AS cnt
                       FROM dbo.Dziennik x LEFT JOIN dbo.DokEwidencja e ON e.ID=x.Ewidencja LEFT JOIN dbo.DefDokumentow def ON def.ID=e.Definicja
                       WHERE x.Data >= m.MonthStart AND x.Data < DATEADD(month,1,m.MonthStart) AND x.KPiRLp > 0
                       GROUP BY COALESCE(def.Symbol,N'Bez definicji')) t), N'{}') AS rodzaje,
       COALESCE(e.documents, 0) AS dokumenty,
       CAST(0 AS bigint) AS pozycje_bankowe
FROM Months AS m CROSS JOIN Company AS c
LEFT JOIN Entries AS e ON e.MonthStart = m.MonthStart
ORDER BY m.MonthStart
OPTION (MAXRECURSION 24);
