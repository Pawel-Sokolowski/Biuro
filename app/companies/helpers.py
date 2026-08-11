# app/companies/helpers.py
from typing import Any, Optional

def parse_num(value: Any) -> Optional[float]:
    """
    Parsuje wartość komórki do liczby (int lub float) lub zwraca None jeśli pusta.
    Zastępuje przecinki kropkami i usuwa spacje.
    """
    if value is None:
        return None
    s = str(value).strip()
    if s == "":
        return None
    s = s.replace(" ", "").replace(",", ".")
    try:
        if "." in s:
            return float(s)
        return int(s)
    except Exception:
        # jeśli nie da się sparsować, zwracamy None (możesz zmienić na surowy string)
        return None

def _get_cell(row: list, idx: int):
    """Bezpieczny dostęp do elementu listy/tuple; zwraca None gdy indeks poza zakresem."""
    try:
        return row[idx]
    except Exception:
        return None
