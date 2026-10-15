"""Integracja z KSeF - na razie numer faktury i odnośnik do podglądu (bez pobierania przez API)."""

from __future__ import annotations

from typing import Optional

from app.db.dao_app_settings import get_setting

URL_TEMPLATE_KEY = "ksef_url_szablon"
# Anonimowe wyszukiwanie faktur w KSeF; w szablonie można użyć {numer}
DEFAULT_URL_TEMPLATE = "https://qr.ksef.mf.gov.pl/invoice/search"


def ksef_url(ksef_numer: Optional[str]) -> str:
    template = (get_setting(URL_TEMPLATE_KEY) or "").strip() or DEFAULT_URL_TEMPLATE
    return template.replace("{numer}", (ksef_numer or "").strip())
