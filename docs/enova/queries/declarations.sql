-- Bundled accounting declaration reader. Verified tables: Soneta EnovaDB Deklaracje.business.xml.
-- Read only; latest numbered declaration/correction per reporting month and normalized form.
-- The internal Enova enum is retained raw, never interpreted as an MF response code.
-- Transmission attempts are NOT declaration submission dates. Stamp is rowversion, not a date.
-- Only MF response 200 plus a downloaded UPO reference counts as accepted.
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
SourceDocuments AS (
    SELECT d.ID, d.OkresTo, d.NumerNumer, d.Bufor,
           UPPER(REPLACE(REPLACE(COALESCE(NULLIF(e.XmlDocument.value('(//*[local-name()="KodFormularza"]/text())[1]', 'nvarchar(64)'), N''),
               NULLIF(f.Symbol, N''), d.NumerSymbol), N'-', N''), N' ', N'')) AS FormKey,
           e.ID AS ElectronicID, e.StatusEDeklaracji,
           TRY_CONVERT(xml, CAST(e.Potwierdzenie AS nvarchar(max))) AS Confirmation
    FROM dbo.Deklaracje d
    LEFT JOIN dbo.DefDokumentow f ON f.ID = d.Definicja
    OUTER APPLY (
        SELECT TOP (1) x.ID, x.StatusEDeklaracji, x.Potwierdzenie,
               TRY_CONVERT(xml, CAST(x.Dokument AS nvarchar(max))) AS XmlDocument
        FROM dbo.EDeklaracje x WHERE x.Deklaracja = d.ID ORDER BY x.ID DESC
    ) e
    CROSS JOIN Parameters p
    WHERE d.OkresTo >= p.DateFrom AND d.OkresTo < p.DateTo
      AND (d.Deklaracja IS NULL OR d.Deklaracja = 0)
),
Forms AS (
    SELECT *, CASE
        WHEN FormKey IN (N'VAT7', N'VAT7K', N'VAT7D') THEN N'VAT'
        WHEN FormKey IN (N'VATUE', N'VATUEK') THEN N'VAT-UE'
        WHEN FormKey IN (N'PIT5', N'PIT5L', N'PIT', N'PPE') THEN N'PIT'
        WHEN FormKey IN (N'CIT2', N'CIT') THEN N'CIT'
        WHEN FormKey = N'CIT8' THEN N'CIT-8'
        WHEN FormKey IN (N'VAT8', N'VAT9M', N'VAT12', N'VAT27') THEN STUFF(FormKey, 4, 0, N'-')
        ELSE NULL END AS FormName
    FROM SourceDocuments
),
Ranked AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY YEAR(OkresTo), MONTH(OkresTo), FormName
                                ORDER BY NumerNumer DESC, ID DESC) AS Position
    FROM Forms WHERE FormName IS NOT NULL
),
Results AS (
    SELECT d.*, t.StatusETransmisji,
           d.Confirmation.value('(//*[local-name()="NumerReferencyjny"]/text())[1]', 'nvarchar(256)') AS Receipt
    FROM Ranked d
    OUTER APPLY (
        SELECT TOP (1) x.StatusETransmisji FROM dbo.ETransmisje x
        WHERE x.EDeklaracja = d.ElectronicID ORDER BY x.Czas DESC, x.ID DESC
    ) t
    WHERE d.Position = 1
)
SELECT c.nip, CONVERT(char(7), d.OkresTo, 120) AS miesiac,
       N'accounting' AS department, d.FormName AS form,
       CASE WHEN d.StatusETransmisji = 200 AND NULLIF(d.Receipt, N'') IS NOT NULL THEN N'accepted'
            WHEN d.StatusETransmisji IN (301,302,303,304) THEN N'processing'
            WHEN d.StatusETransmisji BETWEEN 400 AND 499 THEN N'rejected'
            WHEN d.ElectronicID IS NULL THEN N'prepared'
            ELSE N'unknown' END AS status,
       CAST(NULL AS datetime) AS sent_at,
       COALESCE(d.Receipt, N'') AS upo,
       TRY_CONVERT(datetimeoffset, d.Confirmation.value('(//*[local-name()="DataWytworzeniaPotwierdzenia"]/text())[1]', 'nvarchar(64)')) AS upo_at,
       CONCAT(N'Enova=', d.StatusEDeklaracji, N'; MF=', d.StatusETransmisji) AS raw_status
FROM Results d CROSS JOIN Company c
ORDER BY d.OkresTo, d.FormName
OPTION (MAXRECURSION 36);
