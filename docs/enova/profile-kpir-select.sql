-- Select the KPIR client database in SSMS, then execute this SELECT.
-- June 2026; adjust BOTH date bounds and the month label for another month.
-- Diagnostic only: status codes are displayed, not interpreted.
SELECT
    DB_NAME() AS baza,
    '2026-06' AS miesiac,
    d.Typ,
    d.Bufor,
    d.KPiRStan,
    CASE WHEN d.KPiRLp > 0 THEN 1 ELSE 0 END AS ma_numer_kpir,
    COUNT(*) AS wpisy_dziennika,
    COUNT(DISTINCT d.Ewidencja) AS rozne_id_ewidencji,
    MIN(CASE WHEN d.KPiRLp > 0 THEN d.KPiRLp END) AS pierwszy_numer_kpir,
    MAX(CASE WHEN d.KPiRLp > 0 THEN d.KPiRLp END) AS ostatni_numer_kpir
FROM dbo.Dziennik AS d
WHERE d.Data >= '20260601' AND d.Data < '20260701'
GROUP BY d.Typ, d.Bufor, d.KPiRStan,
         CASE WHEN d.KPiRLp > 0 THEN 1 ELSE 0 END
ORDER BY d.Typ, d.Bufor, d.KPiRStan, ma_numer_kpir;
