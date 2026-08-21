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
    Pobiera wiersz z tabeli cenniki dla danego klucza:
    klucz np. 'Ryczałt_N_20'
    """
    if not klucz:
        return {
            "cena_netto_2024": None,
            "wzrost_ilosci_dokumentow": 0,
            "cena_za_dokument": None,
            "za_przekroczenie_limitu_netto": 0.0,
            "zdrowotna": 0.0,
            "netto": 0.0,
            "brutto": 0.0
        }

    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT
            cena_netto_2024,
            wzrost_ilosci_dokumentow,
            cena_za_dokument,
            za_przekroczenie_limitu_netto,
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
            "cena_netto_2024": None,
            "wzrost_ilosci_dokumentow": 0,
            "cena_za_dokument": None,
            "za_przekroczenie_limitu_netto": 0.0,
            "zdrowotna": 0.0,
            "netto": 0.0,
            "brutto": 0.0
        }

    return {
        "cena_netto_2024": row["cena_netto_2024"],
        "wzrost_ilosci_dokumentow": int(row["wzrost_ilosci_dokumentow"] or 0),
        "cena_za_dokument": float(row["cena_za_dokument"] or 0.0) if row["cena_za_dokument"] is not None else None,
        "za_przekroczenie_limitu_netto": float(row["za_przekroczenie_limitu_netto"] or 0.0),
        "zdrowotna": float(row["zdrowotna"] or 0.0),
        "netto": float(row["netto"] or 0.0),
        "brutto": float(row["brutto"] or 0.0),
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

    limit = firma["limit_dokumentow"]
    za_przekroczenie = pricing["za_przekroczenie_limitu_netto"] or 0.0
    netto_podstawowe = pricing["netto"] or 0.0
    zdrowotna = pricing["zdrowotna"] or 0.0

    if limit and docs_count > limit:
        extra_docs = docs_count - limit
        extra_cost = extra_docs * za_przekroczenie
    else:
        extra_cost = 0.0

    netto = netto_podstawowe + extra_cost
    brutto = pricing["brutto"] or (netto * 1.23)

    return {
        "netto": round(netto, 2),
        "brutto": round(brutto, 2),
        "zdrowotna": round(zdrowotna, 2),
    }
