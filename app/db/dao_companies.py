import sqlite3
from pathlib import Path
from typing import Optional, List, Tuple

from app.config import DB_NAME

# ============================================================
#  SCHEMA – rozszerzone o typ rozliczenia, VAT, globalne cenniki
# ============================================================

_SCHEMA_SQL = """PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS firmy (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nazwa TEXT,
    nip TEXT UNIQUE,
    miasto TEXT,
    liczba_pracownikow INTEGER DEFAULT 0,
    glowny_cennik TEXT,
    limit_dokumentow INTEGER DEFAULT 0,
    typ_rozliczenia TEXT,
    vat_czynny INTEGER DEFAULT 0,
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

-- GLOBALNY CENNIK (Ryczałt_N_20, KPIR_T_50 itd.)
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

-- CENA ZA DOKUMENT PONAD LIMIT (Ryczałt_N → 5, KPIR_T → 5.5 itd.)
CREATE TABLE IF NOT EXISTS globalne_ceny_za_dokument (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    typ TEXT NOT NULL,
    vat INTEGER NOT NULL,
    cena REAL NOT NULL,
    UNIQUE(typ, vat)
);

CREATE TABLE IF NOT EXISTS role (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nazwa TEXT UNIQUE
);
"""


# ============================================================
#  DB CONNECTION
# ============================================================

def get_conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or DB_NAME
    conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
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


# ============================================================
#  LISTA FIRM
# ============================================================

def list_companies_for_employee(pracownik_id: Optional[int] = None, db_path: Optional[str] = None) -> List[Tuple]:
    conn = get_conn(db_path)
    cur = conn.cursor()
    try:
        if pracownik_id is None:
            cur.execute("""
                SELECT id, nazwa, nip, miasto, liczba_pracownikow,
                       glowny_cennik, limit_dokumentow, typ_rozliczenia, vat_czynny
                FROM firmy ORDER BY nazwa
            """)
        else:
            cur.execute("""
                SELECT DISTINCT f.id, f.nazwa, f.nip, f.miasto, f.liczba_pracownikow,
                                f.glowny_cennik, f.limit_dokumentow, f.typ_rozliczenia, f.vat_czynny
                FROM firmy f
                JOIN pracownik_firma pf ON f.id = pf.firma_id
                WHERE pf.pracownik_id = ?
                ORDER BY f.nazwa
            """, (pracownik_id,))
        return cur.fetchall()
    finally:
        conn.close()


# ============================================================
#  CENNIK – wybór klucza
# ============================================================

def _extract_limit_from_key(klucz: str) -> Optional[int]:
    """
    Wyciąga limit z klucza typu:
        Ryczałt_N_20 → 20
        KPIR_T_50 → 50
    """
    try:
        parts = klucz.split("_")
        return int(parts[-1])
    except Exception:
        return None


def wybierz_cennik(conn: sqlite3.Connection, typ: str, vat_czynny: bool, limit_dokumentow: int) -> Optional[sqlite3.Row]:
    """
    Zwraca rekord z tabeli cenniki dla:
    - typu rozliczenia: Ryczałt / KPIR / KH
    - VAT: True/False
    - limitu dokumentów: np. 20, 50, 100

    Klucz ma postać:
        Ryczałt_N_20
        KPIR_T_50
        KH_N_100
    """

    vat_suffix = "T" if vat_czynny else "N"
    pattern = f"{typ}_{vat_suffix}_%"

    cur = conn.cursor()

    # Pobierz wszystkie klucze danego typu
    cur.execute("SELECT * FROM cenniki WHERE klucz LIKE ? ORDER BY klucz", (pattern,))
    rows = cur.fetchall()
    if not rows:
        return None

    # Wybierz najlepszy klucz
    best_row = None
    best_limit = None

    for row in rows:
        lim = _extract_limit_from_key(row["klucz"])
        if lim is None:
            continue

        if lim <= limit_dokumentow and (best_limit is None or lim > best_limit):
            best_limit = lim
            best_row = row

    # Jeśli nie znaleziono <= limitu, weź najmniejszy dostępny
    if best_row is None:
        rows_sorted = sorted(rows, key=lambda r: _extract_limit_from_key(r["klucz"]) or 999999)
        best_row = rows_sorted[0]

    return best_row


# ============================================================
#  CENA ZA DOKUMENT PONAD LIMIT
# ============================================================

def pobierz_cene_za_dokument(conn: sqlite3.Connection, typ: str, vat_czynny: bool) -> Optional[float]:
    """
    Zwraca cenę za dokument ponad limit z tabeli globalne_ceny_za_dokument.
    """
    cur = conn.cursor()
    vat = 1 if vat_czynny else 0

    cur.execute("""
        SELECT cena FROM globalne_ceny_za_dokument
        WHERE typ = ? AND vat = ?
    """, (typ, vat))

    row = cur.fetchone()
    return row["cena"] if row else None
