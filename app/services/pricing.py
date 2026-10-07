# app/global/pricing.py
from typing import Optional, Dict, Any
from app.db.dao_conn import get_conn


def _extract_limit_from_key(klucz: str) -> Optional[int]:
    """
    Extract numeric limit from cennik key if present.
    Example keys:
      - KPIR_N_50  -> returns 50
      - RYCZALT_T  -> returns None
    """
    if not klucz:
        return None
    parts = klucz.split("_")
    for part in reversed(parts):
        try:
            return int(part)
        except Exception:
            continue
    return None


def wybierz_cennik(typ: str, vat_czynny: bool, limit_dokumentow: int) -> Optional[Dict[str, Any]]:
    """
    Wybiera najlepszy cennik z tabeli `cenniki` dla podanych parametrów:
      - typ: "KPIR", "RYCZALT", "KR" (księgi rachunkowe) itp.
      - vat_czynny: True/False
      - limit_dokumentow: liczba dokumentów firmy

    Algorytm:
      1. Buduje wzorzec klucza na podstawie typu i VAT (np. "KPIR_T_%", "KPIR_N_%").
      2. Pobiera wszystkie pasujące wpisy.
      3. Jeśli w kluczu jest liczba (limit), wybiera największy limit nieprzekraczający limit_dokumentow.
      4. Jeśli nic nie pasuje, zwraca pierwszy znaleziony wpis jako fallback.
    """
    conn = get_conn()
    cur = conn.cursor()

    vat_suffix = "T" if vat_czynny else "N"
    pattern = f"{typ}_{vat_suffix}_%"

    cur.execute("SELECT * FROM cenniki WHERE klucz LIKE ? ORDER BY klucz", (pattern,))
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return None

    best = None
    best_lim = None

    for r in rows:
        klucz = r["klucz"]
        lim = _extract_limit_from_key(klucz)
        if lim is None:
            # skip keys without numeric limit for now
            continue
        if lim <= limit_dokumentow and (best_lim is None or lim > best_lim):
            best = r
            best_lim = lim

    if best is None:
        # fallback: choose first row (or the one with smallest numeric limit)
        try:
            rows_sorted = sorted(rows, key=lambda rr: (_extract_limit_from_key(rr["klucz"]) or 999999))
            best = rows_sorted[0]
        except Exception:
            best = rows[0]

    return dict(best)
