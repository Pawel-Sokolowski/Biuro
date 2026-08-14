# app/db/dao_companies.py
import sqlite3
from pathlib import Path
from typing import Optional, List, Tuple

from config import DB_NAME

_SCHEMA_SQL = """PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS firmy (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nazwa TEXT,
    nip TEXT UNIQUE,
    miasto TEXT,
    liczba_pracownikow INTEGER DEFAULT 0,
    glowny_cennik TEXT,
    limit_dokumentow INTEGER DEFAULT 0,
    sekundy_pracy INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pracownicy (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE,
    imie TEXT,
    nazwisko TEXT,
    haslo_hash TEXT,
    aktywny INTEGER DEFAULT 1,
    is_admin INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pracownik_firma (
    pracownik_id INTEGER,
    firma_id INTEGER,
    rola TEXT,
    PRIMARY KEY (pracownik_id, firma_id),
    FOREIGN KEY (pracownik_id) REFERENCES pracownicy(id) ON DELETE CASCADE,
    FOREIGN KEY (firma_id) REFERENCES firmy(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS statusy_miesieczne (
    firma_id INTEGER,
    miesiac TEXT,
    etap TEXT,
    ilosc_dok INTEGER DEFAULT 0,
    PRIMARY KEY (firma_id, miesiac),
    FOREIGN KEY (firma_id) REFERENCES firmy(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS dokumenty (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firma_id INTEGER,
    numer TEXT,
    typ TEXT,
    cena_netto REAL,
    data_wyst TEXT,
    FOREIGN KEY (firma_id) REFERENCES firmy(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS cenniki (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    klucz TEXT UNIQUE,
    cena_netto_2024 REAL,
    wzrost_ilosci_dokumentow REAL,
    cena_za_dokument REAL,
    za_przekroczenie_limitu_netto REAL,
    zdrowotna REAL,
    netto REAL,
    brutto REAL,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS role (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nazwa TEXT UNIQUE
);
"""

def get_conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or DB_NAME
    conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db(db_path: Optional[str] = None, schema_file: Optional[str] = None) -> None:
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        if schema_file:
            p = Path(schema_file)
            if p.exists():
                cur.executescript(p.read_text(encoding="utf-8"))
                conn.commit()
                return
        local_schema = Path(__file__).parent.joinpath("schema.sql")
        if local_schema.exists():
            cur.executescript(local_schema.read_text(encoding="utf-8"))
            conn.commit()
            return
        cur.executescript(_SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()

# przykładowe DAO helpery (używane przez serwisy/UI)
def list_companies_for_employee(pracownik_id: Optional[int] = None, db_path: Optional[str] = None) -> List[Tuple]:
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        if pracownik_id is None:
            cur.execute("SELECT id, nazwa, nip, miasto, liczba_pracownikow, glowny_cennik, limit_dokumentow FROM firmy ORDER BY nazwa")
        else:
            cur.execute("""
                SELECT DISTINCT f.id, f.nazwa, f.nip, f.miasto, f.liczba_pracownikow, f.glowny_cennik, f.limit_dokumentow
                FROM firmy f
                JOIN pracownik_firma pf ON f.id = pf.firma_id
                WHERE pf.pracownik_id = ?
                ORDER BY f.nazwa
            """, (pracownik_id,))
        return cur.fetchall()
    finally:
        conn.close()
