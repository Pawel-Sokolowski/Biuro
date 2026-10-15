"""Obliczenia na pozycjach faktury (wspólne dla edytora, DAO i korekt)."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

from app.utils.parsing import to_float


def vat_percent(value: Any, default: float = 23.0) -> float:
    """'23%', 23, '23' -> 23.0; wartości nienumeryczne ('zw', 'np') dają 0."""
    if value in (None, ""):
        return default
    text = str(value).replace("%", "").strip().lower()
    if text in ("zw", "np", "oo"):
        return 0.0
    return to_float(text, default)


def normalize_position(position: Dict[str, Any]) -> Dict[str, Any]:
    """Uzupełnia pozycję: cena jednostkowa, wartość netto i brutto zgodne z ilością i VAT."""
    result = dict(position)
    ilosc = to_float(result.get("ilosc"), 1.0)
    vat = vat_percent(result.get("vat", result.get("stawka_vat")))

    if result.get("cena_netto") not in (None, ""):
        cena = to_float(result["cena_netto"])
    else:
        cena = to_float(result.get("netto")) / ilosc if ilosc else 0.0

    netto = round(cena * ilosc, 2)
    result.update({
        "nazwa": result.get("nazwa") or result.get("opis") or "",
        "ilosc": ilosc,
        "cena_netto": round(cena, 2),
        "vat": int(vat) if float(vat).is_integer() else vat,
        "netto": netto,
        "brutto": round(netto * (1 + vat / 100), 2),
    })
    return result


def totals(positions: Iterable[Dict[str, Any]]) -> Tuple[float, float]:
    netto = round(sum(to_float(p.get("netto")) for p in positions), 2)
    brutto = round(sum(to_float(p.get("brutto")) for p in positions), 2)
    return netto, brutto


def reverse_positions(positions: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pozycje korekty 'do zera': ujemna ilość, te same ceny."""
    reversed_positions = []
    for position in positions:
        item = normalize_position(position)
        item["ilosc"] = -abs(item["ilosc"])
        item["generated"] = False
        reversed_positions.append(normalize_position(item))
    return reversed_positions
