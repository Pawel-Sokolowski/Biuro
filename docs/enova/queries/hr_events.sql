-- Soneta.KadryPlace: current employment, pending training and medical validity.
WITH Parameters AS (
    SELECT CAST(? AS date) AS AsOf, CAST(? AS date) AS DateTo
), EmploymentHistory AS (
    SELECT h.Pracownik, h.ID, h.EtatOkresFrom, h.EtatOkresTo,
           ROW_NUMBER() OVER (PARTITION BY h.Pracownik ORDER BY h.AktualnoscFrom DESC,h.ID DESC) AS rn
    FROM dbo.PracHistorie h CROSS JOIN Parameters p
    WHERE h.AktualnoscFrom <= p.AsOf AND h.AktualnoscTo >= p.AsOf
), Contracts AS (
    SELECT h.Pracownik, CONCAT('etat:',h.ID) AS event_id,
           CASE WHEN YEAR(h.EtatOkresTo)<9999 THEN CONVERT(date,h.EtatOkresTo) END AS due_date,
           N'Umowa o pracę' AS contract_kind
    FROM EmploymentHistory h CROSS JOIN Parameters p
    WHERE h.rn=1 AND h.EtatOkresFrom > CONVERT(date,'19000101',112)
      AND h.EtatOkresFrom <= p.AsOf AND h.EtatOkresTo >= p.AsOf
    UNION ALL
    SELECT u.Pracownik, CONCAT('cywilna:',u.ID),
           CASE WHEN YEAR(u.OkresTo)<9999 THEN CONVERT(date,u.OkresTo) END,
           N'Umowa cywilnoprawna'
    FROM dbo.Umowy u CROSS JOIN Parameters p
    WHERE u.Anulowana=0 AND u.OkresFrom > CONVERT(date,'19000101',112)
      AND u.OkresFrom <= p.AsOf AND u.OkresTo >= p.AsOf
), Staff AS (
    SELECT DISTINCT Pracownik FROM Contracts
), Medical AS (
    SELECT m.Pracownik,m.ID,m.Data,m.Termin,m.WazneDo,
           ROW_NUMBER() OVER (PARTITION BY m.Pracownik,m.Definicja ORDER BY m.Data DESC,m.ID DESC) AS rn,
           CASE WHEN m.Data > CONVERT(date,'19000101',112) THEN 1 ELSE 0 END AS performed
    FROM dbo.BadaniaLekarskie m JOIN Staff s ON s.Pracownik=m.Pracownik
    CROSS JOIN Parameters p WHERE m.Anulowany=0 AND (m.Data IS NULL OR m.Data <= p.AsOf)
), Events AS (
    SELECT s.Pracownik, CONCAT('pracownik:',s.Pracownik) AS event_id,
           N'employee' AS event_type, CAST(NULL AS date) AS due_date, CAST(NULL AS nvarchar(64)) AS detail
    FROM Staff s
    UNION ALL
    SELECT c.Pracownik,c.event_id,N'contract_end',c.due_date,c.contract_kind FROM Contracts c
    UNION ALL
    SELECT m.Pracownik,CONCAT('badanie:',m.ID),N'medical_exam',
           CASE WHEN m.performed=1 AND YEAR(m.WazneDo) BETWEEN 1901 AND 9998 THEN CONVERT(date,m.WazneDo)
                WHEN m.performed=0 AND YEAR(m.Termin) BETWEEN 1901 AND 9998 THEN CONVERT(date,m.Termin) END,
           CASE WHEN m.performed=1 THEN N'Ważność badania' ELSE N'Termin badania' END
    FROM Medical m WHERE m.rn=1
    UNION ALL
    SELECT t.Pracownik,CONCAT('bhp:',t.ID),N'ohs_training',
           CASE WHEN YEAR(t.Termin) BETWEEN 1901 AND 9998 THEN CONVERT(date,t.Termin) END,
           N'Termin szkolenia'
    FROM dbo.SzkoleniaBHP t JOIN Staff s ON s.Pracownik=t.Pracownik CROSS JOIN Parameters p
    WHERE t.Anulowany=0 AND (t.Data IS NULL OR t.Data <= CONVERT(date,'19000101',112))
      AND NOT EXISTS (SELECT 1 FROM dbo.SzkoleniaBHP done
          WHERE done.Pracownik=t.Pracownik AND done.Definicja=t.Definicja AND done.Anulowany=0
            AND t.Termin > CONVERT(date,'19000101',112) AND done.Data>=t.Termin AND done.Data<=p.AsOf)
)
SELECT CONVERT(nvarchar(10),SESSION_CONTEXT(N'biuro_company_nip')) AS nip,
       e.event_id,e.Pracownik AS employee_id,LTRIM(RTRIM(CONCAT(w.Imie,N' ',w.Nazwisko))) AS employee_name,
       e.event_type,e.due_date,N'active' AS status,e.detail
FROM Events e JOIN dbo.Pracownicy w ON w.ID=e.Pracownik
CROSS JOIN Parameters p
WHERE e.due_date IS NULL OR e.due_date < p.DateTo
ORDER BY e.Pracownik,e.event_type,e.due_date,e.event_id;
