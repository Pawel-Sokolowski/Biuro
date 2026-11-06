"""Exact money allocation shared by reports and financial boundaries."""
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any, Mapping, TypeVar

Key = TypeVar('Key')

CENT = Decimal('0.01')


def decimal_amount(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value if value is not None else 0).replace(' ', '').replace('\xa0', '').replace(',', '.'))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError('Nieprawidłowa kwota.') from exc
    if not amount.is_finite():
        raise ValueError('Kwota musi być skończoną liczbą.')
    return amount


def money(value: Any) -> Decimal:
    return decimal_amount(value).quantize(CENT, rounding=ROUND_HALF_UP)


def allocate_money(total: Any, weights: Mapping[Key, Any]) -> dict[Key, Decimal]:
    """Largest-remainder allocation; negative corrections retain their sign."""
    if not weights:
        return {}
    amounts = {key: max(decimal_amount(weight), Decimal(0)) for key, weight in weights.items()}
    denominator = sum(amounts.values())
    if not denominator:
        amounts = dict.fromkeys(amounts, Decimal(1))
        denominator = Decimal(len(amounts))
    cents = int(money(total) * 100)
    sign = -1 if cents < 0 else 1
    exact = {key: abs(cents) * weight / denominator for key, weight in amounts.items()}
    allocated = {key: int(value.to_integral_value(rounding=ROUND_FLOOR)) for key, value in exact.items()}
    remaining = abs(cents) - sum(allocated.values())
    order = sorted(amounts, key=lambda key: (-(exact[key] - allocated[key]), str(key)))
    for key in order[:remaining]:
        allocated[key] += 1
    return {key: Decimal(value * sign) / 100 for key, value in allocated.items()}
