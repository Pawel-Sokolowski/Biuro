"""Uzupełnianie importowanych firm danymi z CEIDG/GUS/KRS/Białej Listy oraz sprawdzenie VAT."""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_FILL_KEYS = ("regon", "krs", "konto_bankowe", "kody_pkd", "data_rozpoczecia_dg")
_ADDRESS_PREFIXES = ("dz_", "ko_")


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def enrich_payload(
    payload: Dict[str, Any],
    lookup: Optional[Callable[[str], Dict[str, Any]]] = None,
) -> Optional[str]:
    """Dopełnia puste pola payloadu danymi rejestrów (dane z pliku mają pierwszeństwo,
    status VAT pochodzi z rejestru). Zwraca komunikat błędu lub None."""
    nip = _digits(payload.get("nip"))
    if len(nip) != 10:
        return None
    if lookup is None:
        from app.companies.company_lookup import lookup_company_by_nip as lookup
    try:
        data = lookup(nip) or {}
    except Exception as exc:
        logger.warning("Import: błąd pobierania danych dla NIP %s", nip, exc_info=True)
        return f"NIP {nip}: {exc}"
    if data.get("error"):
        return f"NIP {nip}: {data.get('message')}"

    def put(key: str, value: Any) -> None:
        if value not in (None, "", []) and payload.get(key) in (None, ""):
            payload[key] = value

    for key in _FILL_KEYS:
        put(key, data.get(key))
    pkd = data.get("kody_pkd") or data.get("pkd")
    put("kody_pkd", ", ".join(pkd) if isinstance(pkd, list) else pkd)
    put("data_rozpoczecia_dg", data.get("data_rozpoczecia"))
    for key, value in data.items():
        if key.startswith("koresp_"):
            put("ko_" + key[len("koresp_"):], value)
        elif key.startswith(_ADDRESS_PREFIXES):
            put(key, value)
    if data.get("status_vat") in ("Czynny", "Zwolniony"):
        payload["status_vat"] = data["status_vat"]
        payload["vat_czynny"] = 1 if data["status_vat"] == "Czynny" else 0
    return None


def check_vat_for_company(nip: str, firma_id: int, warnings: List[str], row_number: int) -> None:
    from app.utils.vat_check import check_company_vat

    try:
        result = check_company_vat(firma_id)
    except Exception as exc:
        warnings.append(f"Wiersz {row_number}: nie sprawdzono VAT ({exc}).")
        return
    for err in result.get("errors", []):
        warnings.append(f"Wiersz {row_number}: {err}")


def check_ceidg_status_for_company(firma_id: int, warnings: List[str], row_number: int) -> str:
    """Sprawdza status działalności w CEIDG (zawieszenie przenosi firmę do „Zawieszone”).
    Zwraca krótki opis: zrobione / pominięto / błąd."""
    from app.utils.ceidg_suspension import check_company_ceidg

    try:
        res = check_company_ceidg(firma_id)
    except Exception as exc:
        warnings.append(f"Wiersz {row_number}: nie sprawdzono statusu w CEIDG ({exc}).")
        return "błąd"
    if res.get("error"):
        if "Brak konfiguracji" in res["error"] or "Nie znaleziono" in res["error"]:
            return "pominięto"
        warnings.append(f"Wiersz {row_number}: status CEIDG: {res['error']}")
        return "błąd"
    raw = res.get("raw") or ""
    if raw not in ("AKTYWNY", "ZAWIESZONY", "ZAWIESZONA"):
        warnings.append(f"Wiersz {row_number}: CEIDG zwrócił status {raw} – sprawdź firmę ręcznie.")
    return "zrobione"
