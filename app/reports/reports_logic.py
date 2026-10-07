# app/reports/reports_logic.py
import sqlite3
from typing import List, Dict, Any
from app.services.calculator import oblicz_miesieczny_koszt
from app.db.dao_conn import get_conn
from app.utils.months import month_bounds

# ============================================================
# LISTA FIRM
# ============================================================
def get_all_companies() -> List[Dict[str, Any]]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT id, nazwa FROM firmy ORDER BY nazwa")
    rows = cur.fetchall()
    conn.close()

    return [{"id": r["id"], "nazwa": r["nazwa"]} for r in rows]


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
def get_hours_for_company_month(firma_id: int, miesiac: str) -> Dict[str, float]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT sekundy_ksiegowosc, sekundy_kadry
        FROM statusy_miesieczne
        WHERE firma_id = ? AND miesiac = ?
    """, (firma_id, miesiac))

    row = cur.fetchone()
    conn.close()

    if not row:
        return {"ksiegowosc": 0.0, "kadry": 0.0}

    return {
        "ksiegowosc": float(row["sekundy_ksiegowosc"] or 0),
        "kadry": float(row["sekundy_kadry"] or 0)
    }


# ============================================================
# USŁUGI DODATKOWE — SUMA
# ============================================================
def get_extra_services_value(firma_id: int, miesiac: str) -> float:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT SUM(ilosc * cena_netto) AS suma
        FROM uslugi_dodatkowe_firma
        WHERE firma_id = ? AND miesiac = ? AND fakturowac = 1
    """, (firma_id, miesiac))

    row = cur.fetchone()
    conn.close()

    return float(row["suma"] or 0)


def build_summary_report(firma_ids: List[int], months: List[str]) -> List[Dict[str, Any]]:
    """Return employee-attributed work rows for the Reports/Zestawienie view."""
    if not firma_ids or not months:
        return []

    conn = get_conn()
    conn.row_factory = sqlite3.Row
    placeholders_firm = ",".join("?" for _ in firma_ids)
    placeholders_month = ",".join("?" for _ in months)
    rows = conn.execute(
        f"""
        SELECT c.firma_id, c.miesiac, c.typ, c.sekundy,
               f.nazwa AS klient,
               COALESCE(p.imie || ' ' || p.nazwisko, 'Nieprzypisany') AS ksiegowa
        FROM czasomierze c
        JOIN firmy f ON f.id = c.firma_id
        LEFT JOIN pracownicy p ON p.id = c.pracownik_id
        WHERE c.firma_id IN ({placeholders_firm})
          AND c.miesiac IN ({placeholders_month})
        ORDER BY f.nazwa, c.miesiac, ksiegowa, c.typ
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
def build_report(firma_ids: List[int], months: List[str]) -> Dict[str, Any]:
    if not firma_ids or not months:
        return {}

    placeholders_firm = ",".join("?" for _ in firma_ids)
    placeholders_month = ",".join("?" for _ in months)
    range_start = month_bounds(min(months))[0]
    range_end = month_bounds(max(months))[1]
    conn = get_conn()
    try:
        rows = conn.execute(
            f"""
            WITH selected_months(miesiac) AS (
                SELECT ? {" UNION ALL SELECT ?" * (len(months) - 1)}
            ),
            doc_counts AS (
                SELECT firma_id, substr(data_wyst, 1, 7) AS miesiac, COUNT(*) AS dokumenty
                FROM dokumenty
                WHERE firma_id IN ({placeholders_firm})
                  AND data_wyst >= ? AND data_wyst < ?
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
                   COALESCE(dc.dokumenty, 0) AS dokumenty,
                   COALESCE(ex.extra, 0) AS extra,
                   COALESCE(c.netto, c.cena_netto_ksiegowosc, 0) AS netto,
                   COALESCE(c.brutto, c.cena_brutto_ksiegowosc, 0) AS brutto,
                   COALESCE(c.zdrowotna, 0) AS zdrowotna,
                   COALESCE(f.limit_dokumentow, 0) AS limit_dokumentow,
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

    out = {firma_id: {} for firma_id in firma_ids}
    for row in rows:
        docs = int(row["dokumenty"] or 0)
        limit = int(row["limit_dokumentow"] or 0)
        extra_docs = max(0, docs - limit) if limit else 0
        netto = float(row["netto"] or 0) + extra_docs * float(row["cena_za_dokument"] or 0)
        extra = float(row["extra"] or 0)
        koszt_ksieg = netto + extra
        koszt_kadry = float(row["zdrowotna"] or 0)
        godz_ksieg = float(row["sekundy_ksiegowosc"] or 0)
        godz_kadry = float(row["sekundy_kadry"] or 0)
        out[row["firma_id"]][row["miesiac"]] = {
            "koszt_ksiegowosc": round(koszt_ksieg, 2),
            "koszt_kadry": round(koszt_kadry, 2),
            "godziny_ksiegowosc": godz_ksieg,
            "godziny_kadry": godz_kadry,
            "koszt_na_godzine_ksiegowosc": koszt_ksieg / godz_ksieg if godz_ksieg else 0,
            "koszt_na_godzine_kadry": koszt_kadry / godz_kadry if godz_kadry else 0,
            "extra": extra,
        }
    return out


# ============================================================
# RAPORT ROCZNY — POPRAWIONY
# ============================================================
def build_yearly_report(firma_id: int, year: int) -> Dict[str, Any]:
    months = [f"{year}-{m:02d}" for m in range(1, 12 + 1)]
    return build_report([firma_id], months)[firma_id]


# ============================================================
# PORÓWNANIE FIRM — POPRAWIONE
# ============================================================
def compare_companies(firma_ids: List[int], miesiac: str) -> Dict[str, Any]:
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
def build_profit_report(firma_ids: List[int], months: List[str]) -> Dict[str, Any]:
    conn = get_conn()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    out = {}

    for fid in firma_ids:
        out[fid] = {}

        for miesiac in months:
            cur.execute("""
                SELECT sekundy_ksiegowosc, sekundy_kadry, wartosc_uslug
                FROM statusy_miesieczne
                WHERE firma_id = ? AND miesiac = ?
            """, (fid, miesiac))

            row = cur.fetchone()

            if not row:
                out[fid][miesiac] = {
                    "profit_kadry": 0,
                    "profit_ksiegowosc": 0
                }
                continue

            sek_ks = float(row["sekundy_ksiegowosc"] or 0)
            sek_kad = float(row["sekundy_kadry"] or 0)
            wartosc = float(row["wartosc_uslug"] or 0)

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
