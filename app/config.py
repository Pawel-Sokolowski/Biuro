"""Application configuration shared by all database-backed modules."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_default_db_path = Path(__file__).resolve().parents[1] / "biuro.db"
DB_NAME = os.getenv("DB_NAME") or str(_default_db_path)
EMAIL_LOCAL_LIMIT = int(os.getenv("EMAIL_LOCAL_LIMIT", "300"))
