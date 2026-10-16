"""Klient API KRS (odpis aktualny) - dane spółki po numerze KRS.

Publiczne API Ministerstwa Sprawiedliwości, klucz nie jest wymagany.
"""
import re
from typing import Any, Dict

import requests

KRS_URL = "https://api-krs.ms.gov.pl/api/krs/OdpisAktualny/{krs}"
_TIMEOUT = 15


def _pkd(item: Dict[str, Any]) -> str:
    dzial = item.get("kodDzial") or ""
    klasa = item.get("kodKlasa") or ""
    podklasa = item.get("kodPodklasa") or ""
    return f"{dzial}{klasa}{podklasa}"


def parse_odpis(data: Dict[str, Any]) -> Dict[str, Any]:
    """Zamienia odpis aktualny KRS na słownik pól formularza firmy."""
    dzial1 = ((data.get("odpis") or {}).get("dane") or {}).get("dzial1") or {}
    dzial3 = ((data.get("odpis") or {}).get("dane") or {}).get("dzial3") or {}
    podmiot = dzial1.get("danePodmiotu") or {}
    ident = podmiot.get("identyfikatory") or {}
    siedziba = dzial1.get("siedziba") or {}
    adres = (dzial1.get("siedzibaIAdres") or {}).get("adres") or {}

    kod = adres.get("kodPocztowy") or ""
    if re.fullmatch(r"\d{5}", kod):
        kod = f"{kod[:2]}-{kod[2:]}"
    budynek = adres.get("nrDomu") or ""
    lokal = adres.get("nrLokalu") or ""
    ulica = " ".join(p for p in (adres.get("ulica") or "", budynek) if p)
    if lokal:
        ulica += f"/{lokal}"

    pkd = []
    przedmiot = dzial3.get("przedmiotDzialalnosci") or {}
    for key in ("przedmiotPrzewazajacejDzialalnosci", "przedmiotPozostalejDzialalnosci"):
        for item in przedmiot.get(key) or []:
            code = _pkd(item)
            if code and code not in pkd:
                pkd.append(code)

    return {
        "nazwa": podmiot.get("nazwa") or "",
        "nip": ident.get("nip") or "",
        "regon": ident.get("regon") or "",
        "krs": (data.get("odpis") or {}).get("naglowekA", {}).get("numerKRS") or "",
        "pkd": pkd,
        "dz_miasto": adres.get("miejscowosc") or siedziba.get("miejscowosc") or "",
        "dz_kod": kod,
        "dz_ulica": ulica,
        "dz_budynek": budynek,
        "dz_lokal": lokal,
        "dz_wojewodztwo": siedziba.get("wojewodztwo") or "",
        "dz_powiat": siedziba.get("powiat") or "",
        "dz_gmina": siedziba.get("gmina") or "",
        "dz_kraj": adres.get("kraj") or siedziba.get("kraj") or "Polska",
        "email": dzial1.get("siedzibaIAdres", {}).get("adresPocztyElektronicznej") or "",
        "www": dzial1.get("siedzibaIAdres", {}).get("adresStronyInternetowej") or "",
    }


def get_krs_firma_by_krs(krs: str) -> Dict[str, Any]:
    """Zwraca dane podmiotu z KRS albo {"error": True, "message": ...}."""
    krs = "".join(ch for ch in (krs or "") if ch.isdigit())
    if not krs:
        return {"error": True, "message": "Podaj numer KRS."}
    krs = krs.zfill(10)

    for rejestr in ("P", "S"):
        try:
            r = requests.get(
                KRS_URL.format(krs=krs),
                params={"rejestr": rejestr, "format": "json"},
                timeout=_TIMEOUT,
            )
        except requests.RequestException as exc:
            return {"error": True, "message": f"Brak połączenia z KRS: {exc}"}
        if r.status_code == 404:
            continue
        if r.status_code != 200:
            return {"error": True, "message": f"Usługa KRS zwróciła błąd HTTP {r.status_code}."}
        try:
            return parse_odpis(r.json())
        except ValueError:
            return {"error": True, "message": "Nieprawidłowa odpowiedź usługi KRS."}

    return {"error": True, "message": "Nie znaleziono podmiotu o podanym numerze KRS."}
