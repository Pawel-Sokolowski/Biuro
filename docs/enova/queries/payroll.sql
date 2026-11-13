-- Read-only payroll DOCUMENT counts by document Data, not payment/ZUS month.
-- List headers and individual payslips are separate kinds; do not add them as people.
-- Buffer payslips remain visible separately. Included automatically with worker.
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
Company AS (
    SELECT DISTINCT nip FROM NormalizedNips
    WHERE LEN(nip) = 10 AND nip NOT LIKE N'%[^0-9]%'
),
Documents AS (
    SELECT l.Data AS DocumentDate,
           CAST(N'Lista płac: ' + COALESCE(NULLIF(LTRIM(RTRIM(d.Symbol)), N''), N'Bez definicji') AS nvarchar(256)) AS rodzaj
    FROM dbo.ListyPlac l
    LEFT JOIN dbo.DefListPlac d ON d.ID = l.Definicja
    CROSS JOIN Parameters p
    WHERE l.Data >= p.DateFrom AND l.Data < p.DateTo
    UNION ALL
    SELECT w.Data AS DocumentDate,
           CAST(CASE WHEN w.Bufor = 1 THEN N'Wypłata (bufor): '
                     WHEN w.Bufor = 0 THEN N'Wypłata: '
                     ELSE N'Wypłata (nieznany bufor): ' END
                + COALESCE(NULLIF(LTRIM(RTRIM(d.Symbol)), N''), N'Bez definicji') AS nvarchar(256)) AS rodzaj
    FROM dbo.Wyplaty w
    LEFT JOIN dbo.DefDokumentow d ON d.ID = w.Definicja
    CROSS JOIN Parameters p
    WHERE w.Data >= p.DateFrom AND w.Data < p.DateTo
),
Counts AS (
    SELECT DATEFROMPARTS(YEAR(d.DocumentDate), MONTH(d.DocumentDate), 1) AS MonthStart,
           d.rodzaj, COUNT_BIG(*) AS liczba
    FROM Documents d
    GROUP BY DATEFROMPARTS(YEAR(d.DocumentDate), MONTH(d.DocumentDate), 1), d.rodzaj
)
SELECT c.nip, CONVERT(char(7), m.MonthStart, 120) AS miesiac,
       d.rodzaj, COALESCE(d.liczba, 0) AS liczba
FROM Months m CROSS JOIN Company c
LEFT JOIN Counts d ON d.MonthStart = m.MonthStart
ORDER BY m.MonthStart, d.rodzaj
OPTION (MAXRECURSION 36);
