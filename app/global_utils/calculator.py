# app/globals/calculator.py
import sqlite3
from typing import Dict, Any
from app.config import DB_NAME


def _get_company_basic(firma_id: int) -> Dict[str, Any]:
    """
    Pobiera podstawowe dane firmy potrzebne do wyliczeń:
    - typ_rozliczenia (Ryczałt / KPIR / KH)
    - vat_czynny (0/1)
    - limit_dokumentow
    - glowny_cennik (klucz cennika)
    """
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            typ_rozliczenia,
            vat_czynny,
            limit_dokumentow,
            glowny_cennik
        FROM firmy
        WHERE id = ?
    """, (firma_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return {
            "typ_rozliczenia": "Ryczałt",
            "vat_czynny": 0,
            "limit_dokumentow": 0,
            "glowny_cennik": None
        }

    return {
        "typ_rozliczenia": row["typ_rozliczenia"] or "Ryczałt",
        "vat_czynny": int(row["vat_czynny"] or 0),
        "limit_dokumentow": int(row["limit_dokumentow"] or 0),
        "glowny_cennik": row["glowny_cennik"]
    }


def _get_docs_count_for_month(firma_id: int, miesiac: str) -> int:
    """
    Liczba dokumentów w danym miesiącu dla firmy.
    Zakładamy, że data_wyst jest w formacie YYYY-MM-DD.
    miesiac = '2026-08'
    """
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT COUNT(*) AS cnt
        FROM dokumenty
        WHERE firma_id = ?
          AND substr(data_wyst, 1, 7) = ?
    """, (firma_id, miesiac))

    row = cur.fetchone()
    conn.close()

    return int(row["cnt"] or 0)


def _get_pricing_for_key(klucz: str) -> Dict[str, Any]:
    """
    Pobiera wiersz z tabeli cenniki dla danego klucza.
    Zwraca wartości zgodne ze schematem:
      cena_netto_ksiegowosc, cena_brutto_ksiegowosc,
      cena_za_dokument_ponad_limit, zdrowotna, netto, brutto
    """
    if not klucz:
        return {
            "cena_netto_ksiegowosc": 0.0,
            "cena_brutto_ksiegowosc": 0.0,
            "cena_za_dokument_ponad_limit": 0.0,
            "zdrowotna": 0.0,
            "netto": 0.0,
            "brutto": 0.0
        }

    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT
            cena_netto_ksiegowosc,
            cena_brutto_ksiegowosc,
            cena_za_dokument_ponad_limit,
            zdrowotna,
            netto,
            brutto
        FROM cenniki
        WHERE klucz = ?
    """, (klucz,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return {
            "cena_netto_ksiegowosc": 0.0,
            "cena_brutto_ksiegowosc": 0.0,
            "cena_za_dokument_ponad_limit": 0.0,
            "zdrowotna": 0.0,
            "netto": 0.0,
            "brutto": 0.0
        }

    # defensywne rzutowania
    def _f(x):
        try:
            return float(x) if x is not None else 0.0
        except Exception:
            return 0.0

    return {
        "cena_netto_ksiegowosc": _f(row["cena_netto_ksiegowosc"]),
        "cena_brutto_ksiegowosc": _f(row["cena_brutto_ksiegowosc"]),
        "cena_za_dokument_ponad_limit": _f(row["cena_za_dokument_ponad_limit"]),
        "zdrowotna": _f(row["zdrowotna"]),
        "netto": _f(row["netto"]),
        "brutto": _f(row["brutto"]),
    }


def oblicz_miesieczny_koszt(firma_id: int, miesiac: str) -> Dict[str, float]:
    """
    Główna funkcja używana przez raporty:
    - zwraca:
      {
        "netto": ...        # księgowość
        "brutto": ...       # księgowość brutto
        "zdrowotna": ...    # kadry / zdrowotna
      }
    """
    firma = _get_company_basic(firma_id)
    docs_count = _get_docs_count_for_month(firma_id, miesiac)

    klucz = firma["glowny_cennik"]
    pricing = _get_pricing_for_key(klucz)

    limit = int(firma.get("limit_dokumentow") or 0)
    za_przekroczenie = float(pricing.get("cena_za_dokument_ponad_limit") or 0.0)
    netto_podstawowe = float(pricing.get("netto") or pricing.get("cena_netto_ksiegowosc") or 0.0)
    zdrowotna = float(pricing.get("zdrowotna") or 0.0)

    extra_cost = 0.0
    if limit and docs_count > limit:
        extra_docs = docs_count - limit
        extra_cost = extra_docs * za_przekroczenie

    netto = netto_podstawowe + extra_cost
    brutto = float(pricing.get("brutto") or pricing.get("cena_brutto_ksiegowosc") or (netto * 1.23))

    return {
        "netto": round(netto, 2),
        "brutto": round(brutto, 2),
        "zdrowotna": round(zdrowotna, 2),
    }
