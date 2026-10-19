# app/global/services.py
from app.db.dao_conn import get_conn


def pobierz_uslugi_globalne():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, nazwa, cena_netto, stawka_vat FROM uslugi_dodatkowe ORDER BY nazwa")
    rows = cur.fetchall()
    conn.close()
    return rows


def pobierz_uslugi_firmy(firma_id: int, miesiac: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT u.nazwa, f.ilosc, f.cena_netto, u.stawka_vat
        FROM uslugi_dodatkowe_firma f
        JOIN uslugi_dodatkowe u ON u.id = f.usluga_id
        WHERE f.firma_id = %s AND f.miesiac = %s
    """, (firma_id, miesiac))
    rows = cur.fetchall()
    conn.close()
    return rows
