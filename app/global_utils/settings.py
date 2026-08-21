# app/global/settings.py
import sqlite3
from app.config import DB_NAME


def is_admin(pracownik_id: int) -> bool:
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT rola_id FROM pracownik_firma
        WHERE pracownik_id = ? AND rola_id = 4
    """, (pracownik_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row)


def get_global_roles():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id, nazwa FROM role ORDER BY id")
    rows = cur.fetchall()
    conn.close()
    return rows


def get_email_settings():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT id, pracownik_id, email, serwer_imap, serwer_smtp
        FROM konta_email ORDER BY id
    """)
    rows = cur.fetchall()
    conn.close()
    return rows
