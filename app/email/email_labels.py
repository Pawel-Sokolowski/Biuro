"""Numery i kolory etykiet pracowników na wiadomościach (jak etykiety 1-7 w Thunderbirdzie)."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.db.dao_app_settings import get_setting, set_setting

LABEL_COUNT = 7
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
        return {int(k): int(v) for k, v in data.items() if 1 <= int(v) <= LABEL_COUNT}
    except (ValueError, TypeError):
        return None


def save_label_map(mapping: Dict[int, int]) -> None:
    clean = {str(int(k)): int(v) for k, v in mapping.items() if v and 1 <= int(v) <= LABEL_COUNT}
    numbers = list(clean.values())
    if len(numbers) != len(set(numbers)):
        raise ValueError("Dwóch pracowników nie może mieć tego samego numeru etykiety.")
    set_setting(SETTING_KEY, json.dumps(clean))


def resolve_label_numbers(employees: List[Dict[str, Any]]) -> Dict[int, int]:
    """Numery etykiet pracowników; bez ustawień numerujemy ich po kolei (pierwsi 7)."""
    saved = load_label_map()
    if saved is None:
        return {e["id"]: i for i, e in enumerate(employees[:LABEL_COUNT], 1)}
    ids = {e["id"] for e in employees}
    return {emp_id: n for emp_id, n in saved.items() if emp_id in ids}
