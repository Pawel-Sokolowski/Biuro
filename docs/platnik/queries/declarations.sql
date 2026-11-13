-- Shipped template for standard Płatnik forms. No private schema exports.
-- The worker inserts only fixed catalogue identifiers after checking metadata.
-- Two ODBC dates delimit reporting months, document-filled dates or reporting years.
WITH Parameters AS (
    SELECT CAST(? AS date) AS DateFrom, CAST(? AS date) AS DateTo
), Records AS (
__RECORDS__
), Periods AS (
    SELECT r.*, REPLACE(LTRIM(RTRIM(r.source_period)),N'/',N'') AS compact_period
    FROM Records r
), Dated AS (
    SELECT r.*, CASE
        WHEN r.period_kind=N'filled_date' THEN TRY_CONVERT(date,r.source_period,23)
        WHEN r.period_kind=N'year' AND LEN(r.compact_period)=4 AND r.compact_period NOT LIKE N'%[^0-9]%'
            THEN TRY_CONVERT(date,r.compact_period + N'0101',112)
        WHEN r.period_kind=N'month' AND LEN(r.compact_period)=6 AND r.compact_period NOT LIKE N'%[^0-9]%'
            THEN TRY_CONVERT(date,RIGHT(r.compact_period,4) + LEFT(r.compact_period,2) + N'01',112)
        END AS reporting_date
    FROM Periods r
)
SELECT r.form,r.source_id,r.payer_id,r.nip,r.report_id,
       __PERIOD_RESULT__,r.employee_id,r.employee_name,r.raw_status,r.valid_link
FROM Dated r CROSS JOIN Parameters p
WHERE (r.reporting_date>=p.DateFrom AND r.reporting_date<p.DateTo) OR r.reporting_date IS NULL
ORDER BY r.payer_id,r.reporting_date,r.form,r.source_id;
