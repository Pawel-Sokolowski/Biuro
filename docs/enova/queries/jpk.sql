-- Bundled JPK VAT/V7M/V7K reader. JednolitePK columns match supplied Enova schema exports.
-- Latest correction wins even if an older filing already has UPO. Non-VAT JPK types are excluded.
-- A receipt reference comes only from the UPO column; internal StatusJPK is retained raw.
-- No document or missing receipt means unknown, never an invented rejection or not-applicable state.
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
    SELECT j.ID, j.OkresTo, j.NumerKorekty, j.StatusJPK,
           TRY_CONVERT(xml, CAST(j.UPO AS nvarchar(max))) AS Confirmation,
           ROW_NUMBER() OVER (PARTITION BY YEAR(j.OkresTo), MONTH(j.OkresTo)
                              ORDER BY j.NumerKorekty DESC, j.ID DESC) AS Position
    FROM dbo.JednolitePK j CROSS JOIN Parameters p
    WHERE j.OkresTo >= p.DateFrom AND j.OkresTo < p.DateTo
      AND UPPER(REPLACE(REPLACE(j.FormCode, N'_', N''), N'-', N''))
          IN (N'JPKVAT', N'JPKV7M', N'JPKV7K')
),
Receipts AS (
    SELECT *, Confirmation.value('(//*[local-name()="NumerReferencyjny"]/text())[1]', 'nvarchar(256)') AS Receipt
    FROM Documents WHERE Position = 1
)
SELECT c.nip, CONVERT(char(7), m.MonthStart, 120) AS miesiac,
       CASE WHEN NULLIF(d.Receipt, N'') IS NOT NULL THEN N'accepted' ELSE N'unknown' END AS status,
       COALESCE(d.Receipt, N'') AS upo,
       CASE WHEN d.ID IS NULL THEN N'Brak pliku JPK VAT w bazie'
            ELSE CONCAT(N'Enova StatusJPK=', d.StatusJPK) END AS raw_status
FROM Months m CROSS JOIN Company c
LEFT JOIN Receipts d ON YEAR(d.OkresTo) = YEAR(m.MonthStart) AND MONTH(d.OkresTo) = MONTH(m.MonthStart)
ORDER BY m.MonthStart
OPTION (MAXRECURSION 36);
