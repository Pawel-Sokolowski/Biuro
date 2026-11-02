from datetime import datetime
from typing import Dict, List, Optional

from app.db.dao_conn import get_conn

# Spółki płacące CIT (gdy forma opodatkowania nie wskazuje deklaracji)
CIT_LEGAL_FORMS = ("z o.o.", "akcyjna", "komandytowa", "fundacja", "stowarzyszenie")


def inferred_main_declaration(company: dict) -> str:
    form = (company.get("forma_opodatkowania") or "").strip().lower()
    legal = (company.get("forma_dzialalnosci") or "").strip().lower()
    if "estoń" in form or "estonsk" in form:
        return "CIT-8E"
    if "ryczałt" in form:
        return "PIT-28"
    if "liniow" in form:
        return "PIT-36L"
    if "skala" in form or "ogólne" in form or "progresywn" in form:
        return "PIT-36"
    if "księgi" in form or "cit" in form:
        return "CIT-8"
    if any(key in legal for key in CIT_LEGAL_FORMS):
        return "CIT-8"
    if "ryczałt" in legal:
        return "PIT-28"
    return ""


def company_declarations(company: dict) -> List[str]:
    """Deklaracje roczne firmy: podstawowa (pole firmy lub z formy opodatkowania) + dodatkowe."""
    codes = []
    main = (company.get("rozliczenie_roczne") or "").strip() or inferred_main_declaration(company)
    if main:
        codes.append(main)
    for part in (company.get("deklaracje_dodatkowe") or "").replace(";", ",").split(","):
        code = part.strip().upper()
        if code and code not in codes:
            codes.append(code)
    return codes


def list_year_end(year: int) -> List[Dict]:
    """Firmy aktywne z deklaracjami roku i ich statusem."""
    conn = get_conn()
    try:
        companies = conn.execute(
            """
            SELECT id, nazwa, nazwa_skrocona, nip, forma_opodatkowania, forma_dzialalnosci,
                   rozliczenie_roczne, deklaracje_dodatkowe
            FROM firmy
            WHERE COALESCE(status, 'Aktywne') = 'Aktywne'
            ORDER BY COALESCE(NULLIF(nazwa_skrocona, ''), nazwa)
            """
        ).fetchall()
        saved = {}
        for row in conn.execute("SELECT * FROM zakonczenie_roku WHERE rok = %s", (year,)).fetchall():
            saved[(row["firma_id"], row["deklaracja"])] = dict(row)
    finally:
        conn.close()
    result = []
    for company in companies:
        company = dict(company)
        declarations = []
        for code in company_declarations(company):
            declarations.append({"kod": code, **saved.get((company["id"], code), {})})
        company["deklaracje"] = declarations
        result.append(company)
    return result


def set_company_declarations(firma_id: int, main: str, extra: str) -> None:
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE firmy SET rozliczenie_roczne = %s, deklaracje_dodatkowe = %s WHERE id = %s",
            (main or "", extra or "", firma_id),
        )
        conn.commit()
    finally:
        conn.close()


def save_declaration(actor_id: int, firma_id: int, year: int, code: str, *,
                     filed: bool, filed_date: Optional[str] = None,
                     amount: Optional[float] = None, upo: str = "", notes: str = "") -> None:
    conn = get_conn()
    try:
        conn.execute(
            """
            INSERT INTO zakonczenie_roku
                (firma_id, rok, deklaracja, zlozona, data_zlozenia, kwota, upo, uwagi, updated_by, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (firma_id, rok, deklaracja) DO UPDATE SET
                zlozona = EXCLUDED.zlozona, data_zlozenia = EXCLUDED.data_zlozenia,
                kwota = EXCLUDED.kwota, upo = EXCLUDED.upo, uwagi = EXCLUDED.uwagi,
                updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at
            """,
            (firma_id, year, code, 1 if filed else 0, filed_date or None, amount,
             upo or "", notes or "", actor_id, datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
    finally:
        conn.close()
