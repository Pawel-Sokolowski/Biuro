-- Read-only ODBC query: inclusive start and exclusive end date parameters.
-- Returns all requested months, including zero, plus the company NIP from Enova.
WITH Parameters AS (
    SELECT CAST(? AS date) AS DateFrom, CAST(? AS date) AS DateTo,
           CONVERT(nvarchar(12), SESSION_CONTEXT(N'biuro_accounting_type')) AS Ledger,
           TRY_CONVERT(bit, SESSION_CONTEXT(N'biuro_vat_czynny')) AS WithVat
),
Months AS (
    SELECT DateFrom AS MonthStart FROM Parameters WHERE DateFrom < DateTo
    UNION ALL
    SELECT DATEADD(month, 1, m.MonthStart)
    FROM Months AS m CROSS JOIN Parameters AS p
    WHERE DATEADD(month, 1, m.MonthStart) < p.DateTo
),
RawNips AS (
    SELECT REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(UPPER(LTRIM(RTRIM(NIP))),
           N'-', N''), N' ', N''), NCHAR(9), N''), NCHAR(10), N''), NCHAR(13), N''), NCHAR(160), N'') AS Value
    FROM dbo.HstDanychFirmy
    WHERE Termin=(SELECT MAX(Termin) FROM dbo.HstDanychFirmy WHERE Termin<=CONVERT(date,GETDATE()))
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
    -- Worker supplies the saved company mapping only when company history has no valid NIP.
    -- Never reinterpret PESEL or replace a conflicting valid NIP.
    SELECT mapped.nip
    FROM (SELECT CONVERT(nvarchar(32), SESSION_CONTEXT(N'biuro_company_nip')) AS nip) mapped
    WHERE LEN(mapped.nip) = 10 AND mapped.nip NOT LIKE N'%[^0-9]%'
      AND NOT EXISTS (SELECT 1 FROM ValidNips)
),
Entries AS (
    SELECT DATEFROMPARTS(YEAR(d.Data), MONTH(d.Data), 1) AS MonthStart,
           e.Definicja, e.Dokument, e.DokumentType
    FROM dbo.Dziennik d CROSS JOIN Parameters p
    LEFT JOIN dbo.DokEwidencja e ON e.ID = d.Ewidencja
    WHERE p.Ledger = N'KPIR' AND d.KPiRLp > 0 AND d.Data >= p.DateFrom AND d.Data < p.DateTo
    UNION ALL
    SELECT DATEFROMPARTS(YEAR(d.DataEwidencji), MONTH(d.DataEwidencji), 1),
           d.Definicja, d.Dokument, d.DokumentType
    FROM dbo.DokEwidencja d CROSS JOIN Parameters p
    WHERE p.Ledger IN (N'KH', N'RYCZALT') AND d.DataEwidencji >= p.DateFrom AND d.DataEwidencji < p.DateTo
),
Classified AS (
    SELECT e.*, COALESCE(f.Symbol, N'Bez definicji') AS Symbol, f.Typ AS DefinitionType,
           st.Typ AS AssetType,
           COALESCE(df.Typ, f.Typ, 0) AS FormType,
           UPPER(REPLACE(REPLACE(COALESCE(NULLIF(df.Symbol, N''), NULLIF(f.Symbol, N''), N''), N'-', N''), N' ', N'')) AS FormKey
    FROM Entries e
    LEFT JOIN dbo.DefDokumentow f ON f.ID = e.Definicja
    LEFT JOIN dbo.DokumentyST st ON e.DokumentType = N'DokumentyST' AND st.ID = e.Dokument
    LEFT JOIN dbo.Deklaracje decl ON e.DokumentType = N'Deklaracje' AND decl.ID = e.Dokument
    LEFT JOIN dbo.DefDokumentow df ON df.ID = decl.Definicja
),
LedgerDocuments AS (
    SELECT d.MonthStart, CASE WHEN d.AssetType = 1303 THEN N'AMO' ELSE d.Symbol END AS Symbol,
           CASE WHEN d.Symbol IN (N'DW', N'DWNNC', N'DWNNZ') OR d.DefinitionType IN (1206,1207) THEN N'DW'
                WHEN d.Symbol IN (N'SPU',N'SPR',N'SPD',N'SPT',N'SPUUE',N'SPTUE',N'SPUEX',N'SPTEX',N'VME')
                     OR d.DefinitionType IN (1201,1205,1210) THEN N'SPU'
                WHEN d.Symbol IN (N'ZR',N'ZP',N'ZT',N'ZUE',N'ZŚT',N'ZST')
                     OR d.DefinitionType = 1202 THEN N'ZR'
                ELSE N'DW' END AS Category
    FROM Classified d CROSS JOIN Parameters p
    WHERE d.Symbol NOT IN (N'OT',N'LT',N'RKVE',N'RKEW')
      AND COALESCE(d.AssetType, d.DefinitionType, 0) NOT IN (1301,1302)
      AND (p.Ledger <> N'KH' OR d.Symbol <> N'WBEW')
      -- The declaration rules apply only to their ledger. Count the included forms below.
      AND NOT (p.Ledger = N'KPIR' AND (d.FormType IN (207,1004,1014,1018,3000)
          OR d.FormKey IN (N'PIT',N'PIT5',N'PIT5L',N'PPE',N'DRA',N'DRA2',N'ZUSDRA',N'ZUSDRAII',
              N'VAT',N'VAT7',N'VAT7K',N'VAT7D',N'JPKVAT',N'JPKV7M',N'JPKV7K')))
      AND NOT (p.Ledger = N'KH' AND (d.FormType IN (207,1012,1023,1100)
          OR d.FormKey IN (N'CIT',N'CIT2',N'CIT8',N'CIT8E',
              N'VAT',N'VAT7',N'VAT7K',N'VAT7D',N'JPKVAT',N'JPKV7M',N'JPKV7K')))
),
Declarations AS (
    SELECT DATEFROMPARTS(YEAR(d.OkresTo), MONTH(d.OkresTo), 1) AS MonthStart,
           CASE WHEN p.Ledger = N'KPIR' AND f.Typ IN (1004,3000) THEN N'DRA'
                WHEN p.Ledger = N'KH' AND f.Typ = 1012 THEN N'CIT'
                WHEN p.Ledger = N'KH' AND f.Typ = 1023 THEN N'CIT-8'
                WHEN p.Ledger = N'KH' AND f.Typ = 1100 THEN N'CIT-8E'
                WHEN p.Ledger = N'KH' AND f.Typ = 207 THEN N'VAT' END AS Symbol
    FROM dbo.Deklaracje d CROSS JOIN Parameters p
    JOIN dbo.DefDokumentow f ON f.ID = d.Definicja
    WHERE ((p.Ledger = N'KPIR' AND f.Typ IN (1004,3000)) OR (p.Ledger = N'KH' AND f.Typ IN (207,1012,1023,1100)))
      AND d.OkresTo >= p.DateFrom AND d.OkresTo < p.DateTo
      AND COALESCE(d.Bufor,1) = 0 AND COALESCE(d.Deklaracja,0) = 0
      AND NOT EXISTS (SELECT 1 FROM dbo.EDeklaracje electronic
          WHERE electronic.ID = (SELECT MAX(x.ID) FROM dbo.EDeklaracje x WHERE x.Deklaracja = d.ID)
            AND electronic.StatusEDeklaracji = 100)
    UNION
    SELECT DATEFROMPARTS(YEAR(j.OkresTo), MONTH(j.OkresTo), 1), N'VAT'
    FROM dbo.JednolitePK j CROSS JOIN Parameters p
    WHERE p.Ledger = N'KH' AND j.OkresTo >= p.DateFrom AND j.OkresTo < p.DateTo
      AND j.FormCode IN (N'JPK_VAT',N'JPK_V7M',N'JPK_V7K') AND j.ZakresDanych IN (1,3)
      AND j.BramkaJPK = 3 AND j.StatusJPK IN (40,100)
),
IncludedDocuments AS (
    SELECT d.MonthStart,d.Symbol,d.Category FROM LedgerDocuments d CROSS JOIN Parameters p
    WHERE p.Ledger <> N'RYCZALT' OR d.Category = N'SPU' OR (p.WithVat = 1 AND d.Category = N'ZR')
    UNION ALL
    SELECT MonthStart,Symbol,N'DW' FROM Declarations WHERE Symbol IS NOT NULL
),
DocumentTypes AS (
    SELECT MonthStart, Symbol, COUNT_BIG(*) AS Quantity,
        SUM(CONVERT(bigint, CASE WHEN Category = N'SPU' THEN 1 ELSE 0 END)) AS Sales,
        SUM(CONVERT(bigint, CASE WHEN Category = N'ZR' THEN 1 ELSE 0 END)) AS Purchases
    FROM IncludedDocuments GROUP BY MonthStart, Symbol
),
Totals AS (
    SELECT MonthStart, SUM(Quantity) AS Quantity, SUM(Sales) AS Sales, SUM(Purchases) AS Purchases,
           N'{' + STRING_AGG(CAST(N'"' + STRING_ESCAPE(Symbol, 'json') + N'":'
               + CONVERT(nvarchar(20), Quantity) AS nvarchar(max)), N',') + N'}' AS Breakdown
    FROM DocumentTypes GROUP BY MonthStart
),
Reports AS (
    SELECT d.MonthStart,d.Dokument,d.DokumentType,
        MAX(CASE WHEN d.Symbol = N'WBEW' THEN 1 ELSE 0 END) AS Bank
    FROM Classified d CROSS JOIN Parameters p
    WHERE (p.Ledger = N'KH' AND d.Symbol = N'WBEW')
       OR (p.Ledger IN (N'KH',N'KPIR') AND (d.Symbol = N'RKEW' OR (d.Symbol = N'RKVE' AND d.DokumentType = N'RaportyESP')))
    GROUP BY d.MonthStart,d.Dokument,d.DokumentType
),
ReportCounts AS (
    SELECT r.MonthStart, r.Bank, COUNT_BIG(z.ID) AS Positions,
           MAX(CASE WHEN source.ID IS NULL THEN 1 ELSE 0 END) AS Broken
    FROM Reports r
    LEFT JOIN dbo.RaportyESP source ON r.DokumentType = N'RaportyESP' AND source.ID = r.Dokument
    LEFT JOIN dbo.Zaplaty z ON z.Raport = source.ID AND z.RaportType = N'RaportyESP'
    GROUP BY r.MonthStart,r.Bank
),
CashSettlements AS (
    -- Standard RKVE can be a single VAT cash settlement with no report behind it.
    SELECT d.MonthStart,COUNT_BIG(*) AS Positions
    FROM Classified d CROSS JOIN Parameters p
    WHERE p.Ledger IN (N'KH',N'KPIR') AND d.Symbol = N'RKVE'
      AND COALESCE(d.DokumentType,N'') <> N'RaportyESP'
    GROUP BY d.MonthStart
),
BrokenAssets AS (
    SELECT DISTINCT d.MonthStart FROM Classified d CROSS JOIN Parameters p
    WHERE p.Ledger IN (N'KH',N'KPIR') AND d.Symbol = N'STE' AND d.AssetType IS NULL
)
SELECT c.nip, CONVERT(char(7), m.MonthStart, 120) AS miesiac, COALESCE(t.Breakdown,N'{}') AS rodzaje,
       CASE WHEN asset.MonthStart IS NOT NULL THEN NULL ELSE COALESCE(t.Quantity,0) END AS dokumenty,
       CASE WHEN bank.Broken = 1 THEN NULL ELSE COALESCE(bank.Positions,0) END AS pozycje_bankowe,
       CASE WHEN cash.Broken = 1 THEN NULL ELSE COALESCE(cash.Positions,0) + COALESCE(rkve.Positions,0) END AS pozycje_kasowe,
       COALESCE(t.Sales,0) AS sprzedaz, COALESCE(t.Purchases,0) AS zakup
FROM Months m CROSS JOIN Company c CROSS JOIN Parameters p
LEFT JOIN Totals t ON t.MonthStart = m.MonthStart
LEFT JOIN ReportCounts bank ON bank.MonthStart = m.MonthStart AND bank.Bank = 1
LEFT JOIN ReportCounts cash ON cash.MonthStart = m.MonthStart AND cash.Bank = 0
LEFT JOIN CashSettlements rkve ON rkve.MonthStart = m.MonthStart
LEFT JOIN BrokenAssets asset ON asset.MonthStart = m.MonthStart
WHERE p.Ledger IN (N'KPIR', N'KH', N'RYCZALT') AND (p.Ledger <> N'RYCZALT' OR p.WithVat IS NOT NULL)
ORDER BY m.MonthStart
OPTION (MAXRECURSION 36);
