"""Pobieranie danych firmy po NIP z kilku źródeł.

Spółka (jest numer KRS):  NIP -> Biała Lista MF -> KRS -> API KRS
JDG (brak KRS):           NIP -> CEIDG (zapasowo GUS) -> Biała Lista MF
"""
from typing import Any, Callable, Dict, Optional

from app.companies.biala_lista_api import get_biala_lista_by_nip
from app.companies.ceidg_api import get_ceidg_firma_by_nip
from app.companies.gus_api import get_gus_firma_by_nip
from app.companies.krs_api import get_krs_firma_by_krs


def _ok(data: Optional[Dict[str, Any]]) -> bool:
    return bool(data) and not data.get("error")


def _merge(base: Dict[str, Any], wl: Dict[str, Any]) -> Dict[str, Any]:
    """Dane rejestru mają pierwszeństwo, Biała Lista dopełnia braki i daje status VAT."""
    merged = dict(base)
    for key, value in wl.items():
        if value and (key in ("status_vat", "konto_bankowe") or not merged.get(key)):
            merged[key] = value
    return merged


def lookup_company_by_nip(
    nip: str,
    wl: Callable = get_biala_lista_by_nip,
    krs: Callable = get_krs_firma_by_krs,
    ceidg: Callable = get_ceidg_firma_by_nip,
    gus: Callable = get_gus_firma_by_nip,
) -> Dict[str, Any]:
    wl_data = wl(nip)

    if _ok(wl_data) and wl_data.get("krs"):
        krs_data = krs(wl_data["krs"])
        if _ok(krs_data):
            return _merge(krs_data, wl_data)
        return wl_data

    registry = ceidg(nip)
    if not _ok(registry):
        gus_data = gus(nip)
        registry = gus_data if _ok(gus_data) else registry
    if _ok(registry):
        return _merge(registry, wl_data) if _ok(wl_data) else registry
    if _ok(wl_data):
        return wl_data
    return registry if registry and registry.get("error") else wl_data
