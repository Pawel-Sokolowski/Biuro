-- Select the KH client database in SSMS, then execute these three SELECTs.
-- June 2026; adjust BOTH date bounds and the month labels for another month.
-- Diagnostic only: do not sum all document groups before verifying the types.
SELECT
    DB_NAME() AS baza,
    '2026-06' AS miesiac,
    d.Definicja,
    def.Symbol,
    def.Nazwa,
    d.DokumentType,
    d.Typ,
    d.Stan,
    d.BuforK,
    d.NieKsiegowac,
    d.Szablon,
    COUNT(*) AS dokumenty_ewidencji
FROM dbo.DokEwidencja AS d
LEFT JOIN dbo.DefDokumentow AS def ON def.ID = d.Definicja
WHERE d.DataEwidencji >= '20260601' AND d.DataEwidencji < '20260701'
GROUP BY d.Definicja, def.Symbol, def.Nazwa, d.DokumentType,
         d.Typ, d.Stan, d.BuforK, d.NieKsiegowac, d.Szablon
ORDER BY def.Symbol, d.Stan, d.BuforK, d.DokumentType;

-- Relationships confirmed by the supplied foreign-key metadata:
-- OperacjeBankowe.WyciagBankowy -> RaportyESP.ID
-- OperacjeBankowe.Zaplata       -> Zaplaty.ID
-- Each operation is counted once; statement headers are not divided by 3.
SELECT
    DB_NAME() AS baza,
    '2026-06' AS miesiac,
    o.Stan AS stan_operacji,
    CASE WHEN z.ID IS NULL THEN 0 ELSE 1 END AS ma_zaplate,
    z.Stan AS stan_zaplaty,
    r.Bufor AS bufor_wyciagu,
    COUNT(*) AS pozycje_bankowe
FROM dbo.OperacjeBankowe AS o
LEFT JOIN dbo.Zaplaty AS z ON z.ID = o.Zaplata
LEFT JOIN dbo.RaportyESP AS r ON r.ID = o.WyciagBankowy
WHERE o.Data >= '20260601' AND o.Data < '20260701'
GROUP BY o.Stan, CASE WHEN z.ID IS NULL THEN 0 ELSE 1 END,
         z.Stan, r.Bufor
ORDER BY o.Stan, ma_zaplate, z.Stan, r.Bufor;

-- Show whether the date basis affects the count for this month.
SELECT N'Data' AS pole_daty, COUNT(*) AS pozycje_bankowe
FROM dbo.OperacjeBankowe
WHERE Data >= '20260601' AND Data < '20260701'
UNION ALL
SELECT N'DataOperacji', COUNT(*)
FROM dbo.OperacjeBankowe
WHERE DataOperacji >= '20260601' AND DataOperacji < '20260701';
