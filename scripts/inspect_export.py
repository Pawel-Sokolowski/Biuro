"""Pokazuje strukturę pliku CSV/XLSX (nagłówki, liczba wierszy, typy i przykładowe wartości).

Użycie:  .venv\\Scripts\\python.exe scripts\\inspect_export.py "C:\\sciezka\\plik.xlsx"
Domyślnie wartości są maskowane (cyfry -> 9, litery -> a); dodaj --raw, aby zobaczyć prawdziwe.
Wynik zapisuje też do inspect_export.log.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.utils.saldeo_contractors_import import _csv_rows, _xlsx_rows  # noqa: E402


def mask(value) -> str:
    text = "" if value is None else str(value)
    return re.sub(r"[^\W\d_]", "a", re.sub(r"\d", "9", text))[:40]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--raw", action="store_true")
    parser.add_argument("--rows", type=int, default=3)
    args = parser.parse_args()
    path = Path(args.path)
    rows = _csv_rows(path) if path.suffix.lower() == ".csv" else _xlsx_rows(path)
    out = [f"Plik: {path.name}, wierszy (z nagłówkiem): {len(rows)}"]
    for i, row in enumerate(rows[:args.rows + 1]):
        shown = [str(c) if (args.raw or i == 0) else mask(c) for c in row]
        out.append(f"{'NAGŁÓWEK' if i == 0 else f'wiersz {i}'}: {shown}")
    if len(rows) > 1:
        for j, name in enumerate(rows[0]):
            values = {str(r[j]) for r in rows[1:] if j < len(r) and r[j] not in (None, "")}
            if 0 < len(values) <= 8:
                out.append(f"Wartości kolumny {name!r}: {sorted(values)}")
    text = "\n".join(out)
    print(text)
    (ROOT / "inspect_export.log").write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
