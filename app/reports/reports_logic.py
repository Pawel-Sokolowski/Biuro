# app/reports/reports_logic.py
import psycopg
from typing import List, Dict, Any
from app.services.calculator import oblicz_miesieczny_koszt
from app.db.dao_conn import get_conn
from app.db.dao_monthly import contracts_by_month
from app.utils.months import month_bounds
from app.security.authorization import assigned_company_ids, can, is_administrator

# ============================================================
# LISTA FIRM
# ============================================================
def get_all_companies(pracownik_id: int | None = None) -> List[Dict[str, Any]]:
    conn = get_conn()
    cur = conn.cursor()

    sql = "SELECT id, COALESCE(NULLIF(nazwa_skrocona, ''), nazwa) AS nazwa FROM firmy"
    params: List[int] = []
    if pracownik_id and not (
        is_administrator(pracownik_id) or can(pracownik_id, "can_view_finances")
    ):
        ids = assigned_company_ids(pracownik_id)
        if not ids:
            conn.close()
            return []
        placeholders = ",".join("%s" for _ in ids)
        sql += f" WHERE id IN ({placeholders})"
        params.extend(sorted(ids))
    cur.execute(sql + " ORDER BY 2", params)
    rows = cur.fetchall()
    conn.close()

    return [{"id": r["id"], "nazwa": r["nazwa"]} for r in rows]


def _authorized_company_ids(firma_ids: List[int], pracownik_id: int | None) -> List[int]:
    if not pracownik_id or is_administrator(pracownik_id) or can(pracownik_id, "can_view_finances"):
        return firma_ids
    allowed = assigned_company_ids(pracownik_id)
    return [firma_id for firma_id in firma_ids if firma_id in allowed]


# ============================================================
# RANGE MIESIĘCY
# ============================================================
def get_months_range(start: str, end: str) -> List[str]:
    from datetime import datetime
    out = []

    s = datetime.strptime(start, "%Y-%m")
    e = datetime.strptime(end, "%Y-%m")

    cur = s
    while cur <= e:
        out.append(cur.strftime("%Y-%m"))
        if cur.month == 12:
            cur = cur.replace(year=cur.year + 1, month=1)
        else:
            cur = cur.replace(month=cur.month + 1)

    return out


# ============================================================
# GODZINY DLA FIRMY I MIESIĄCA
# ============================================================
def get_imported_seconds(firma_ids: List[int], months: List[str]) -> Dict[tuple, Dict[str, float]]:
    """Zaimportowany czas pracy: (firma_id, miesiąc) -> {'ksiegowosc': s, 'kadry': s}."""
    if not firma_ids or not months:
        return {}
    ph_f = ",".join("%s" for _ in firma_ids)
    ph_m = ",".join("%s" for _ in months)
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            SELECT firma_id, miesiac, dzial, SUM(sekundy) AS sek
            FROM import_czas_pracy
            WHERE firma_id IN ({ph_f}) AND miesiac IN ({ph_m})
            GROUP BY firma_id, miesiac, dzial
            """,
            [*firma_ids, *months],
        ).fetchall()
    finally:
        conn.close()
    out: Dict[tuple, Dict[str, float]] = {}
    for r in rows:
        slot = out.setdefault((r["firma_id"], r["miesiac"]), {"ksiegowosc": 0.0, "kadry": 0.0})
        key = "kadry" if r["dzial"] in ("kadry", "inne_kadry") else "ksiegowosc"
        slot[key] += float(r["sek"] or 0)
    return out


def get_hours_for_company_month(firma_id: int, miesiac: str) -> Dict[str, float]:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT sekundy_ksiegowosc, sekundy_kadry
        FROM statusy_miesieczne
        WHERE firma_id = %s AND miesiac = %s
    """, (firma_id, miesiac))

    row = cur.fetchone()
    conn.close()

    imported = get_imported_seconds([firma_id], [miesiac]).get((firma_id, miesiac), {})
    return {
        "ksiegowosc": float((row["sekundy_ksiegowosc"] if row else 0) or 0) + imported.get("ksiegowosc", 0.0),
        "kadry": float((row["sekundy_kadry"] if row else 0) or 0) + imported.get("kadry", 0.0),
    }


# ============================================================
# USŁUGI DODATKOWE — SUMA
# ============================================================
def get_extra_services_value(firma_id: int, miesiac: str) -> float:
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT SUM(ilosc * cena_netto) AS suma
        FROM uslugi_dodatkowe_firma
        WHERE firma_id = %s AND miesiac = %s AND fakturowac = 1
    """, (firma_id, miesiac))

    row = cur.fetchone()
    conn.close()

    return float(row["suma"] or 0)


def build_summary_report(
    firma_ids: List[int],
    months: List[str],
    pracownik_id: int | None = None,
) -> List[Dict[str, Any]]:
    """Return employee-attributed work rows for the Reports/Zestawienie view."""
    firma_ids = _authorized_company_ids(firma_ids, pracownik_id)
    if not firma_ids or not months:
        return []

    conn = get_conn()
    placeholders_firm = ",".join("%s" for _ in firma_ids)
    placeholders_month = ",".join("%s" for _ in months)
    rows = conn.execute(
        f"""
        SELECT c.firma_id, c.miesiac, c.typ, c.sekundy,
               COALESCE(NULLIF(f.nazwa_skrocona, ''), f.nazwa) AS klient,
               COALESCE(p.imie || ' ' || p.nazwisko, 'Nieprzypisany') AS ksiegowa
        FROM czasomierze c
        JOIN firmy f ON f.id = c.firma_id
        LEFT JOIN pracownicy p ON p.id = c.pracownik_id
        WHERE c.firma_id IN ({placeholders_firm})
          AND c.miesiac IN ({placeholders_month})
        ORDER BY COALESCE(NULLIF(f.nazwa_skrocona, ''), f.nazwa), c.miesiac, ksiegowa, c.typ
        """,
        [*firma_ids, *months],
    ).fetchall()
    conn.close()

    summary: Dict[tuple, Dict[str, Any]] = {}
    for row in rows:
        key = (row["firma_id"], row["miesiac"], row["ksiegowa"], row["typ"])
        item = summary.setdefault(
            key,
            {
                "firma_id": row["firma_id"],
                "miesiac": row["miesiac"],
                "klient": row["klient"],
                "ksiegowa": row["ksiegowa"],
                "typ": row["typ"],
                "sekundy": 0,
            },
        )
        item["sekundy"] += int(row["sekundy"] or 0)

    # Existing monthly pricing is used as the service value, divided by the
    # department's tracked time so every employee row remains comparable.
    for item in summary.values():
        koszt = oblicz_miesieczny_koszt(item["firma_id"], item["miesiac"])
        extra = get_extra_services_value(item["firma_id"], item["miesiac"])
        value = float(koszt["zdrowotna"] if item["typ"] == "kadry" else koszt["netto"] + extra)
        total = max(1, sum(
            x["sekundy"]
            for x in summary.values()
            if x["firma_id"] == item["firma_id"]
            and x["miesiac"] == item["miesiac"]
            and x["typ"] == item["typ"]
        ))
        item["cena_za_usluge"] = round(value * item["sekundy"] / total, 2)

    return list(summary.values())


# ============================================================
# RAPORT MIESIĘCZNY — POPRAWIONY
# ============================================================
def build_report(
    firma_ids: List[int],
    months: List[str],
    pracownik_id: int | None = None,
) -> Dict[str, Any]:
    firma_ids = _authorized_company_ids(firma_ids, pracownik_id)
    if not firma_ids or not months:
        return {}

    placeholders_firm = ",".join("%s" for _ in firma_ids)
    placeholders_month = ",".join("%s" for _ in months)
    range_start = month_bounds(min(months))[0]
    range_end = month_bounds(max(months))[1]
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            WITH selected_months(miesiac) AS (
                SELECT %s {" UNION ALL SELECT %s" * (len(months) - 1)}
            ),
            doc_counts AS (
                SELECT firma_id, substr(data_wyst, 1, 7) AS miesiac, COUNT(*) AS dokumenty
                FROM dokumenty
                WHERE firma_id IN ({placeholders_firm})
                  AND data_wyst >= %s AND data_wyst < %s
                GROUP BY firma_id, substr(data_wyst, 1, 7)
            ),
            extras AS (
                SELECT firma_id, miesiac, SUM(ilosc * cena_netto) AS extra
                FROM uslugi_dodatkowe_firma
                WHERE firma_id IN ({placeholders_firm})
                  AND miesiac IN ({placeholders_month})
                  AND fakturowac = 1
                GROUP BY firma_id, miesiac
            )
            SELECT f.id AS firma_id, m.miesiac,
                   COALESCE(sm.sekundy_ksiegowosc, 0) AS sekundy_ksiegowosc,
                   COALESCE(sm.sekundy_kadry, 0) AS sekundy_kadry,
                   -- Te same dane co w widoku miesięcznym: rzeczywiste dokumenty
                   -- z tabeli dokumenty albo ręczna/importowana liczba miesięczna.
                   -- Limit firmy wpływa wyłącznie na cenę, nigdy na tę liczbę.
                   COALESCE(NULLIF(dc.dokumenty, 0), sm.ilosc_dok, 0) AS dokumenty,
                   COALESCE(ex.extra, 0) AS extra,
                   COALESCE(c.netto, c.cena_netto_ksiegowosc, 0) AS netto,
                   COALESCE(c.brutto, c.cena_brutto_ksiegowosc, 0) AS brutto,
                   COALESCE(c.zdrowotna, 0) AS zdrowotna,
                   COALESCE(f.limit_dokumentow, 0) AS limit_dokumentow,
                   COALESCE(f.uop_ilosc, 0) + COALESCE(f.uz_ilosc, 0) + COALESCE(f.uod_ilosc, 0) AS umowy,
                   COALESCE(c.cena_za_dokument_ponad_limit, 0) AS cena_za_dokument
            FROM firmy f
            CROSS JOIN selected_months m
            LEFT JOIN statusy_miesieczne sm
              ON sm.firma_id = f.id AND sm.miesiac = m.miesiac
            LEFT JOIN doc_counts dc ON dc.firma_id = f.id AND dc.miesiac = m.miesiac
            LEFT JOIN extras ex ON ex.firma_id = f.id AND ex.miesiac = m.miesiac
            LEFT JOIN cenniki c ON c.klucz = f.glowny_cennik
            WHERE f.id IN ({placeholders_firm})
            """,
            [
                *months,
                *firma_ids, range_start, range_end,
                *firma_ids, *months,
                *firma_ids,
            ],
        ).fetchall()
    finally:
        conn.close()

    imported = get_imported_seconds(firma_ids, months)
    conn = get_conn()
    try:
        contracts = contracts_by_month(conn, firma_ids, months)
    finally:
        conn.close()
    out = {firma_id: {} for firma_id in firma_ids}
    for row in rows:
        docs = int(row["dokumenty"] or 0)
        limit = int(row["limit_dokumentow"] or 0)
        extra_docs = max(0, docs - limit) if limit else 0
        netto = float(row["netto"] or 0) + extra_docs * float(row["cena_za_dokument"] or 0)
        extra = float(row["extra"] or 0)
        koszt_ksieg = netto + extra
        koszt_kadry = float(row["zdrowotna"] or 0)
        imp = imported.get((row["firma_id"], row["miesiac"]), {})
        godz_ksieg = float(row["sekundy_ksiegowosc"] or 0) + imp.get("ksiegowosc", 0.0)
        godz_kadry = float(row["sekundy_kadry"] or 0) + imp.get("kadry", 0.0)
        out[row["firma_id"]][row["miesiac"]] = {
            "koszt_ksiegowosc": round(koszt_ksieg, 2),
            "koszt_kadry": round(koszt_kadry, 2),
            "godziny_ksiegowosc": godz_ksieg,
            "godziny_kadry": godz_kadry,
            "koszt_na_godzine_ksiegowosc": koszt_ksieg / godz_ksieg if godz_ksieg else 0,
            "koszt_na_godzine_kadry": koszt_kadry / godz_kadry if godz_kadry else 0,
            "extra": extra,
            "dokumenty": docs,
            "umowy": contracts.get((row["firma_id"], row["miesiac"]), int(row["umowy"] or 0)),
        }
    return out


# ============================================================
# RAPORT ROCZNY — wszystkie 12 miesięcy
# ============================================================
def build_yearly_report(firma_id: int, year: int) -> Dict[str, Any]:
    months = [f"{year}-{m:02d}" for m in range(1, 12 + 1)]
    return build_report([firma_id], months)[firma_id]


# ============================================================
# PORÓWNANIE FIRM — POPRAWIONE
# ============================================================
def compare_companies(
    firma_ids: List[int],
    miesiac: str,
    pracownik_id: int | None = None,
) -> Dict[str, Any]:
    firma_ids = _authorized_company_ids(firma_ids, pracownik_id)
    out = {}

    for firma_id in firma_ids:
        koszt = oblicz_miesieczny_koszt(firma_id, miesiac)
        godziny = get_hours_for_company_month(firma_id, miesiac)
        extra = get_extra_services_value(firma_id, miesiac)

        koszt_ksieg = koszt["netto"] + extra
        koszt_kadry = koszt["zdrowotna"]

        out[firma_id] = {
            "koszt_ksiegowosc": koszt_ksieg,
            "koszt_kadry": koszt_kadry,
            "godziny_ksiegowosc": godziny["ksiegowosc"],
            "godziny_kadry": godziny["kadry"],
            "koszt_na_godzine_ksiegowosc": koszt_ksieg / godziny["ksiegowosc"] if godziny["ksiegowosc"] > 0 else 0,
            "koszt_na_godzine_kadry": koszt_kadry / godziny["kadry"] if godziny["kadry"] > 0 else 0,
            "extra": extra
        }

    return out


# ============================================================
# ZAROBEK NA GODZINĘ — POPRAWIONY
# ============================================================
def build_profit_report(
    firma_ids: List[int],
    months: List[str],
    pracownik_id: int | None = None,
) -> Dict[str, Any]:
    firma_ids = _authorized_company_ids(firma_ids, pracownik_id)
    imported = get_imported_seconds(firma_ids, months)
    conn = get_conn()
    cur = conn.cursor()

    out = {}

    for fid in firma_ids:
        out[fid] = {}

        for miesiac in months:
            cur.execute("""
                SELECT sekundy_ksiegowosc, sekundy_kadry, wartosc_uslug
                FROM statusy_miesieczne
                WHERE firma_id = %s AND miesiac = %s
            """, (fid, miesiac))

            row = cur.fetchone()
            imp = imported.get((fid, miesiac), {})

            if not row and not imp:
                out[fid][miesiac] = {
                    "profit_kadry": 0,
                    "profit_ksiegowosc": 0
                }
                continue

            sek_ks = float((row["sekundy_ksiegowosc"] if row else 0) or 0) + imp.get("ksiegowosc", 0.0)
            sek_kad = float((row["sekundy_kadry"] if row else 0) or 0) + imp.get("kadry", 0.0)
            wartosc = float((row["wartosc_uslug"] if row else 0) or 0)

            extra = get_extra_services_value(fid, miesiac)
            wartosc += extra

            wart_ks = wartosc * 0.5
            wart_kad = wartosc * 0.5

            profit_ks = wart_ks / (sek_ks / 3600) if sek_ks > 0 else 0
            profit_kad = wart_kad / (sek_kad / 3600) if sek_kad > 0 else 0

            out[fid][miesiac] = {
                "profit_kadry": profit_kad,
                "profit_ksiegowosc": profit_ks,
                "extra": extra
            }

    conn.close()
    return out
