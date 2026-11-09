-- Read-only diagnostic: June 2026. Select the KH client database in SSMS.
-- Count positions attached to the SAME bank statements as the document report.
-- Compare imported bank rows with payment rows; do not add these sources together.
-- Raport is polymorphic: inspect RaportType before accepting a Zaplaty group.
-- Status/type filters are still unverified. This is not a production count_query.
WITH MonthDocuments AS (
    SELECT d.ID, d.Dokument, d.DokumentType, def.Symbol AS SymbolDokumentu
    FROM dbo.DokEwidencja AS d
    LEFT JOIN dbo.DefDokumentow AS def ON def.ID = d.Definicja
    WHERE d.DataEwidencji >= '20260601'
      AND d.DataEwidencji < '20260701'
),
BankHeaders AS (
    -- WBEW is the bank-statement definition identified in the diagnostic output.
    -- Other cash/report definitions must not automatically receive the /3 rule.
    SELECT * FROM MonthDocuments WHERE SymbolDokumentu = N'WBEW'
),
StatementIds AS (
    SELECT DISTINCT r.ID
    FROM dbo.RaportyESP AS r
    WHERE EXISTS (
        SELECT 1 FROM BankHeaders AS d
        WHERE d.DokumentType = N'RaportyESP' AND d.Dokument = r.ID
    )
),
Totals AS (
    SELECT COUNT(*) AS dokumenty_w_ewidencji,
           COUNT(CASE WHEN SymbolDokumentu = N'WBEW' THEN 1 END) AS naglowki_wyciagow,
           COUNT(*) - COUNT(CASE WHEN SymbolDokumentu = N'WBEW' THEN 1 END) AS pozostale_dokumenty
    FROM MonthDocuments
),
PositionCounts AS (
    SELECT N'OperacjeBankowe / import' AS zrodlo_pozycji,
           COUNT(*) AS pozycje_bankowe
    FROM dbo.OperacjeBankowe AS o
    WHERE EXISTS (SELECT 1 FROM StatementIds AS s WHERE s.ID = o.WyciagBankowy)
    UNION ALL
    SELECT N'Zaplaty / ' + COALESCE(z.RaportType, N'NULL'), COUNT(*)
    FROM dbo.Zaplaty AS z
    WHERE EXISTS (SELECT 1 FROM StatementIds AS s WHERE s.ID = z.Raport)
    GROUP BY z.RaportType
)
SELECT DB_NAME() AS baza,
       '2026-06' AS miesiac,
       t.dokumenty_w_ewidencji,
       t.naglowki_wyciagow,
       (SELECT COUNT(*) FROM StatementIds) AS powiazane_wyciagi,
       (SELECT COUNT(*) FROM BankHeaders AS d
        WHERE NOT EXISTS (SELECT 1 FROM StatementIds AS s
                          WHERE s.ID = d.Dokument AND d.DokumentType = N'RaportyESP')) AS naglowki_bez_powiazania,
       t.pozostale_dokumenty,
       p.zrodlo_pozycji,
       p.pozycje_bankowe,
       CAST(p.pozycje_bankowe / 3.0 AS decimal(18,2)) AS pozycje_podzielone_przez_3,
       CAST(ROUND(t.pozostale_dokumenty + p.pozycje_bankowe / 3.0, 0) AS int) AS razem_po_zaokragleniu
FROM Totals AS t
CROSS JOIN PositionCounts AS p
ORDER BY p.zrodlo_pozycji;
