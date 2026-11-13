-- Bundled JPK VAT/V7M/V7K reader. JednolitePK columns match supplied Enova schema exports.
-- Latest correction wins even if an older filing already has UPO. Non-VAT JPK types are excluded.
-- StatusPlikuJPK/BramkaJPKFile verified in Soneta.Core 2506.0.0, 2606.0.1, 2610.0.2.
-- Acceptance requires Finished + official gateway + downloaded UPO, never a test submission.
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
    SELECT j.ID, j.OkresTo, j.NumerKorekty, j.StatusJPK, j.BramkaJPK,
           TRY_CONVERT(xml, CASE WHEN LEFT(CAST(j.UPO AS nvarchar(max)), 5) = N'<?xml' AND CHARINDEX(N'?>', CAST(j.UPO AS nvarchar(max))) > 0
                   THEN SUBSTRING(CAST(j.UPO AS nvarchar(max)), CHARINDEX(N'?>', CAST(j.UPO AS nvarchar(max))) + 2, 2147483647) ELSE CAST(j.UPO AS nvarchar(max)) END) AS Confirmation,
           ROW_NUMBER() OVER (PARTITION BY YEAR(j.OkresTo), MONTH(j.OkresTo)
                              ORDER BY CASE WHEN j.BramkaJPK = 3 THEN 0 ELSE 1 END, j.NumerKorekty DESC, j.ID DESC) AS Position
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
       CASE WHEN d.BramkaJPK <> 3 OR d.BramkaJPK IS NULL THEN N'unknown'
            WHEN d.StatusJPK = 100 AND NULLIF(d.Receipt, N'') IS NOT NULL THEN N'accepted'
            WHEN d.StatusJPK IN (10,15) THEN N'prepared'
            WHEN d.StatusJPK = 20 THEN N'not_sent'
            WHEN d.StatusJPK IN (30,40) THEN N'processing'
            WHEN d.StatusJPK = 200 THEN N'failed'
            ELSE N'unknown' END AS status,
       CASE WHEN d.BramkaJPK = 3 AND d.StatusJPK = 100 THEN COALESCE(d.Receipt, N'') ELSE N'' END AS upo,
       CASE WHEN d.ID IS NULL THEN N'Brak pliku JPK VAT w bazie'
            ELSE CONCAT(N'Enova StatusJPK=', d.StatusJPK, N' (',
                 CASE d.StatusJPK WHEN 10 THEN N'Created' WHEN 15 THEN N'WaitingForSignature'
                    WHEN 20 THEN N'ReadyToSend' WHEN 30 THEN N'SendingFiles' WHEN 40 THEN N'ProcessingByGate'
                    WHEN 100 THEN N'Finished' WHEN 200 THEN N'Failed' ELSE N'nieznany' END,
                 N'); bramka=', d.BramkaJPK, N' (',
                 CASE d.BramkaJPK WHEN 3 THEN N'oficjalna' WHEN 2 THEN N'testowa' ELSE N'nieokreślona' END, N')') END AS raw_status
FROM Months m CROSS JOIN Company c
LEFT JOIN Receipts d ON YEAR(d.OkresTo) = YEAR(m.MonthStart) AND MONTH(d.OkresTo) = MONTH(m.MonthStart)
ORDER BY m.MonthStart
OPTION (MAXRECURSION 36);
