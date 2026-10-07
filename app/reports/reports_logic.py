# app/reports/reports_logic.py
import sqlite3
from typing import List, Dict, Any
from app.global_utils.calculator import oblicz_miesieczny_koszt
import os
from dotenv import load_dotenv
from app.db.dao_conn import get_conn

load_dotenv()
DB_NAME = os.getenv("DB_NAME")

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
    conn = sqlite3.connect(DB_NAME)
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
    out = {}

    for firma_id in firma_ids:
        out[firma_id] = {}

        for miesiac in months:
            koszt = oblicz_miesieczny_koszt(firma_id, miesiac)
            godziny = get_hours_for_company_month(firma_id, miesiac)
            extra = get_extra_services_value(firma_id, miesiac)

            koszt_ksieg = koszt["netto"] + extra
            koszt_kadry = koszt["zdrowotna"]

            godz_ksieg = godziny["ksiegowosc"]
            godz_kadry = godziny["kadry"]

            out[firma_id][miesiac] = {
                "koszt_ksiegowosc": koszt_ksieg,
                "koszt_kadry": koszt_kadry,
                "godziny_ksiegowosc": godz_ksieg,
                "godziny_kadry": godz_kadry,
                "koszt_na_godzine_ksiegowosc": koszt_ksieg / godz_ksieg if godz_ksieg > 0 else 0,
                "koszt_na_godzine_kadry": koszt_kadry / godz_kadry if godz_kadry > 0 else 0,
                "extra": extra
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
    conn = sqlite3.connect(DB_NAME)
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
