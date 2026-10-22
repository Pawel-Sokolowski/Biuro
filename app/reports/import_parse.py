# app/reports/import_parse.py
"""Parsowanie i klasyfikacja danych importowanych do raportów (bez bazy danych)."""
import datetime
import hashlib
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional

MIN_YEAR = 2026
KADRY_EMPLOYEE_MARKER = "czajkowska"

DZIALY = ("ksiegowosc", "inne_ksiegowosc", "kadry", "inne_kadry")
DZIAL_LABELS = {
    "ksiegowosc": "Księgowość",
    "inne_ksiegowosc": "Dodatkowe usługi księgowości",
    "kadry": "Kadry",
    "inne_kadry": "Dodatkowe usługi kadr",
}
RODZAJE_FAKTUR = ("ksiegowosc", "kadry", "zdrowotna", "ponad_limit", "rabat", "inne")


def fold(text: Any) -> str:
    s = unicodedata.normalize("NFKD", str(text or "")).replace("ł", "l").replace("Ł", "L")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


def digits(text: Any) -> str:
    return re.sub(r"\D", "", str(text or ""))


def row_hash(*values: Any) -> str:
    return hashlib.sha1("|".join(str(v) for v in values).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------
# wartości z komórek
# ------------------------------------------------------------------
def parse_date(value: Any) -> Optional[str]:
    if isinstance(value, datetime.datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, datetime.date):
        return value.strftime("%Y-%m-%d")
    text = str(value or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse_clock(value: Any) -> str:
    if isinstance(value, (datetime.datetime, datetime.time)):
        return value.strftime("%H:%M")
    return str(value or "").strip()[:5]


def parse_seconds(hms: Any, minutes: Any = None) -> int:
    """Czas z komórki h:m:s (tekst/time/timedelta/ułamek doby); gdy brak - z minut."""
    if isinstance(hms, datetime.timedelta):
        return int(hms.total_seconds())
    if isinstance(hms, datetime.datetime):
        return hms.hour * 3600 + hms.minute * 60 + hms.second
    if isinstance(hms, datetime.time):
        return hms.hour * 3600 + hms.minute * 60 + hms.second
    if isinstance(hms, (int, float)) and not isinstance(hms, bool):
        return int(round(float(hms) * 86400)) if float(hms) < 1 else int(round(float(hms) * 60))
    text = str(hms or "").strip()
    m = re.fullmatch(r"(\d+):(\d{1,2})(?::(\d{1,2}))?", text)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3) or 0)
    return parse_number_seconds(minutes)


def parse_number_seconds(minutes: Any) -> int:
    try:
        return int(round(float(str(minutes).replace(",", ".")) * 60))
    except (TypeError, ValueError):
        return 0


def parse_amount(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = re.sub(r"[^\d,.\-]", "", str(value or "")).replace(",", ".")
    if text.count(".") > 1:
        head, _, tail = text.rpartition(".")
        text = head.replace(".", "") + "." + tail
    try:
        return float(text)
    except ValueError:
        return 0.0


# ------------------------------------------------------------------
# klasyfikacja
# ------------------------------------------------------------------
def is_kadry_employee(name: Any) -> bool:
    return KADRY_EMPLOYEE_MARKER in fold(name)


def classify_work(firma: Any, czynnosc: Any, pracownik: Any) -> str:
    """Dział czasu pracy: ksiegowosc / kadry / inne_kadry."""
    text = f"{fold(firma)} {fold(czynnosc)}"
    kadrowa = is_kadry_employee(pracownik)
    if re.search(r"\bkadr|\bplac|\bzus\b|\bpayroll", text):
        return "kadry"
    if kadrowa and re.search(r"\brozm", text):
        return "kadry"
    if re.search(r"\binn", text):
        return "inne_kadry" if kadrowa else "inne_ksiegowosc"
    return "ksiegowosc"


def classify_invoice_item(pozycja: Any) -> str:
    t = fold(pozycja)
    if "rabat" in t:
        return "rabat"
    if "zdrowotn" in t:
        return "zdrowotna"
    if "ponad limit" in t:
        return "ponad_limit"
    if "kadrow" in t or "place" in t or re.search(r"\b(uop|uz|uod)\b", t):
        return "kadry"
    if "ksiegow" in t:
        return "ksiegowosc"
    return "inne"


def kadry_contract_type(pozycja: Any) -> Optional[str]:
    """'uop' / 'uz' / 'uod' dla pozycji typu 'Us?uga kadrowo-p?acowa - UZ'; None dla sta?ej stawki bez typu."""
    t = fold(pozycja)
    if not ("kadrow" in t or "place" in t):
        return None
    found = re.findall(r"\b(uop|uz|uod)\b", t)
    return found[-1] if found else None


def invoice_item_dzial(rodzaj: str) -> str:
    """Do którego działu liczymy przychód z pozycji faktury."""
    if rodzaj in ("kadry", "zdrowotna"):
        return "kadry"
    return "inne_ksiegowosc" if rodzaj == "inne" else "ksiegowosc"


def month_from_invoice_number(number: Any) -> Optional[str]:
    text = str(number or "")
    m = re.search(r"(?<!\d)(0?[1-9]|1[0-2])[/.\-](20\d{2})(?!\d)", text)
    if m:
        return f"{m.group(2)}-{int(m.group(1)):02d}"
    m = re.search(r"(?<!\d)(20\d{2})[/.\-](0?[1-9]|1[0-2])(?!\d)", text)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    return None


# ------------------------------------------------------------------
# odczyt arkuszy
# ------------------------------------------------------------------
def _header_map(header: Iterable[Any]) -> Dict[str, int]:
    return {fold(h): i for i, h in enumerate(header) if h is not None}


def _find(cols: Dict[str, int], *needles: str) -> Optional[int]:
    for key, idx in cols.items():
        if all(n in key for n in needles):
            return idx
    return None


def _cell(row, idx):
    return row[idx] if idx is not None and idx < len(row) else None


def parse_work_rows(rows: List[tuple]) -> List[Dict[str, Any]]:
    """rows: pierwszy wiersz = nagłówek. Zwraca tylko wiersze od MIN_YEAR."""
    if not rows:
        return []
    cols = _header_map(rows[0])
    i_firma = _find(cols, "firma")
    i_nip = _find(cols, "nip")
    i_user = _find(cols, "uzytkownik")
    i_mdok = _find(cols, "miesiac")
    i_data = cols.get("data")
    i_od, i_do = cols.get("od"), cols.get("do")
    i_hms = _find(cols, "czas pracy", "h:m")
    i_min = _find(cols, "czas pracy", "min")
    i_phms = _find(cols, "czas pauzy", "h:m")
    i_pmin = _find(cols, "czas pauzy", "min")
    i_czyn = _find(cols, "czynnosc")
    i_kom = _find(cols, "komentarz")
    if i_user is None or i_data is None:
        raise ValueError("Brak kolumn „Użytkownik” i „Data” w pliku.")

    out = []
    for row in rows[1:]:
        data = parse_date(_cell(row, i_data))
        if not data or int(data[:4]) < MIN_YEAR:
            continue
        pracownik = str(_cell(row, i_user) or "").strip()
        firma = str(_cell(row, i_firma) or "").strip()
        czynnosc = str(_cell(row, i_czyn) or "").strip()
        sekundy = parse_seconds(_cell(row, i_hms), _cell(row, i_min))
        pauza = parse_seconds(_cell(row, i_phms), _cell(row, i_pmin))
        od, do = parse_clock(_cell(row, i_od)), parse_clock(_cell(row, i_do))
        mdok = str(_cell(row, i_mdok) or "").strip()[:7]
        miesiac = mdok if re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", mdok) else data[:7]
        out.append({
            "firma_nazwa": firma,
            "nip": digits(_cell(row, i_nip)),
            "pracownik_nazwa": pracownik,
            "miesiac_dok": mdok,
            "data": data,
            "miesiac": miesiac,
            "od": od,
            "do": do,
            "sekundy": sekundy,
            "pauza_sekundy": pauza,
            "czynnosc": czynnosc,
            "komentarz": str(_cell(row, i_kom) or "").strip(),
            "dzial": classify_work(firma, czynnosc, pracownik),
            "hash": row_hash("czas", firma, pracownik, data, od, do, sekundy, czynnosc),
        })
    return out


def parse_invoice_rows(rows: List[tuple], default_month: Optional[str] = None) -> List[Dict[str, Any]]:
    if not rows:
        return []
    cols = _header_map(rows[0])
    i_kontr = _find(cols, "nazwa", "kontrahenta")
    i_nip = _find(cols, "nip")
    i_nr = _find(cols, "nr", "faktury") or _find(cols, "numer", "faktury")
    i_poz = _find(cols, "pozycja", "faktury") or _find(cols, "pozycja")
    i_idx = _find(cols, "indeks")
    i_net = _find(cols, "wartosc", "netto")
    i_wal = _find(cols, "waluta")
    i_mies = cols.get("miesiac")
    if i_poz is None or i_net is None:
        raise ValueError("Brak kolumn „Pozycja faktury” i „Wartość netto” w pliku.")

    out = []
    for row in rows[1:]:
        pozycja = str(_cell(row, i_poz) or "").strip()
        if not pozycja:
            continue
        nr = str(_cell(row, i_nr) or "").strip()
        miesiac = (
            (parse_date(f"{_cell(row, i_mies)}-01") or "")[:7] if i_mies is not None and _cell(row, i_mies) else ""
        ) or month_from_invoice_number(nr) or default_month
        if not miesiac or int(miesiac[:4]) < MIN_YEAR:
            continue
        netto = parse_amount(_cell(row, i_net))
        kontrahent = str(_cell(row, i_kontr) or "").strip()
        out.append({
            "kontrahent": kontrahent,
            "nip": digits(_cell(row, i_nip)),
            "nr_faktury": nr,
            "pozycja": pozycja,
            "indeks": str(_cell(row, i_idx) or "").strip(),
            "wartosc_netto": netto,
            "waluta": str(_cell(row, i_wal) or "PLN").strip() or "PLN",
            "miesiac": miesiac,
            "rodzaj": classify_invoice_item(pozycja),
            "hash": row_hash("faktura", kontrahent, nr, pozycja, netto, miesiac),
        })
    return out


# ------------------------------------------------------------------
# rentowność: czas × stawka vs cena usługi
# ------------------------------------------------------------------
def compute_profitability(
    work: List[Dict[str, Any]],
    invoices: List[Dict[str, Any]],
    rates: Dict[str, float],
) -> List[Dict[str, Any]]:
    """Wiersze (firma, miesiąc, dział): czas, przychód, koszt pracy, zysk.

    work: pola firma_id, firma_nazwa, miesiac, pracownik_nazwa, dzial, sekundy
    invoices: pola firma_id, kontrahent, miesiac, rodzaj, wartosc_netto
    rates: stawka godzinowa po fold(imię nazwisko)
    """
    acc: Dict[tuple, Dict[str, Any]] = {}

    def slot(firma_id, nazwa, miesiac, dzial):
        key = (firma_id if firma_id else fold(nazwa), miesiac, dzial)
        return acc.setdefault(key, {
            "firma_id": firma_id, "klient": nazwa, "miesiac": miesiac, "dzial": dzial,
            "sekundy": 0, "przychod": 0.0, "koszt": 0.0,
        })

    for w in work:
        if not w.get("firma_id"):
            continue
        item = slot(w["firma_id"], w.get("firma_nazwa"), w["miesiac"], w["dzial"])
        sek = int(w.get("sekundy") or 0)
        item["sekundy"] += sek
        item["koszt"] += sek / 3600 * float(rates.get(fold(w.get("pracownik_nazwa")), 0) or 0)

    for inv in invoices:
        if not inv.get("firma_id"):
            continue
        item = slot(inv["firma_id"], inv.get("kontrahent"), inv["miesiac"], invoice_item_dzial(inv["rodzaj"]))
        item["przychod"] += float(inv.get("wartosc_netto") or 0)

    names = {}
    for item in acc.values():
        if item["klient"]:
            names.setdefault(item["firma_id"], item["klient"])
    result = []
    for item in acc.values():
        item["klient"] = names.get(item["firma_id"], item["klient"])
        item["zysk"] = item["przychod"] - item["koszt"]
        hours = item["sekundy"] / 3600
        item["przychod_na_godzine"] = item["przychod"] / hours if hours else 0.0
        item["koszt"] = round(item["koszt"], 2)
        item["przychod"] = round(item["przychod"], 2)
        item["zysk"] = round(item["zysk"], 2)
        result.append(item)
    result.sort(key=lambda r: (fold(r["klient"]), r["miesiac"], r["dzial"]))
    return result


SEKCJE = ("ksiegowosc", "kadry")
SEKCJA_LABELS = {"ksiegowosc": "Księgowość", "kadry": "Kadry"}
NO_EMPLOYEE = "— (brak czasu pracy)"


def dzial_sekcja(dzial: str) -> str:
    return "kadry" if dzial in ("kadry", "inne_kadry") else "ksiegowosc"


def _year_months(year: str) -> List[str]:
    return [f"{year}-{m:02d}" for m in range(1, 13)]


MAX_PREPAID_INVOICES = 3


def spread_annual_fees(invoices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Jedna duża opłata księgowa w roku (np. w styczniu), a w pozostałych miesiącach
    tylko inne pozycje -> kwota jest rozbijana równo na 12 miesięcy roku."""
    by_firm_year: Dict[tuple, List[Dict[str, Any]]] = {}
    last_month_of_year: Dict[str, str] = {}
    for inv in invoices:
        year = inv["miesiac"][:4]
        last_month_of_year[year] = max(last_month_of_year.get(year, ""), inv["miesiac"])
        if inv.get("firma_id"):
            by_firm_year.setdefault((inv["firma_id"], year), []).append(inv)

    result = []
    for (firma_id, year), items in by_firm_year.items():
        all_months = {i["miesiac"] for i in items}
        spread = []
        for rodzaj in ("ksiegowosc", "kadry"):
            fee_items = [i for i in items if i["rodzaj"] == rodzaj and float(i.get("wartosc_netto") or 0) > 0]
            fee_months = sorted({i["miesiac"] for i in fee_items})
            if not fee_months or len(fee_months) > MAX_PREPAID_INVOICES:
                continue
            # jedna opłata, a firma ma pozycje w innych miesiącach, albo opłaty tylko
            # na początku roku (styczeń/luty), choć import sięga później (płatność z góry)
            single_with_other_months = len(fee_months) == 1 and bool(all_months - set(fee_months))
            prepaid_early = (
                fee_months[0][5:7] in ("01", "02") and last_month_of_year[year] > fee_months[-1]
            )
            if not (single_with_other_months or prepaid_early):
                continue
            fee_items = [i for i in items if i["rodzaj"] == rodzaj and i["miesiac"] in fee_months]
            if rodzaj == "ksiegowosc":
                fee_items += [i for i in items if i["rodzaj"] == "rabat" and i["miesiac"] in fee_months]
            total = sum(float(i.get("wartosc_netto") or 0) for i in fee_items)
            for month in _year_months(year):
                result.append({**fee_items[0], "rodzaj": rodzaj, "miesiac": month, "wartosc_netto": total / 12})
            spread.extend(id(i) for i in fee_items)
        result.extend(i for i in items if id(i) not in spread)
    return result


def _shares(per_emp: Dict[str, Dict[str, int]], kind: str) -> Dict[str, float]:
    """Udziały pracowników: wg czasu danego rodzaju, potem wg całego czasu, na końcu po równo."""
    for weights in (
        {e: v[kind] for e, v in per_emp.items()},
        {e: v["glowny"] + v["dodatkowy"] for e, v in per_emp.items()},
    ):
        total = sum(weights.values())
        if total:
            return {e: w / total for e, w in weights.items()}
    return {e: 1.0 / len(per_emp) for e in per_emp}


def compute_employee_report(
    work: List[Dict[str, Any]],
    invoices: List[Dict[str, Any]],
    rates: Dict[str, float],
    counts: Optional[Dict[tuple, int]] = None,
    months: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Wiersze (sekcja, firma, miesiąc, pracownik).

    Każda sekcja (księgowość/kadry) ma: czas główny + cena główna oraz czas i cenę usług
    dodatkowych. Ceny z faktur dzielone są między pracowników proporcjonalnie do czasu,
    koszt = stawka × czas, zysk = cena główna + cena dodatkowa − koszt.
    counts: (sekcja, firma_id, miesiąc) -> liczba dokumentów (księgowość) lub umów (kadry).
    months: ogranicza wynik do podanych miesięcy.
    """
    counts = counts or {}
    names: Dict[Any, str] = {}
    hours: Dict[tuple, Dict[str, Dict[str, int]]] = {}
    price: Dict[tuple, Dict[str, float]] = {}

    for w in work:
        if not w.get("firma_id"):
            continue
        names.setdefault(w["firma_id"], w.get("firma_nazwa"))
        key = (dzial_sekcja(w["dzial"]), w["firma_id"], w["miesiac"])
        emp = (w.get("pracownik_nazwa") or "").strip() or NO_EMPLOYEE
        slot = hours.setdefault(key, {}).setdefault(emp, {"glowny": 0, "dodatkowy": 0})
        kind = "dodatkowy" if w["dzial"] in ("inne_ksiegowosc", "inne_kadry") else "glowny"
        slot[kind] += int(w.get("sekundy") or 0)

    def has_time(sekcja, firma_id, miesiac):
        return any(v["glowny"] + v["dodatkowy"] for v in hours.get((sekcja, firma_id, miesiac), {}).values())

    for inv in spread_annual_fees(invoices):
        names.setdefault(inv["firma_id"], inv.get("kontrahent"))
        rodzaj = inv["rodzaj"]
        dzial = invoice_item_dzial(rodzaj)
        sekcja = dzial_sekcja(dzial)
        if rodzaj == "zdrowotna" and not has_time("kadry", inv["firma_id"], inv["miesiac"]):
            sekcja, dzial = "ksiegowosc", "ksiegowosc"
        kind = "dodatkowy" if dzial in ("inne_ksiegowosc", "inne_kadry") else "glowny"
        slot = price.setdefault((sekcja, inv["firma_id"], inv["miesiac"]), {"glowny": 0.0, "dodatkowy": 0.0})
        slot[kind] += float(inv.get("wartosc_netto") or 0)

    result = []
    for key in set(hours) | set(price):
        sekcja, firma_id, miesiac = key
        if months is not None and miesiac not in months:
            continue
        per_emp = hours.get(key) or {NO_EMPLOYEE: {"glowny": 0, "dodatkowy": 0}}
        cena = price.get(key, {"glowny": 0.0, "dodatkowy": 0.0})
        share_main = _shares(per_emp, "glowny")
        share_extra = _shares(per_emp, "dodatkowy")
        for emp, sek in per_emp.items():
            total_sek = sek["glowny"] + sek["dodatkowy"]
            koszt = total_sek / 3600 * float(rates.get(fold(emp), 0) or 0)
            cena_glowna = cena["glowny"] * share_main[emp]
            cena_dod = cena["dodatkowy"] * share_extra[emp]
            result.append({
                "sekcja": sekcja,
                "firma_id": firma_id,
                "klient": names.get(firma_id) or "",
                "miesiac": miesiac,
                "ilosc": int(counts.get(key, 0)),
                "pracownik": emp,
                "sek_glowny": sek["glowny"],
                "cena_glowna": round(cena_glowna, 2),
                "sek_dodatkowy": sek["dodatkowy"],
                "cena_dodatkowa": round(cena_dod, 2),
                "sekundy": total_sek,
                "cena": round(cena_glowna + cena_dod, 2),
                "koszt": round(koszt, 2),
                "zysk": round(cena_glowna + cena_dod - koszt, 2),
            })
    result.sort(key=lambda r: (r["sekcja"], fold(r["klient"]), r["miesiac"], fold(r["pracownik"])))
    return result
