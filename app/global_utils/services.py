# app/global/services.py
import sqlite3
from app.config import DB_NAME


def pobierz_uslugi_globalne():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("SELECT id, nazwa, cena_netto, stawka_vat FROM uslugi_dodatkowe ORDER BY nazwa")
    rows = cur.fetchall()
    conn.close()
    return rows


def pobierz_uslugi_firmy(firma_id: int, miesiac: str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute("""
        SELECT u.nazwa, f.ilosc, f.cena_netto, u.stawka_vat
        FROM uslugi_dodatkowe_firma f
        JOIN uslugi_dodatkowe u ON u.id = f.usluga_id
        WHERE f.firma_id = ? AND f.miesiac = ?
    """, (firma_id, miesiac))
    rows = cur.fetchall()
    conn.close()
    return rows
