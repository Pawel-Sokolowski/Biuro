"""Numery i kolory etykiet pracowników na wiadomościach (jak etykiety 1-7 w Thunderbirdzie)."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.db.dao_app_settings import get_setting, set_setting

LABEL_COUNT = 7
EMPLOYEE_LABEL_NUMBERS = (1, 2, 4, 5, 6, 7)
COMPLETED_LABEL_NUMBER = 3
SETTING_KEY = "email_etykiety_pracownikow"
# kolory kolejnych etykiet z Thunderbirda: czerwony, zielony, niebieski, morski, różowy, pomarańczowy, bordowy
LABEL_COLORS = {
    1: "#ff0000",
    2: "#009900",
    3: "#3333ff",
    4: "#408080",
    5: "#ff00ff",
    6: "#ff8000",
    7: "#800040",
}
NO_LABEL_COLOR = "#666666"


def label_color(number: Optional[int]) -> str:
    return LABEL_COLORS.get(number or 0, NO_LABEL_COLOR)


def load_label_map() -> Optional[Dict[int, int]]:
    """{id pracownika: numer etykiety} albo None, gdy nic nie ustawiono."""
    raw = get_setting(SETTING_KEY)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return {
            int(k): int(v)
            for k, v in data.items()
            if int(v) in EMPLOYEE_LABEL_NUMBERS
        }
    except (ValueError, TypeError):
        return None


def save_label_map(mapping: Dict[int, int]) -> None:
    if any(v and int(v) not in EMPLOYEE_LABEL_NUMBERS for v in mapping.values()):
        raise ValueError("Klawisz 3 jest zarezerwowany dla etykiety „Zrobione”.")
    clean = {
        str(int(k)): int(v)
        for k, v in mapping.items()
        if v and int(v) in EMPLOYEE_LABEL_NUMBERS
    }
    numbers = list(clean.values())
    if len(numbers) != len(set(numbers)):
        raise ValueError("Dwóch pracowników nie może mieć tego samego numeru etykiety.")
    set_setting(SETTING_KEY, json.dumps(clean))


def resolve_label_numbers(employees: List[Dict[str, Any]]) -> Dict[int, int]:
    """Numery etykiet pracowników; klawisz 3 jest zarezerwowany dla statusu „Zrobione”."""
    saved = load_label_map()
    if saved is None:
        return {
            e["id"]: number
            for e, number in zip(employees, EMPLOYEE_LABEL_NUMBERS)
        }
    ids = {e["id"] for e in employees}
    return {emp_id: n for emp_id, n in saved.items() if emp_id in ids}
