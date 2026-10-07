# app/db/dao_conn.py
import os
import sqlite3
from app.config import get_database

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(get_database(), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    return conn
