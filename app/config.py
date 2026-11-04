"""Konfiguracja aplikacji: połączenie z PostgreSQL i katalog danych lokalnych."""

from __future__ import annotations

import os
import sys
from contextvars import ContextVar
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from app.utils.env_config import load_environment

load_environment()

EMAIL_HISTORY_DAYS = int(os.getenv("EMAIL_HISTORY_DAYS", "365"))
EMAIL_LOCAL_LIMIT = int(os.getenv("EMAIL_LOCAL_LIMIT", "300"))

_schema: str | None = None
_schema_override = ContextVar("biuro_schema", default=...)


def get_data_dir() -> Path:
    """Folder na pliki lokalne (cache poczty, kopie). Baza danych jest w PostgreSQL."""
    if getattr(sys, "frozen", False):
        base = Path(os.getenv("LOCALAPPDATA") or Path.home()) / "Biuro"
    else:
        base = Path(__file__).resolve().parents[1]
    base.mkdir(parents=True, exist_ok=True)
    return base


def get_dsn() -> str:
    """Adres bazy PostgreSQL z DB_URL (postgresql://uzytkownik:haslo@host:5432/baza)."""
    url = (os.getenv("DB_URL") or "").strip()
    if not url:
        raise RuntimeError(
            "Brak DB_URL w pliku .env. Ustaw np. DB_URL=postgresql://uzytkownik:haslo@host:5432/biuro"
        )
    return url


def get_database() -> str:
    """Opis bazy do wyświetlenia (host:port/baza), bez hasła."""
    try:
        parsed = urlparse(get_dsn())
    except RuntimeError:
        return "(brak DB_URL)"
    return f"{parsed.hostname}:{parsed.port or 5432}{parsed.path}"


def set_schema(name: str | None) -> None:
    """Wybiera schemat PostgreSQL (search_path). Używane głównie przez testy."""
    global _schema
    _schema = name or None


def get_schema() -> str | None:
    override = _schema_override.get()
    return _schema if override is ... else override


@contextmanager
def schema_context(name: str | None):
    """Keep a background operation on its originating schema during UI/test changes."""
    token = _schema_override.set(name)
    try:
        yield
    finally:
        _schema_override.reset(token)
