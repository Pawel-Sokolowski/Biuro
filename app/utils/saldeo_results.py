"""Odczyt maili z rozliczeniem miesięcznym z Saldeo (przychód, koszty, VAT, CIT, PIT)."""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional

_MONTHS = {
    "stycz": 1, "lut": 2, "mar": 3, "kwie": 4, "maj": 5, "czerw": 6,
    "lip": 7, "sierp": 8, "wrze": 9, "pa": 10, "listop": 11, "grud": 12,
}
_AMOUNT = r"(-?\d[\d\s\u00a0\u202f]*(?:[.,]\d+)?)\s*PLN"


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in ("td", "th"):
            self.parts.append("\t")
        elif tag in ("tr", "p", "div", "table", "h1", "h2", "h3", "li"):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(source: str) -> str:
    parser = _TextExtractor()
    parser.feed(source)
    return html.unescape("".join(parser.parts))


def _to_number(raw: str) -> float:
    cleaned = re.sub(r"[\s\u00a0\u202f]", "", raw).replace(",", ".")
    return float(cleaned)


def _month_number(name: str) -> Optional[int]:
    low = name.lower().replace("ń", "n").replace("ś", "s").replace("ź", "z").replace("ż", "z")
    low = low.replace("ó", "o").replace("ł", "l").replace("ą", "a").replace("ę", "e").replace("ć", "c")
    for prefix, number in _MONTHS.items():
        if low.startswith(prefix):
            return number
    return None


def _first_amount(line: str) -> Optional[float]:
    match = re.search(_AMOUNT, line, re.IGNORECASE)
    return _to_number(match.group(1)) if match else None


def is_quarterly(period: str) -> bool:
    return "kwarta" in (period or "").lower()


def parse_saldeo_settlement(text: str) -> Optional[Dict[str, Any]]:
    """Zwraca {nazwa, miesiac, przychod, koszty, dochod, vat_kwota, vat_opis, cit_kwota, pit_kwota}
    albo None, gdy to nie jest mail z rozliczeniem."""
    text = text.replace("\r", "")
    lines = [re.sub(r"[ \t\u00a0]+", " ", ln).strip() for ln in text.split("\n")]
    lines = [ln for ln in lines if ln]

    start = next((i for i, ln in enumerate(lines) if re.match(r"rozliczenie za\b", ln, re.IGNORECASE)), None)
    if start is None:
        return None
    header = re.match(r"rozliczenie za\s+(\S+)\s+(\d{4})", lines[start], re.IGNORECASE)
    month = _month_number(header.group(1)) if header else None
    if not header or month is None:
        return None

    result: Dict[str, Any] = {
        "nazwa": lines[start - 1] if start > 0 else "",
        "miesiac": f"{int(header.group(2)):04d}-{month:02d}",
        "przychod": None, "koszty": None, "dochod": None,
        "vat_kwota": None, "vat_opis": "", "cit_kwota": None, "pit_kwota": None,
        "vat_okres": "", "cit_okres": "", "pit_okres": "",
        "vat_termin": "", "cit_termin": "", "pit_termin": "",
    }

    in_taxes = False
    for i in range(start + 1, len(lines)):
        line = lines[i]
        low = line.lower()
        if low.startswith("kwoty podatków") or low.startswith("kwoty podatkow"):
            in_taxes = True
            continue
        if not in_taxes:
            if low.startswith("przychód") or low.startswith("przychod"):
                result["przychod"] = _first_amount(line)
            elif low.startswith("koszty"):
                result["koszty"] = _first_amount(line)
            elif low.startswith("dochód") or low.startswith("dochod"):
                result["dochod"] = _first_amount(line)
            elif low.startswith("różnica podatku vat") or low.startswith("roznica podatku vat"):
                notes = []
                for follow in [line] + lines[i + 1:i + 3]:
                    if follow is not line and (re.match(r"kwoty podatk", follow.lower()) or not re.search(_AMOUNT, follow)):
                        break
                    notes += [f"{_to_number(m.group(1)):,.2f}".replace(",", " ").replace(".", ",") + " PLN " + (m.group(2) or "")
                              for m in re.finditer(_AMOUNT + r"\s*(\([^)]*\))?", follow, re.IGNORECASE)]
                result["vat_opis"] = "; ".join(n.strip() for n in notes)
            continue
        if low.startswith("mikrorachunek"):
            break
        tax = re.match(r"(PIT|CIT|VAT)[\w\-/]*\s+" + _AMOUNT + r"(.*)$", line, re.IGNORECASE)
        if tax:
            kind = tax.group(1).upper()
            key = f"{kind.lower()}_kwota"
            result[key] = (result[key] or 0.0) + _to_number(tax.group(2))
            rest = tax.group(3)
            period = re.search(r"\b(I{1,3}|IV)\s+kwarta[łl]\s+\d{4}|\b[^\W\d_]+\s+\d{4}", rest)
            deadline = re.search(r"\d{2}-\d{2}-\d{4}", rest)
            if period:
                prev = result[f"{kind.lower()}_okres"]
                result[f"{kind.lower()}_okres"] = ", ".join(dict.fromkeys(filter(None, [prev, period.group(0)])))
            if deadline:
                result[f"{kind.lower()}_termin"] = deadline.group(0)

    if all(result[k] is None for k in ("przychod", "koszty", "dochod", "vat_kwota", "cit_kwota", "pit_kwota")):
        return None
    return result
