"""Klient Białej Listy podatników VAT (MF) - status VAT, KRS i rachunki po NIP.

Publiczne API, klucz nie jest wymagany.
"""
import re
from datetime import date
from typing import Any, Dict

import requests

from app.companies.address_parsing import split_street_address

WL_URL = "https://wl-api.mf.gov.pl/api/search/nip/{nip}"
_TIMEOUT = 15

_STATUS_VAT = {"czynny": "Czynny", "zwolniony": "Zwolniony"}


def _split_address(text: str) -> Dict[str, str]:
    """'FRYDERYKA CHOPINA 35, 71-450 SZCZECIN' -> ulica / kod / miasto."""
    street, _, rest = (text or "").partition(",")
    match = re.match(r"\s*(\d{2}-\d{3})\s+(.+)", rest)
    ulica, budynek, lokal = split_street_address(street.strip().title())
    return {
        "dz_ulica": ulica,
        "dz_budynek": budynek,
        "dz_lokal": lokal,
        "dz_kod": match.group(1) if match else "",
        "dz_miasto": match.group(2).strip().title() if match else "",
    }


def parse_subject(subject: Dict[str, Any]) -> Dict[str, Any]:
    status = _STATUS_VAT.get(str(subject.get("statusVat") or "").strip().lower(), "Brak danych")
    accounts = subject.get("accountNumbers") or []
    out = {
        "nazwa": subject.get("name") or "",
        "nip": subject.get("nip") or "",
        "regon": subject.get("regon") or "",
        "krs": subject.get("krs") or "",
        "status_vat": status,
        "konto_bankowe": accounts[0] if accounts else "",
    }
    out.update(_split_address(subject.get("workingAddress") or subject.get("residenceAddress") or ""))
    return out


def get_biala_lista_by_nip(nip: str) -> Dict[str, Any]:
    """Zwraca dane podmiotu z Białej Listy albo {"error": True, "message": ...}."""
    nip = "".join(ch for ch in (nip or "") if ch.isdigit())
    if not nip:
        return {"error": True, "message": "Podaj NIP."}
    try:
        r = requests.get(
            WL_URL.format(nip=nip),
            params={"date": date.today().isoformat()},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        return {"error": True, "message": f"Brak połączenia z Białą Listą MF: {exc}"}
    if r.status_code == 400:
        return {"error": True, "message": "Nieprawidłowy NIP."}
    if r.status_code != 200:
        return {"error": True, "message": f"Biała Lista MF zwróciła błąd HTTP {r.status_code}."}
    try:
        subject = ((r.json() or {}).get("result") or {}).get("subject")
    except ValueError:
        return {"error": True, "message": "Nieprawidłowa odpowiedź Białej Listy MF."}
    if not subject:
        return {"error": True, "not_found": True, "message": "Nie znaleziono podmiotu na Białej Liście MF."}
    return parse_subject(subject)
