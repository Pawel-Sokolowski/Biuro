"""Application configuration shared by all database-backed modules."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

if getattr(sys, "frozen", False):
    _default_db_path = (
        Path(os.getenv("LOCALAPPDATA") or Path.home())
        / "Biuro"
        / "biuro.db"
    )
    _default_db_path.parent.mkdir(parents=True, exist_ok=True)
else:
    _default_db_path = Path(__file__).resolve().parents[1] / "biuro.db"
DEFAULT_DB_NAME = os.getenv("DB_NAME") or str(_default_db_path)
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
