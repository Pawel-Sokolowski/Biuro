# app/companies/cennik_keys.py
"""Klucze cenników księgowych, np. KPIR_T_5_260 = typ_VAT_limit_cena."""
from typing import Any, Dict, Iterable, List, Optional

_TYP_ORDER = {"KPIR": 0, "RYCZALT": 1, "KH": 2}


def _fold(text: Any) -> str:
    return str(text or "").strip().lower().translate(
        str.maketrans({"ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n",
                       "ó": "o", "ś": "s", "ź": "z", "ż": "z"})
    )


def normalize_typ(prefix: str) -> str:
    folded = _fold(prefix)
    if folded.startswith("ryczalt"):
        return "RYCZALT"
    if folded in ("kh", "kr"):
        return "KH"
    return folded.upper()


def parse_cennik_key(klucz: str) -> Dict[str, Any]:
    parts = str(klucz or "").replace("-", "_").split("_")
    typ = normalize_typ(parts[0]) if parts and parts[0] else ""
    vat = parts[1].upper() if len(parts) > 1 and parts[1].upper() in ("T", "N") else None
    rest = parts[2:] if vat else parts[1:]
    limit: Optional[int] = int(rest[0]) if rest and rest[0].isdigit() else None
    price: Optional[float] = None
    price_parts = rest[1:]
    if price_parts and all(p.isdigit() for p in price_parts):
        price = float(".".join(price_parts)) if len(price_parts) > 1 else float(price_parts[0])
    return {"typ": typ, "vat": vat, "limit": limit, "price": price}


def cennik_sort_key(klucz: str):
    info = parse_cennik_key(klucz)
    return (
        _TYP_ORDER.get(info["typ"], 99), info["typ"],
        info["vat"] or "",
        info["limit"] if info["limit"] is not None else 10 ** 9,
        info["price"] if info["price"] is not None else 0.0,
        klucz,
    )


def typ_from_forma(forma_opodatkowania: Any) -> Optional[str]:
    forma = _fold(forma_opodatkowania)
    if "ryczalt" in forma:
        return "RYCZALT"
    if "kpir" in forma or "przychod" in forma:
        return "KPIR"
    if "ksieg" in forma:
        return "KH"
    return None


def vat_flag_from_status(status_vat: Any) -> Optional[str]:
    status = _fold(status_vat)
    if status in ("czynny", "vat czynny"):
        return "T"
    if status in ("zwolniony", "niczynny", "nieczynny"):
        return "N"
    return None


def filter_cennik_keys(keys: Iterable[str], typ: Optional[str], vat_flag: Optional[str]) -> List[str]:
    out = []
    for key in keys:
        info = parse_cennik_key(key)
        if typ and info["typ"] != typ:
            continue
        if vat_flag and info["vat"] and info["vat"] != vat_flag:
            continue
        out.append(key)
    return sorted(out, key=cennik_sort_key)


def find_cennik_key(
    keys: Iterable[str], typ: str, vat_flag: Optional[str],
    limit: Optional[int], price: Optional[float],
) -> Optional[str]:
    """Dokładne dopasowanie typ + VAT + limit + cena; bez ceny tylko gdy jest jednoznaczne."""
    matches = []
    if not vat_flag:
        return None
    for key in filter_cennik_keys(keys, typ, vat_flag):
        info = parse_cennik_key(key)
        if info["vat"] != vat_flag or (limit is not None and info["limit"] != limit):
            continue
        if price:
            if info["price"] is not None and abs(info["price"] - float(price)) < 0.005:
                matches.append(key)
        else:
            matches.append(key)
    return matches[0] if matches and (price or len(matches) == 1) else None
