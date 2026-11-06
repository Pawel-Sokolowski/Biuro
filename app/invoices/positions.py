"""Obliczenia na pozycjach faktury (wspólne dla edytora, DAO i korekt)."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from app.utils.money import decimal_amount

CENT = Decimal("0.01")

def decimal_value(value, default=0):
    try:
        result = Decimal(str(value).replace("\xa0", "").replace(" ", "").replace(",", "."))
        return result if result.is_finite() else Decimal(str(default))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(str(default))

def money(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def vat_percent(value: Any, default: float = 23.0) -> float:
    """'23%', 23, '23' -> 23.0; wartości nienumeryczne ('zw', 'np') dają 0."""
    if value in (None, ""):
        return default
    text = str(value).replace("%", "").strip().lower()
    if text in ("zw", "np", "np i", "np ii", "oo", "0 kr", "0 wdt", "0 ex"):
        return 0.0
    return float(decimal_value(text, default))


def normalize_position(position: Dict[str, Any], *, preserve_amounts: bool = False) -> Dict[str, Any]:
    """Uzupełnia pozycję: cena jednostkowa, wartość netto i brutto zgodne z ilością i VAT."""
    result = dict(position)
    for field in ('ilosc', 'cena_netto', 'netto', 'brutto'):
        if result.get(field) not in (None, ''):
            decimal_amount(result[field])
    ilosc = decimal_value(result.get("ilosc"), 1)
    vat = vat_percent(result.get("vat", result.get("stawka_vat")))

    if result.get("cena_netto") not in (None, ""):
        cena = decimal_value(result["cena_netto"])
    else:
        cena = decimal_value(result.get("netto")) / ilosc if ilosc else Decimal(0)

    raw_vat = str(result.get("vat", result.get("stawka_vat", ""))).strip().lower()
    if raw_vat in ("zw", "np", "np i", "np ii", "oo", "0 kr", "0 wdt", "0 ex"):
        result["vat_code"] = raw_vat
    elif result.get("vat_code") and vat != 0:
        result.pop("vat_code", None)
    if preserve_amounts and result.get("netto") not in (None, "") and result.get("brutto") not in (None, ""):
        netto = decimal_value(result["netto"])
        brutto = decimal_value(result["brutto"])
    else:
        netto = money(cena * ilosc)
        tax = money(netto * decimal_value(vat) / 100)
        brutto = netto + tax
    result.update({
        "nazwa": result.get("nazwa") or result.get("opis") or "",
        "ilosc": float(ilosc),
        "cena_netto": float(cena),
        "vat": int(vat) if float(vat).is_integer() else vat,
        "netto": float(netto),
        "brutto": float(brutto),
    })
    return result


def totals(positions: Iterable[Dict[str, Any]]) -> Tuple[float, float]:
    positions = list(positions)
    netto = float(money(sum((decimal_value(p.get("netto")) for p in positions), Decimal(0))))
    brutto = float(money(sum((decimal_value(p.get("brutto")) for p in positions), Decimal(0))))
    return netto, brutto


def reverse_positions(positions: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pozycje korekty 'do zera': ujemna ilość, te same ceny."""
    reversed_positions = []
    for position in positions:
        item = normalize_position(position, preserve_amounts=True)
        item["ilosc"] = -item["ilosc"]
        item["netto"] = -item["netto"]
        item["brutto"] = -item["brutto"]
        if item.get("vat_amount") is not None:
            item["vat_amount"] = -float(decimal_value(item["vat_amount"]))
        item["generated"] = False
        reversed_positions.append(item)
    return reversed_positions
