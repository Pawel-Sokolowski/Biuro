import re
from typing import Any, Iterable, List

_NUMERIC = re.compile(r"-?[\d\s.,]+")


def safe_cell(value: Any) -> Any:
    """Neutralizuje tekst zaczynający się od = + - @, aby Excel nie wykonał go jako formuły."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@") and not _NUMERIC.fullmatch(value):
        return "'" + value
    return value


def safe_row(values: Iterable[Any]) -> List[Any]:
    return [safe_cell(v) for v in values]
