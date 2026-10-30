"""Synchronizacja numerów KSeF z wystawionymi fakturami (backend, bez UI)."""

from __future__ import annotations

import logging
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.db.dao_app_settings import get_setting, set_setting
from app.db.dao_conn import get_conn
from app.db.dao_invoices import list_issued_without_ksef, set_invoice_number, set_ksef_number
from app.invoices.ksef_client import ENVIRONMENTS, KsefClient, KsefError
from app.utils.crypto import decrypt_password, encrypt_password

logger = logging.getLogger(__name__)

NIP_KEY = "ksef_nip"
TOKEN_KEY = "ksef_token_enc"
ENV_KEY = "ksef_srodowisko"
# API ogranicza zakres dat pojedynczego zapytania
WINDOW_DAYS = 90


def _local_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _child(element: Optional[ET.Element], name: str) -> Optional[ET.Element]:
    if element is None:
        return None
    return next((item for item in element.iter() if _local_name(item) == name), None)


def _text(element: Optional[ET.Element], name: str, default: str = "") -> str:
    item = _child(element, name)
    return (item.text or "").strip() if item is not None else default


def _decimal(value: Any, field: str) -> Decimal:
    try:
        return Decimal(str(value).replace(",", ".")).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise KsefError(f"Faktura KSeF ma nieprawidłową wartość pola {field}.") from exc


def parse_fa3_invoice(xml: str) -> Dict[str, Any]:
    """Odczytuje dane faktury FA(3) potrzebne do lokalnej ewidencji."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise KsefError("KSeF zwrócił nieprawidłowy XML faktury.") from exc
    fa = _child(root, "Fa")
    buyer = _child(root, "Podmiot2")
    identity = _child(buyer, "DaneIdentyfikacyjne")
    number = _text(fa, "P_2")
    issue_date = _text(fa, "P_1")[:10]
    buyer_nip = _digits(_text(identity, "NIP"))
    if not number or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", issue_date) or len(buyer_nip) != 10:
        raise KsefError("Faktura KSeF nie zawiera numeru, daty wystawienia lub NIP-u nabywcy.")
    positions: List[Dict[str, Any]] = []
    for row in (item for item in fa.iter() if _local_name(item) == "FaWiersz"):
        quantity = _decimal(_text(row, "P_8B", "1"), "P_8B")
        unit_net = _decimal(_text(row, "P_9A", "0"), "P_9A")
        net = _decimal(_text(row, "P_11", str(quantity * unit_net)), "P_11")
        vat_text = _text(row, "P_12", "0")
        try:
            vat: Any = int(Decimal(vat_text))
            gross = _decimal(_text(row, "P_11A", str(net * (Decimal("1") + Decimal(vat) / 100))), "P_11A")
        except InvalidOperation:
            vat = vat_text.lower()
            gross = _decimal(_text(row, "P_11A", str(net)), "P_11A")
        positions.append({
            "nazwa": _text(row, "P_7") or "Pozycja KSeF", "ilosc": float(quantity),
            "jednostka": _text(row, "P_8A") or "szt.", "cena_netto": float(unit_net),
            "vat": vat, "netto": float(net), "brutto": float(gross), "zrodlo": "KSeF",
        })
    if not positions:
        raise KsefError(f"Faktura KSeF {number} nie zawiera pozycji.")
    total_net = sum((_decimal(item.text, _local_name(item)) for item in fa.iter()
                     if _local_name(item).startswith("P_13_") and item.text), Decimal("0.00"))
    if not total_net:
        total_net = sum((Decimal(str(item["netto"])) for item in positions), Decimal("0.00"))
    total_gross = _decimal(_text(fa, "P_15", sum(Decimal(str(item["brutto"])) for item in positions)), "P_15")
    address = _child(buyer, "Adres")
    address_pol = _child(address, "AdresPol")
    due = _text(_child(fa, "TerminPlatnosci"), "Termin")
    return {
        "number": number, "issue_date": issue_date, "sale_date": (_text(fa, "P_6") or issue_date)[:10],
        "due_date": due[:10] if due else issue_date, "month": issue_date[:7],
        "buyer_nip": buyer_nip, "buyer_name": _text(identity, "Nazwa"),
        "buyer_country": _text(address_pol, "KodKraju", "PL"),
        "buyer_address": _text(address_pol, "AdresL1"), "buyer_address_2": _text(address_pol, "AdresL2"),
        "net": total_net.quantize(Decimal("0.01")), "gross": total_gross,
        "positions": positions, "kind": _text(fa, "RodzajFaktury", "VAT"),
        "currency": _text(fa, "KodWaluty", "PLN"),
    }


def _upsert_downloaded_invoice(data: Dict[str, Any], ksef_number: str) -> tuple[str, int]:
    """Zastępuje lokalną treść dokumentem KSeF, zachowując jego ID i płatności."""
    conn = get_conn()
    try:
        companies = conn.execute(
            "SELECT * FROM firmy WHERE regexp_replace(COALESCE(nip, ''), '[^0-9]', '', 'g') = %s ORDER BY id FOR UPDATE",
            (data["buyer_nip"],),
        ).fetchall()
        if len(companies) != 1:
            conn.rollback()
            return ("brak_firmy" if not companies else "niejednoznaczna_firma", 0)
        company = dict(companies[0])
        existing = conn.execute(
            "SELECT * FROM faktury WHERE ksef_numer = %s OR (firma_id = %s AND lower(trim(COALESCE(numer, ''))) = lower(trim(%s))) ORDER BY id FOR UPDATE",
            (ksef_number, company["id"], data["number"]),
        ).fetchall()
        if not existing:
            # Lokalna faktura może jeszcze nie mieć numeru z programu zewnętrznego.
            # Używamy NIP-u (firma_id) i miesiąca tylko wtedy, gdy pozostał
            # dokładnie jeden dokument, który nie był wcześniej zsynchronizowany.
            existing = conn.execute(
                "SELECT * FROM faktury WHERE firma_id = %s AND miesiac = %s "
                "AND rodzaj = 'faktura' AND COALESCE(ksef_numer, '') = '' ORDER BY id FOR UPDATE",
                (company["id"], data["month"]),
            ).fetchall()
        if len(existing) > 1:
            conn.rollback()
            return "niejednoznaczna_faktura", 0
        address = data["buyer_address"]
        postal = ""
        city = data["buyer_address_2"]
        match = re.match(r"^\s*(\d{2}-\d{3})\s+(.+)$", city)
        if match:
            postal, city = match.groups()
        values = (
            company["id"], data["month"], float(data["net"]), float(data["gross"]),
            data["issue_date"], data["sale_date"], data["due_date"],
            company.get("nazwa_skrocona") or data["buyer_name"], data["buyer_name"], address,
            postal, city, data["buyer_nip"], data["buyer_country"],
            json.dumps(data["positions"], ensure_ascii=False), data["number"], ksef_number,
        )
        if existing:
            invoice_id = existing[0]["id"]
            conn.execute("""
                UPDATE faktury SET firma_id=%s, miesiac=%s, kwota_netto=%s, kwota_brutto=%s,
                    data_wystawienia=%s, data_sprzedazy=%s, termin_platnosci=%s,
                    nabywca_skrocona=%s, nabywca_pelna=%s, nabywca_adres=%s,
                    nabywca_kod=%s, nabywca_miasto=%s, nabywca_nip=%s, nabywca_kraj=%s,
                    pozycje_json=%s, numer=%s, ksef_numer=%s, ksef_data=to_char(now(), 'YYYY-MM-DD HH24:MI:SS'),
                    eksportowana=1, status='wystawiona', data_eksportu=COALESCE(data_eksportu, to_char(now(), 'YYYY-MM-DD HH24:MI:SS')),
                    updated_at=to_char(now(), 'YYYY-MM-DD HH24:MI:SS') WHERE id=%s
            """, values + (invoice_id,))
            action = "zaktualizowane"
        else:
            invoice_id = conn.execute("""
                INSERT INTO faktury (firma_id, reczna, miesiac, kwota_netto, kwota_brutto,
                    data_wystawienia, data_sprzedazy, termin_platnosci, nabywca_skrocona,
                    nabywca_pelna, nabywca_adres, nabywca_kod, nabywca_miasto, nabywca_nip,
                    nabywca_kraj, pozycje_json, numer, ksef_numer, ksef_data, eksportowana,
                    status, data_eksportu, rodzaj)
                VALUES (%s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, to_char(now(), 'YYYY-MM-DD HH24:MI:SS'), 1, 'wystawiona',
                    to_char(now(), 'YYYY-MM-DD HH24:MI:SS'), 'faktura') RETURNING id
            """, values).fetchone()[0]
            action = "utworzone"
        conn.commit()
        return action, int(invoice_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_ksef_credentials(nip: str, token: Optional[str], environment: str = "prod") -> None:
    if environment not in ENVIRONMENTS:
        raise ValueError(f"Nieznane środowisko KSeF: {environment}")
    set_setting(NIP_KEY, "".join(ch for ch in nip if ch.isdigit()))
    set_setting(ENV_KEY, environment)
    if token:
        set_setting(TOKEN_KEY, encrypt_password(token.strip()))


def is_configured() -> bool:
    from app.invoices.ksef_credentials import load_credentials

    return bool(get_setting(NIP_KEY) and load_credentials())


def client_from_settings() -> KsefClient:
    if not is_configured():
        raise KsefError("Skonfiguruj NIP oraz certyfikat KSeF z kluczem prywatnym. Token KSeF nie jest używany.")
    from app.invoices.ksef_credentials import load_credentials

    cred = load_credentials()
    if not cred:
        raise KsefError("Brak certyfikatu i klucza prywatnego KSeF.")
    return KsefClient(
        get_setting(NIP_KEY),
        "",
        get_setting(ENV_KEY) or "prod",
        certificate=cred["certificate"],
        private_key=cred["private_key"],
    )


def _windows(start: datetime, end: datetime):
    cursor = start
    while cursor < end:
        yield cursor, min(cursor + timedelta(days=WINDOW_DAYS), end)
        cursor += timedelta(days=WINDOW_DAYS)


def _already_imported_ksef_numbers(numbers: List[str]) -> set[str]:
    if not numbers:
        return set()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT ksef_numer FROM faktury WHERE ksef_numer = ANY(%s)", (numbers,),
        ).fetchall()
        return {row["ksef_numer"] for row in rows if row["ksef_numer"]}
    finally:
        conn.close()


def _company_count_for_nips(nips: set[str]) -> Dict[str, int]:
    if not nips:
        return {}
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT regexp_replace(COALESCE(nip, ''), '[^0-9]', '', 'g') AS nip, COUNT(*) AS amount "
            "FROM firmy WHERE regexp_replace(COALESCE(nip, ''), '[^0-9]', '', 'g') = ANY(%s) "
            "GROUP BY regexp_replace(COALESCE(nip, ''), '[^0-9]', '', 'g')",
            (list(nips),),
        ).fetchall()
        return {row["nip"]: int(row["amount"]) for row in rows}
    finally:
        conn.close()


def _metadata_buyer_nip(meta: Dict[str, Any]) -> str:
    return _digits(((meta.get("buyer") or {}).get("identifier") or {}).get("value"))


def sync_issued_invoices(
    date_from: datetime,
    date_to: Optional[datetime] = None,
    client: Optional[KsefClient] = None,
    xml_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Pobiera sprzedaż z KSeF i zastępuje lokalne faktury treścią FA(3)."""
    client = client or client_from_settings()
    end = date_to or datetime.now(timezone.utc)
    if date_from.tzinfo is None:
        date_from = date_from.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    # Najpierw pobieramy całą listę. KSeF potrafi zwrócić ten sam
    # dokument na granicy stron/okien, więc numer KSeF jest kluczem deduplikacji.
    metadata: Dict[str, Dict[str, Any]] = {}
    missing_number: List[str] = []
    for window_start, window_end in _windows(date_from, end):
        for meta in client.query_invoices(window_start, window_end):
            ksef_number = str(meta.get("ksefNumber") or "").strip()
            if not ksef_number:
                missing_number.append(meta.get("invoiceNumber") or "(bez numeru)")
                continue
            metadata.setdefault(ksef_number, meta)

    existing_numbers = _already_imported_ksef_numbers(list(metadata))
    buyer_nips = {_metadata_buyer_nip(meta) for meta in metadata.values()} - {""}
    company_counts = _company_count_for_nips(buyer_nips)
    matched: List[str] = []
    unmatched: List[str] = []
    created: List[str] = []
    updated: List[str] = []
    skipped: List[str] = []
    unmatched.extend(missing_number)
    for ksef_number, meta in metadata.items():
        label = meta.get("invoiceNumber") or ksef_number
        if ksef_number in existing_numbers:
            skipped.append(ksef_number)
            continue
        buyer_nip = _metadata_buyer_nip(meta)
        if buyer_nip and company_counts.get(buyer_nip, 0) != 1:
            reason = "brak firmy o NIP-ie nabywcy" if not company_counts.get(buyer_nip) else "kilka firm ma ten sam NIP"
            unmatched.append(f"{label} — {reason}")
            continue
        xml = client.download_invoice_xml(ksef_number)
        try:
            data = parse_fa3_invoice(xml)
        except KsefError as exc:
            unmatched.append(f"{label} — {exc}")
            continue
        if data["currency"] != "PLN":
            unmatched.append(f"{label} — waluta {data['currency']} nie jest obsługiwana przez lokalną ewidencję")
            continue
        if data["kind"] != "VAT":
            unmatched.append(f"{label} — typ KSeF {data['kind']} wymaga osobnego importu")
            continue
        action, _invoice_id = _upsert_downloaded_invoice(data, ksef_number)
        if action not in ("utworzone", "zaktualizowane"):
            reason = {
                "brak_firmy": "brak firmy o NIP-ie nabywcy",
                "niejednoznaczna_firma": "kilka firm ma ten sam NIP",
                "niejednoznaczna_faktura": "kilka lokalnych faktur ma ten sam numer",
            }.get(action, action)
            unmatched.append(f"{label} — {reason}")
            continue
        matched.append(ksef_number)
        (created if action == "utworzone" else updated).append(ksef_number)
        if xml_dir:
            xml_dir.mkdir(parents=True, exist_ok=True)
            (xml_dir / f"{ksef_number}.xml").write_text(xml, encoding="utf-8")
    return {"dopasowane": matched, "utworzone": created, "zaktualizowane": updated,
            "pominiete": skipped, "niedopasowane": unmatched, "znalezione": len(metadata)}


def _digits(value: Optional[str]) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def _take_match(
    candidates: List[Dict[str, Any]],
    meta: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Porównuje numer bez rozróżniania wielkości liter, NIP i kwotę.

    Brak numeru wymaga jednoznacznego wyniku w odpowiednim miesiącu;
    sam NIP nie wystarcza do przypisania numeru KSeF.
    """
    buyer_nip = _digits(((meta.get("buyer") or {}).get("identifier") or {}).get("value"))
    invoice_number = str(meta.get("invoiceNumber") or "").strip()
    if not buyer_nip or (meta.get("currency") or "PLN") != "PLN":
        logger.warning("NO MATCH invoice=%s nip=%s", invoice_number, buyer_nip)
        return None
    try:
        gross = Decimal(str(meta.get("grossAmount"))).quantize(Decimal("0.01"))
        matches = [invoice for invoice in candidates
                   if _digits(invoice.get("firma_nip")) == buyer_nip
                   and Decimal(str(invoice.get("kwota_brutto"))).quantize(Decimal("0.01")) == gross]
    except (InvalidOperation, TypeError, ValueError):
        logger.warning("NO MATCH invoice=%s nip=%s: nieprawidłowa kwota", invoice_number, buyer_nip)
        return None
    logger.warning("KSEF invoice=%s nip=%s gross=%s", invoice_number, buyer_nip, gross)
    exact = [invoice for invoice in matches
             if invoice_number and str(invoice.get("numer") or "").strip().casefold() == invoice_number.casefold()]
    if exact:
        matches = exact
    else:
        # Nie zastępuj innego istniejącego numeru ani nie przypisuj faktury
        # tylko po NIP: klient może mieć wiele faktur o tej samej kwocie.
        matches = [invoice for invoice in matches if not str(invoice.get("numer") or "").strip()]
        issue_month = str(meta.get("issueDate") or "")[:7]
        if issue_month:
            matches = [invoice for invoice in matches if invoice.get("miesiac") == issue_month]
    if len(matches) != 1:
        logger.warning("NO MATCH invoice=%s nip=%s", invoice_number, buyer_nip)
        return None
    candidates.remove(matches[0])
    logger.warning("MATCH NUMBER %s" if exact else "MATCH NIP %s",
                   matches[0].get("numer") if exact else buyer_nip)
    return matches[0]


def sync_month(miesiac: str, xml_dir: Optional[Path] = None, client: Optional[KsefClient] = None) -> Dict[str, Any]:
    """miesiac w formacie RRRR-MM; pobiera faktury sprzedaży wystawione w tym miesiącu."""
    start = datetime.strptime(miesiac, "%Y-%m").replace(tzinfo=timezone.utc)
    end = (start + timedelta(days=32)).replace(day=1)
    return sync_issued_invoices(start, end - timedelta(seconds=1), client=client, xml_dir=xml_dir)
