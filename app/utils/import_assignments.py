"""Import przypisań księgowa/kadrowa z tabeli firma x pracownik (kolumny to kody pracowników).

Kod pracownika = 2 pierwsze litery nazwiska + 1. litera imienia (np. Czarnecki Katarzyna -> CZK).
Reguły: 1 osoba -> obie role; CZK jest kadrową, pozostali księgowymi; przy 3+ osobach kolumna PUK
jest ignorowana. Kolumny form prawnych (sp. z o.o. itd.) są pomijane.
"""
from __future__ import annotations

import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from app.db.dao_conn import get_conn
from app.utils.importers import _load_rows_from_file
from app.utils.parsing import normalize_identifier

KADROWA_CODE = "CZK"
IGNORED_WHEN_MANY = "PUK"
_TRUTHY = {"tak", "t", "x", "1", "yes", "y"}
_PL = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")


def employee_code(imie: str, nazwisko: str) -> str:
    def plain(text: str) -> str:
        text = unicodedata.normalize("NFKD", str(text or "").translate(_PL))
        return "".join(ch for ch in text if ch.isalpha())
    return (plain(nazwisko)[:2] + plain(imie)[:1]).upper()


def choose_roles(marked: List[str]) -> Tuple[Optional[str], Optional[str]]:
    """Zwraca (ksiegowa, kadrowa) jako kody z listy zaznaczonych kolumn."""
    codes = list(dict.fromkeys(marked))
    if len(codes) > 2 and IGNORED_WHEN_MANY in codes:
        codes.remove(IGNORED_WHEN_MANY)
    if not codes:
        return None, None
    if len(codes) == 1:
        return codes[0], codes[0]
    kadrowa = KADROWA_CODE if KADROWA_CODE in codes else None
    others = [c for c in codes if c != KADROWA_CODE]
    return (others[0] if others else None), kadrowa


def import_role_assignments_from_file(filepath: str) -> Dict[str, Any]:
    from app.db.dao_companies import _sync_assignees

    warnings: List[str] = []
    try:
        headers, rows = _load_rows_from_file(filepath)
    except Exception as exc:
        return {"updated": 0, "warnings": [f"Plik nie mógł zostać wczytany: {exc}"]}

    conn = get_conn()
    updated = 0
    try:
        by_code: Dict[str, int] = {}
        for emp in conn.execute("SELECT id, imie, nazwisko FROM pracownicy WHERE COALESCE(aktywny, 1) = 1").fetchall():
            by_code.setdefault(employee_code(emp["imie"], emp["nazwisko"]), emp["id"])

        norm = [str(h or "").strip().upper() for h in headers]
        name_idx = next((i for i, h in enumerate(norm) if h in ("NAZWA FIRMY", "NAZWA", "FIRMA")), 0)
        nip_idx = next((i for i, h in enumerate(norm) if h == "NIP"), None)
        code_cols = [(i, h) for i, h in enumerate(norm) if h in by_code]
        for h in norm:
            if len(h) == 3 and h.isalpha() and h not in by_code and h not in ("NIP",):
                warnings.append(f"Kolumna {h}: nie znaleziono pracownika o takim kodzie.")

        for row_number, row in enumerate(rows, start=2):
            def cell(idx: Optional[int]) -> str:
                return str(row[idx] or "").strip() if idx is not None and idx < len(row) else ""

            name, nip = cell(name_idx), normalize_identifier(cell(nip_idx), {10})
            if not (name or nip):
                continue
            marked = [h for i, h in code_cols if cell(i).lower() in _TRUTHY]
            if not marked:
                continue
            firm = None
            if nip:
                firm = conn.execute("SELECT id FROM firmy WHERE nip = %s", (nip,)).fetchone()
            if firm is None and name:
                firm = conn.execute("SELECT id FROM firmy WHERE lower(nazwa) = lower(%s)", (name,)).fetchone()
            if firm is None:
                warnings.append(f"Wiersz {row_number}: nie znaleziono firmy {name or nip}.")
                continue
            ksiegowa, kadrowa = choose_roles(marked)
            if ksiegowa is None or kadrowa is None:
                warnings.append(f"Wiersz {row_number}: {name or nip} – brak jednoznacznej księgowej/kadrowej ({', '.join(marked)}).")
            conn.execute(
                "UPDATE firmy SET ksiegowa_id = COALESCE(%s, ksiegowa_id), kadrowa_id = COALESCE(%s, kadrowa_id) WHERE id = %s",
                (by_code.get(ksiegowa), by_code.get(kadrowa), firm["id"]),
            )
            _sync_assignees(conn, firm["id"])
            updated += 1
        conn.commit()
    finally:
        conn.close()
    return {"updated": updated, "warnings": warnings}
