"""Ręczne sprawdzenie statusu VAT (Biała Lista MF) i VAT UE (VIES) dla firmy."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, Optional

from app.db.dao_conn import get_conn

VAT_STATUSES = ("czynny", "zwolniony", "nieznany")
_WL_MAP = {"Czynny": "czynny", "Zwolniony": "zwolniony"}


def _employee_name(actor_id: Optional[int]) -> str:
    if not actor_id:
        return ""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT imie, nazwisko FROM pracownicy WHERE id = %s", (actor_id,)
        ).fetchone()
    finally:
        conn.close()
    return f"{row[0]} {row[1]}".strip() if row else ""


def check_company_vat(
    firma_id: int,
    actor_id: Optional[int] = None,
    wl_fetch: Optional[Callable[[str], dict]] = None,
    vies_fetch: Optional[Callable[[str], dict]] = None,
) -> Dict[str, Any]:
    """Odpytuje obie usługi i zapisuje wyniki. Błąd usługi nie nadpisuje poprzedniego
    statusu. Zwraca {"vat": status|None, "vat_ue": status|None, "errors": [...]}."""
    if wl_fetch is None:
        from app.companies.biala_lista_api import get_biala_lista_by_nip as wl_fetch
    if vies_fetch is None:
        from app.companies.vies_api import get_vies_by_nip as vies_fetch

    conn = get_conn()
    try:
        row = conn.execute("SELECT nip FROM firmy WHERE id = %s", (firma_id,)).fetchone()
    finally:
        conn.close()
    nip = "".join(ch for ch in str(row[0] if row else "") if ch.isdigit())
    result: Dict[str, Any] = {"vat": None, "vat_ue": None, "errors": []}
    if len(nip) != 10:
        result["errors"].append("Firma nie ma poprawnego NIP-u.")
        return result

    wl = wl_fetch(nip)
    if wl.get("error"):
        result["errors"].append(f"VAT: {wl.get('message')}")
    else:
        result["vat"] = _WL_MAP.get(wl.get("status_vat"), "nieznany")
    vies = vies_fetch(nip)
    if vies.get("error"):
        result["errors"].append(f"VAT UE: {vies.get('message')}")
    else:
        result["vat_ue"] = "czynny" if vies.get("valid") else "zwolniony"

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    who = _employee_name(actor_id)
    conn = get_conn()
    try:
        for prefix in ("vat", "vat_ue"):
            if result[prefix] is not None:
                conn.execute(
                    f"UPDATE firmy SET {prefix}_status = %s, {prefix}_sprawdzono = %s, "
                    f"{prefix}_sprawdzil = %s WHERE id = %s",
                    (result[prefix], now, who, firma_id),
                )
        conn.commit()
    finally:
        conn.close()
    return result


def vat_tooltip(label: str, status: Optional[str], checked_at: Optional[str], who: Optional[str]) -> str:
    names = {"czynny": "czynny", "zwolniony": "nieaktywny", "nieznany": "nieznany status"}
    if not status:
        return f"{label}: nie sprawdzono"
    text = f"{label} {names.get(status, status)}"
    if checked_at:
        try:
            stamp = datetime.strptime(str(checked_at)[:19], "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y %H:%M")
        except ValueError:
            stamp = str(checked_at)
        text += f"\n{stamp}" + (f", {who}" if who else "")
    return text
