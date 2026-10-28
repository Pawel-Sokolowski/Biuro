"""Pobieranie danych rejestrowych zależnie od formy prawnej.

JDG: CEIDG. Spółka cywilna: REGON/GUS + powiązania wspólników w CEIDG.
Pozostałe podmioty wpisane do KRS: GUS -> numer KRS -> odpis KRS.
Biała Lista MF sprawdzana dla KAŻDEJ formy, ale uzupełnia WYŁĄCZNIE VAT i konto.
"""
from typing import Any, Callable, Dict, Optional

from app.companies.biala_lista_api import get_biala_lista_by_nip
from app.companies.ceidg_api import get_ceidg_firma_by_nip
from app.companies.gus_api import get_gus_firma_by_nip
from app.companies.krs_api import get_krs_firma_by_krs


def _ok(data: Optional[Dict[str, Any]]) -> bool:
    return bool(data) and not data.get("error")


def _digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _kind(forma_dzialalnosci: Optional[str]) -> str:
    """Wybór źródła według formy ustawionej w kartotece."""
    if not forma_dzialalnosci:
        return "auto"
    forma = forma_dzialalnosci.strip().casefold()
    if forma == "jdg" or forma.startswith("osoba fizyczna") or "jednoosob" in forma:
        return "jdg"
    if "spółka cywilna" in forma or "spolka cywilna" in forma or "sp. cywilna" in forma:
        return "civil"
    if (
        forma.startswith("spółka") or forma.startswith("spolka")
        or forma.startswith("sp. ") or forma in ("fundacja", "stowarzyszenie")
    ):
        return "krs"
    return "auto"


def _merge(base: Dict[str, Any], wl: Dict[str, Any]) -> Dict[str, Any]:
    """Biała Lista podaje WYŁĄCZNIE status VAT i numer rachunku bankowego."""
    merged = dict(base)
    for key in ("status_vat", "konto_bankowe"):
        if wl.get(key):
            merged[key] = wl[key]
    return merged


def _matches_nip(data: Dict[str, Any], nip: str) -> bool:
    """Rekord nie może zostać przypisany do klienta z innym NIP."""
    returned = _digits(data.get("nip"))
    return not returned or returned == _digits(nip)


def lookup_company_by_nip(
    nip: str,
    wl: Callable = get_biala_lista_by_nip,
    krs: Callable = get_krs_firma_by_krs,
    ceidg: Callable = get_ceidg_firma_by_nip,
    gus: Callable = get_gus_firma_by_nip,
    forma_dzialalnosci: Optional[str] = None,
    civil_partners: Optional[Callable] = None,
) -> Dict[str, Any]:
    nip = _digits(nip)
    if len(nip) != 10:
        return {"error": True, "message": "NIP musi składać się z 10 cyfr."}

    # VAT i numer konta sprawdzane zawsze, również dla spółek cywilnych.
    wl_data = wl(nip) or {}
    kind = _kind(forma_dzialalnosci)

    gus_data: Dict[str, Any] = {}
    diagnostics = []
    if kind in ("civil", "krs", "auto"):
        gus_data = gus(nip) or {}
        if _ok(gus_data) and not _matches_nip(gus_data, nip):
            gus_data = {"error": True, "message": "GUS zwrócił inny NIP niż wyszukiwany."}
        if not _ok(gus_data):
            diagnostics.append("GUS: " + str(gus_data.get("message") or "brak danych rejestrowych"))

    if kind == "jdg":
        registry = ceidg(nip)
    elif kind == "civil":
        registry = gus_data
        if _ok(registry):
            registry = dict(registry)
            # Spółka cywilna ma własny NIP i REGON, lecz nie ma numeru KRS.
            registry.pop("krs", None)
            if civil_partners is None:
                from app.companies.ceidg_api import get_ceidg_civil_partners
                civil_partners = get_ceidg_civil_partners
            try:
                registry["wspolnicy_ceidg"] = civil_partners(nip)
            except Exception:
                registry["wspolnicy_ceidg"] = []
    else:
        if kind == "auto" and (
            not _ok(gus_data) or str(gus_data.get("typ") or "").upper() == "F"
        ):
            # Brak wskazanej formy: preferuj CEIDG dla osoby fizycznej.
            person = ceidg(nip)
            registry = person if _ok(person) and _matches_nip(person, nip) else gus_data
        else:
            registry = gus_data
            reg_krs = _digits(gus_data.get("krs")) if _ok(gus_data) else ""
            if reg_krs:
                krs_data = krs(reg_krs)
                if _ok(krs_data) and _matches_nip(krs_data, nip):
                    # KRS jest źródłem szczegółowych danych, GUS numeru REGON.
                    registry = dict(gus_data)
                    registry.update({key: value for key, value in krs_data.items() if value not in (None, "")})
                    registry["krs"] = reg_krs.zfill(10)
                else:
                    diagnostics.append(
                        "KRS: " + str(krs_data.get("message") or "nie udało się zweryfikować NIP w KRS")
                    )
            elif kind == "krs" and _ok(gus_data):
                registry_info = str(gus_data.get("gus_rodzaj_rejestru") or "").strip()
                registry_number = str(gus_data.get("gus_numer_w_rejestrze") or "").strip()
                details = (
                    f" Rodzaj rejestru z GUS: {registry_info or 'brak'};"
                    f" numer w rejestrze: {registry_number or 'brak'}."
                )
                diagnostics.append(
                    "GUS zwrócił dane firmy, ale nie udało się ustalić numeru KRS z pełnego raportu."
                    + details
                )

    if _ok(registry) and _matches_nip(registry, nip):
        result = dict(registry)
        result["nip"] = nip
        result = _merge(result, wl_data) if _ok(wl_data) else result
        if diagnostics:
            result["_registry_warning"] = "\n".join(diagnostics)
        return result

    # Nie zwracaj nazwy ani adresów z Białej Listy zamiast rejestru.
    # Gdy VAT jest dostępny, zwracamy go WRAZ z ostrzeżeniem o braku danych.
    if _ok(wl_data):
        result = _merge({"nip": nip}, wl_data)
        message = diagnostics or [str((registry or {}).get("message") or
                                      "Nie udało się pobrać danych firmy z właściwego rejestru.")]
        result["_registry_warning"] = "\n".join(message)
        return result
    return registry or {"error": True, "message": "Nie znaleziono firmy w odpowiednim rejestrze."}
