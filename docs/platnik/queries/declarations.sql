-- Płatnik standard tables documented by ZUS, Struktury danych osobowych 1.9.
-- Two ODBC dates: first reporting month inclusive, last month exclusive.
-- Technical STATUSPT is optional and substituted only after metadata discovery.
-- No PESEL, contribution amounts, private keys or document bodies are read.
WITH Parameters AS (
    SELECT CAST(? AS date) AS DateFrom, CAST(? AS date) AS DateTo
), Records AS (
    SELECT N'ZUS DRA' AS form, a.ID_DOKUMENT AS source_id,
           a.ID_PLATNIK AS payer_id, a.II_1_NIP AS nip,
           CONVERT(nvarchar(16), a.I_2_2OKRESDEKLAR) AS reporting_period,
           CONVERT(nvarchar(16), a.I_2_1IDDEKLS) AS report_id,
           CAST(NULL AS int) AS employee_id, CAST(NULL AS nvarchar(256)) AS employee_name,
           __DRA_STATUS__ AS raw_status,
           CASE WHEN d.ID_DOKUMENT IS NOT NULL AND d.ID_PLATNIK = a.ID_PLATNIK THEN 1 ELSE 0 END AS valid_link
    FROM dbo.ZUSDRA a
    LEFT JOIN dbo.DOKUMENT d ON d.ID_DOKUMENT = a.ID_DOKUMENT
    UNION ALL
    SELECT N'ZUS RCA', a.ID_DOKUMENT, a.ID_PLATNIK, a.II_1_NIP,
           CONVERT(nvarchar(16), a.I_1_2OKRROZL), CONVERT(nvarchar(16), a.I_1_1IDRAPS),
           NULL, NULL, __RCA_STATUS__,
           CASE WHEN d.ID_DOKUMENT IS NOT NULL AND d.ID_PLATNIK = a.ID_PLATNIK THEN 1 ELSE 0 END
    FROM dbo.ZUSRCA a
    LEFT JOIN dbo.DOKUMENT d ON d.ID_DOKUMENT = a.ID_DOKUMENT
    UNION ALL
    SELECT N'ZUS RCA', u.ID_DOKUMENT, a.ID_PLATNIK, a.II_1_NIP,
           CONVERT(nvarchar(16), a.I_1_2OKRROZL), CONVERT(nvarchar(16), a.I_1_1IDRAPS),
           u.ID_UBEZPIECZONY,
           LTRIM(RTRIM(COALESCE(u.III_A_2_IMIEPIERW,N'') + N' ' + COALESCE(u.III_A_1_NAZWISKO,N''))),
           __RCA_STATUS__,
           CASE WHEN d.ID_DOKUMENT IS NOT NULL AND d.ID_PLATNIK = a.ID_PLATNIK
                     AND u.ID_PLATNIK = a.ID_PLATNIK THEN 1 ELSE 0 END
    FROM dbo.UBEZP_ZUSRCA u
    LEFT JOIN dbo.ZUSRCA a ON a.ID_DOKUMENT = u.ID_DOK_NAD
    LEFT JOIN dbo.DOKUMENT d ON d.ID_DOKUMENT = a.ID_DOKUMENT
), Periods AS (
    SELECT r.*, REPLACE(LTRIM(RTRIM(r.reporting_period)), N'/', N'') AS compact_period
    FROM Records r
), Dated AS (
    SELECT r.*, CASE WHEN LEN(r.compact_period) = 6 AND r.compact_period NOT LIKE N'%[^0-9]%'
                    THEN TRY_CONVERT(date, RIGHT(r.compact_period,4) + LEFT(r.compact_period,2) + N'01',112)
                    END AS reporting_date
    FROM Periods r
)
SELECT r.form, r.source_id, r.payer_id, r.nip, r.report_id,
       CONVERT(char(7), r.reporting_date,120) AS miesiac,
       r.employee_id, r.employee_name, r.raw_status, r.valid_link
FROM Dated r CROSS JOIN Parameters p
WHERE (r.reporting_date >= p.DateFrom AND r.reporting_date < p.DateTo)
   OR r.reporting_date IS NULL
ORDER BY r.payer_id, r.reporting_date, r.form, r.source_id;
