"""Sprawdzanie statusu VAT UE polskiego NIP-u w VIES (Komisja Europejska)."""
from typing import Any, Dict

import requests

VIES_URL = "https://ec.europa.eu/taxation_customs/vies/rest-api/ms/PL/vat/{nip}"
_TIMEOUT = 15


def get_vies_by_nip(nip: str) -> Dict[str, Any]:
    """Zwraca {"valid": bool} albo {"error": True, "message": ...}."""
    nip = "".join(ch for ch in (nip or "") if ch.isdigit())
    if len(nip) != 10:
        return {"error": True, "message": "Nieprawidłowy NIP."}
    try:
        r = requests.get(VIES_URL.format(nip=nip), timeout=_TIMEOUT)
        data = r.json()
    except (requests.RequestException, ValueError) as exc:
        return {"error": True, "message": f"Brak połączenia z VIES: {exc}"}
    if "isValid" in data and str(data.get("userError", "VALID")).upper() in ("VALID", "INVALID"):
        return {"valid": bool(data["isValid"])}
    return {"error": True, "message": f"VIES niedostępny ({data.get('userError') or r.status_code})."}
