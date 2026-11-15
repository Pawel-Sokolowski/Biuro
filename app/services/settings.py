from app.remote.boundary import endpoint as office_endpoint
# app/global/settings.py
from app.db.dao_conn import get_conn


@office_endpoint('app.services.settings.is_admin')
def is_admin(pracownik_id: int) -> bool:
    from app.security.authorization import is_administrator
    return is_administrator(pracownik_id)


@office_endpoint('app.services.settings.get_global_roles')
def get_global_roles():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, nazwa FROM role ORDER BY id")
    rows = cur.fetchall()
    conn.close()
    return rows


@office_endpoint('app.services.settings.get_email_settings')
def get_email_settings():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, pracownik_id, email, serwer_imap, serwer_smtp
        FROM konta_email WHERE COALESCE(systemowe, 0) = 0 ORDER BY id
    """)
    rows = cur.fetchall()
    conn.close()
    return rows
