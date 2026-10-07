# app/global/settings.py
from app.db.dao_conn import get_conn


def is_admin(pracownik_id: int) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT rola_id FROM pracownik_firma
        WHERE pracownik_id = ? AND rola_id = 4
    """, (pracownik_id,))
    row = cur.fetchone()
    conn.close()
    return bool(row)


def get_global_roles():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, nazwa FROM role ORDER BY id")
    rows = cur.fetchall()
    conn.close()
    return rows


def get_email_settings():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, pracownik_id, email, serwer_imap, serwer_smtp
        FROM konta_email ORDER BY id
    """)
    rows = cur.fetchall()
    conn.close()
    return rows
