"""Diagnostyka SaldeoSMART API (tylko odczyt) z pełnym logiem.

Uruchomienie (z katalogu projektu):
    .venv\\Scripts\\python.exe scripts\\saldeo_probe.py                     # poprzedni miesiąc, pierwsza firma
    .venv\\Scripts\\python.exe scripts\\saldeo_probe.py --nip 6741006281 --month 2026-10

Zapytania: company/list, document/getidlist (miesiąc), document/listbyid (max --limit dokumentów).
Przy błędzie "quota exceeded" kończy od razu. Token nie trafia do logu.
Log: saldeo_probe.log; surowe XML-e: saldeo_probe_raw\\ (zawierają dane kontrahentów - nie wysyłaj ich dalej).
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from xml.etree import ElementTree  # noqa: E402

from app.utils.saldeo_api import SaldeoApiError, SaldeoClient, element_to_dict  # noqa: E402

log = logging.getLogger("saldeo_probe")
RAW = ROOT / "saldeo_probe_raw"


def setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
    for handler in (logging.StreamHandler(sys.stdout), logging.FileHandler(ROOT / "saldeo_probe.log", "w", "utf-8")):
        handler.setFormatter(fmt)
        log.addHandler(handler)
    log.setLevel(logging.DEBUG)


def previous_month() -> str:
    first = dt.date.today().replace(day=1)
    last = first - dt.timedelta(days=1)
    return f"{last.year}-{last.month:02d}"


def dump(name: str, element: ElementTree.Element) -> None:
    RAW.mkdir(exist_ok=True)
    path = RAW / f"{name}_{time.strftime('%H%M%S')}.xml"
    path.write_bytes(ElementTree.tostring(element, encoding="utf-8"))
    log.info("Zapisano: %s", path)


def summarize(documents: list) -> None:
    log.info("Dokumentów: %d", len(documents))
    for field in ("STAGE", "SOURCE_TYPE", "CREATE_SOURCE", "DOCUMENT_TYPE", "EXPORTED", "IS_DOCUMENT_BELONG_TO_COMPANY"):
        log.info("%s: %s", field, dict(Counter(str(d.get(field)) for d in documents)))
    log.info("FOLDER: %s", dict(Counter(str(d.get("FOLDER")) for d in documents)))
    log.info("Pola dokumentu: %s", sorted({k for d in documents for k in d}))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nip")
    parser.add_argument("--month", default=previous_month(), help="RRRR-MM, domyślnie poprzedni miesiąc")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    setup_logging()
    year, month = (int(x) for x in args.month.split("-"))

    try:
        client = SaldeoClient.from_settings()
        companies = client.list_companies()
        log.info("Firm w Saldeo: %d", len(companies))
        company = next((c for c in companies if c.get("VAT_NUMBER") == args.nip), None) if args.nip else companies[0]
        if not company:
            log.error("Nie znaleziono firmy o NIP %s", args.nip)
            return 1
        log.info("Wybrana firma: %s (NIP %s), miesiąc %04d-%02d", company.get("SHORT_NAME"),
                 company.get("VAT_NUMBER"), year, month)
        pid = company["COMPANY_PROGRAM_ID"]

        groups = client.document_id_groups(pid, year, month)
        dump("getidlist", groups)
        for group in groups:
            log.info("Grupa %s: %d ID", group.tag, len(group))
        total = sum(len(g) for g in groups)
        if not total:
            log.info("Brak dokumentów w folderze %04d-%02d. Spróbuj --month z innym miesiącem lub --nip.", year, month)
            return 0

        documents = client.documents_by_group_ids(pid, groups, limit=args.limit)
        summarize(documents)
        if documents:
            log.info("Przykładowy dokument (bez pozycji): %s",
                     {k: v for k, v in documents[0].items() if k not in ("ITEMS", "CONTRACTOR", "BANK_ACCOUNTS")})
    except SaldeoApiError as exc:
        log.error("Błąd API: %s", exc)
        return 2
    log.info("Gotowe. Wyślij mi plik saldeo_probe.log (bez saldeo_probe_raw).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
