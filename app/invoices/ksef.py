"""Odnośniki do urzędowego podglądu/weryfikacji faktury KSeF."""

from __future__ import annotations

from typing import Optional
from urllib.parse import quote
from datetime import datetime

from app.db.dao_app_settings import get_setting

URL_TEMPLATE_KEY = "ksef_url_szablon"
# Anonimowe wyszukiwanie faktur w KSeF; w szablonie można użyć {numer}
DEFAULT_URL_TEMPLATE = "https://qr.ksef.mf.gov.pl/invoice/search"
VERIFICATION_URL_TEMPLATE = "https://qr.ksef.mf.gov.pl/invoice/{nip}/{data}/{hash}"


def ksef_url(ksef_numer: Optional[str], invoice: Optional[dict] = None) -> str:
    invoice = invoice or {}
    number = (ksef_numer or "").strip()
    nip = "".join(ch for ch in str(invoice.get("ksef_nip_wysylki") or "") if ch.isdigit())
    issue_date = str(invoice.get("data_wystawienia") or "")[:10]
    try:
        issue_date = datetime.strptime(issue_date, "%Y-%m-%d").strftime("%d-%m-%Y")
    except ValueError:
        issue_date = ""
    document_hash = str(invoice.get("ksef_hash_faktury") or "").strip()
    # Skrót w linku weryfikacyjnym ma postać Base64URL bez dopełnienia.
    url_hash = document_hash.rstrip("=").replace("+", "-").replace("/", "_")
    configured = (get_setting(URL_TEMPLATE_KEY) or "").strip()
    if nip and issue_date and url_hash and configured in ("", DEFAULT_URL_TEMPLATE):
        template = VERIFICATION_URL_TEMPLATE
    else:
        template = configured or DEFAULT_URL_TEMPLATE
    replacements = {"{numer}": number, "{nip}": nip, "{data}": issue_date, "{hash}": url_hash}
    for marker, value in replacements.items():
        template = template.replace(marker, quote(value, safe=""))
    return template
