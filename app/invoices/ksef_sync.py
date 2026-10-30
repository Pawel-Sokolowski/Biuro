"""Synchronizacja numerów KSeF z wystawionymi fakturami (backend, bez UI)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.db.dao_app_settings import get_setting, set_setting
from app.db.dao_invoices import list_issued_without_ksef, set_invoice_number, set_ksef_number
from app.invoices.ksef_client import ENVIRONMENTS, KsefClient, KsefError
from app.utils.crypto import decrypt_password, encrypt_password

logger = logging.getLogger(__name__)

NIP_KEY = "ksef_nip"
TOKEN_KEY = "ksef_token_enc"
ENV_KEY = "ksef_srodowisko"
# API ogranicza zakres dat pojedynczego zapytania
WINDOW_DAYS = 90


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


def sync_issued_invoices(
    date_from: datetime,
    date_to: Optional[datetime] = None,
    client: Optional[KsefClient] = None,
    xml_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Pobiera z KSeF faktury sprzedaży i wpisuje numery KSeF do wystawionych faktur o tym samym numerze."""
    client = client or client_from_settings()
    end = date_to or datetime.now(timezone.utc)
    if date_from.tzinfo is None:
        date_from = date_from.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    candidates = list_issued_without_ksef()
    matched: List[str] = []
    unmatched: List[str] = []
    for window_start, window_end in _windows(date_from, end):
        for meta in client.query_invoices(window_start, window_end):
            ksef_number = meta.get("ksefNumber")
            invoice = _take_match(candidates, meta)
            if not invoice:
                unmatched.append(meta.get("invoiceNumber") or ksef_number)
                continue
            set_ksef_number(invoice["id"], ksef_number)
            # numer nadany w Saldeo bywa pusty do czasu importu; bierzemy go z KSeF
            if not invoice.get("numer") and meta.get("invoiceNumber"):
                set_invoice_number(invoice["id"], meta["invoiceNumber"])
            matched.append(ksef_number)
            if xml_dir:
                xml_dir.mkdir(parents=True, exist_ok=True)
                (xml_dir / f"{ksef_number}.xml").write_text(client.download_invoice_xml(ksef_number), encoding="utf-8")
    return {"dopasowane": matched, "niedopasowane": unmatched}


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
