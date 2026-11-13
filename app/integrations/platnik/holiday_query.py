"""Standard Płatnik SQL schema confirmed by the user's metadata export (SQL 2017).

RWS status codes deliberately stay raw. No inferred payments, notifications or senders.
"""
QUERY = r'''
SELECT r.ID AS source_id,r.ID_PLATNIK AS source_payer_id,r.ID_UBEZPIECZONY AS source_person_id,
    CASE WHEN p.nip_count=1 THEN p.nip END AS nip,
    CASE WHEN u.name_count=1 THEN u.person_name END AS imiona_nazwiska,
    r.ROK AS rok,MONTH(r.MIESIAC) AS miesiac,YEAR(r.MIESIAC) AS month_year,
    RTRIM(r.KOD_TYT_UBEZP) AS kod_ubezpieczenia,
    RTRIM(r.BAZOWY_KOD_TYTULU) AS bazowy_kod_ubezpieczenia,
    RTRIM(r.STATUS_ZWOLNIENIA) AS status_zrodlowy,
    r.SKLADKA_EM AS skladka_em,r.SKLADKA_REN AS skladka_ren,
    r.SKLADKA_WYP AS skladka_wyp,r.SKLADKA_CHOR AS skladka_chor,r.SKLADKA_FPFS AS skladka_fpfs
FROM dbo.RWS_ZWOLNIENIE r
OUTER APPLY (
    SELECT COUNT(DISTINCT NULLIF(REPLACE(REPLACE(UPPER(LTRIM(RTRIM(i.NIP))),'-',''),' ',''),'')) AS nip_count,
        MIN(NULLIF(REPLACE(REPLACE(UPPER(LTRIM(RTRIM(i.NIP))),'-',''),' ',''),'')) AS nip
    FROM dbo.PLATN_IDENT i WHERE i.ID_PLATNIK=r.ID_PLATNIK
) p
OUTER APPLY (
    SELECT COUNT(DISTINCT NULLIF(LTRIM(RTRIM(COALESCE(i.IMIEPIERW,'')+' '+COALESCE(i.NAZWISKO,''))),'')) AS name_count,
        MIN(NULLIF(LTRIM(RTRIM(COALESCE(i.IMIEPIERW,'')+' '+COALESCE(i.NAZWISKO,''))),'')) AS person_name
    FROM dbo.UBEZP_IDENT i WHERE i.ID_UBEZPIECZONY=r.ID_UBEZPIECZONY
        OR (r.ID_UB_ZUS>0 AND i.ID_UB_ZUS=r.ID_UB_ZUS AND i.ID_PL_ZUS=r.ID_PL_ZUS)
) u
WHERE r.ROK=?
ORDER BY r.ID_PLATNIK,r.ID_UBEZPIECZONY,r.MIESIAC,r.ID
'''
