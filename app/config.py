"""Application configuration shared by all database-backed modules."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from app.utils.env_config import load_environment

load_environment()

if getattr(sys, "frozen", False):
    _default_db_path = (
        Path(os.getenv("LOCALAPPDATA") or Path.home())
        / "Biuro"
        / "biuro.db"
    )
    _default_db_path.parent.mkdir(parents=True, exist_ok=True)
else:
    _default_db_path = Path(__file__).resolve().parents[1] / "biuro.db"
_env_db = os.getenv("DB_NAME")
if _env_db and getattr(sys, "frozen", False) and not os.path.isabs(_env_db):
    # Względna nazwa z .env trafia do folderu danych użytkownika, a nie do katalogu roboczego.
    _env_db = str(_default_db_path.parent / _env_db)
DEFAULT_DB_NAME = _env_db or str(_default_db_path)
DB_NAME = DEFAULT_DB_NAME
EMAIL_LOCAL_LIMIT = int(os.getenv("EMAIL_LOCAL_LIMIT", "300"))


def set_database(path: str) -> str:
    """Select the SQLite database used by the running application."""
    global DB_NAME
    selected = str(Path(path).expanduser().resolve())
    DB_NAME = selected
    return selected


def get_database() -> str:
    """Return the currently selected database path."""
    return DB_NAME
