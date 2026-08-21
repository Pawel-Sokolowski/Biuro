# app/reports/reports_logic.py
import sqlite3
from typing import List, Dict, Any
from app.config import DB_NAME
from app.global_utils.calculator import oblicz_miesieczny_koszt


def get_all_companies() -> List[Dict[str, Any]]:
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT id, nazwa FROM firmy ORDER BY nazwa")
    rows = cur.fetchall()
    conn.close()

    return [{"id": r["id"], "nazwa": r["nazwa"]} for r in rows]


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


def get_hours_for_company_month(firma_id: int, miesiac: str) -> Dict[str, float]:
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT SUM(czas_ksiegowosc) AS ksieg,
               SUM(czas_kadry) AS kadry
        FROM stoper_miesieczny
        WHERE firma_id = ? AND miesiac = ?
    """, (firma_id, miesiac))

    row = cur.fetchone()
    conn.close()

    return {
        "ksiegowosc": float(row["ksieg"]) if row["ksieg"] else 0.0,
        "kadry": float(row["kadry"]) if row["kadry"] else 0.0
    }


def build_report(firma_ids: List[int], months: List[str]) -> Dict[str, Any]:
    out = {}

    for firma_id in firma_ids:
        out[firma_id] = {}

        for miesiac in months:
            koszt = oblicz_miesieczny_koszt(firma_id, miesiac)
            godziny = get_hours_for_company_month(firma_id, miesiac)

            koszt_ksieg = koszt["netto"]
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
            }

    return out


def build_yearly_report(firma_id: int, year: int) -> Dict[str, Any]:
    months = [f"{year}-{m:02d}" for m in range(1, 12 + 1)]
    return build_report([firma_id], months)[firma_id]


def compare_companies(firma_ids: List[int], miesiac: str) -> Dict[str, Any]:
    out = {}

    for firma_id in firma_ids:
        koszt = oblicz_miesieczny_koszt(firma_id, miesiac)
        godziny = get_hours_for_company_month(firma_id, miesiac)

        out[firma_id] = {
            "koszt_ksiegowosc": koszt["netto"],
            "koszt_kadry": koszt["zdrowotna"],
            "godziny_ksiegowosc": godziny["ksiegowosc"],
            "godziny_kadry": godziny["kadry"],
            "koszt_na_godzine_ksiegowosc": koszt["netto"] / godziny["ksiegowosc"] if godziny["ksiegowosc"] > 0 else 0,
            "koszt_na_godzine_kadry": koszt["zdrowotna"] / godziny["kadry"] if godziny["kadry"] > 0 else 0,
        }

    return out
