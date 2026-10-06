# app/db/dao_conn.py
import os
import sqlite3
from dotenv import load_dotenv

# Load .env once when module is imported
load_dotenv()

DB_NAME = os.getenv("DB_NAME")

def get_conn():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
    except Exception:
        pass
    return conn
