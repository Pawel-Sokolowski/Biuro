# app/db/schema.py
import sqlite3
import bcrypt
from app.companies.companies_model import ensure_columns
from app.companies.constants import COMPANY_EXTRA_COLUMNS

from config import DB_NAME

def _column_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    cols = [r[1] for r in cursor.fetchall()]
    return column in cols

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Tabela firm z kadrami i stoperem
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS firmy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nazwa TEXT NOT NULL,
            nip TEXT UNIQUE,
            miasto TEXT,
            uop INTEGER DEFAULT 0,
            uz INTEGER DEFAULT 0,
            uod INTEGER DEFAULT 0,
            sekundy_pracy INTEGER DEFAULT 0
        )
    """)
    ensure_columns(cursor, "firmy", COMPANY_EXTRA_COLUMNS)

    # Tabela statusów miesięcznych
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS statusy_miesieczne (
            firma_id INTEGER,
            miesiac TEXT,
            etap TEXT DEFAULT 'Brak',
            ilosc_dok INTEGER DEFAULT 0,
            PRIMARY KEY(firma_id, miesiac)
        )
    """)

    # Tabela dokumentów pod eksport Saldeo
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dokumenty (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firma_id INTEGER,
            numer TEXT,
            typ TEXT,
            cena_netto REAL,
            data_wyst TEXT,
            FOREIGN KEY(firma_id) REFERENCES firmy(id)
        )
    """)

    # === NOWE TABELE: SYSTEM PRACOWNIKÓW ===

    # Tabela pracowników
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pracownicy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            haslo_hash TEXT NOT NULL,
            imie TEXT NOT NULL,
            nazwisko TEXT NOT NULL,
            aktywny INTEGER DEFAULT 1,
            data_utworzenia TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Tabela ról
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS role (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nazwa TEXT UNIQUE NOT NULL
        )
    """)

    # Wstaw domyślne role (jeśli nie istnieją)
    cursor.execute("INSERT OR IGNORE INTO role (id, nazwa) VALUES (1, 'księgowa')")
    cursor.execute("INSERT OR IGNORE INTO role (id, nazwa) VALUES (2, 'kadrowa')")
    cursor.execute("INSERT OR IGNORE INTO role (id, nazwa) VALUES (3, 'manager')")
    cursor.execute("INSERT OR IGNORE INTO role (id, nazwa) VALUES (4, 'admin')")
    cursor.execute("INSERT OR IGNORE INTO role (id, nazwa) VALUES (5, 'opiekun')")

    # Tabela przypisania pracownika do firmy z rolą
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pracownik_firma (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pracownik_id INTEGER NOT NULL,
            firma_id INTEGER NOT NULL,
            rola_id INTEGER,
            rola TEXT,
            FOREIGN KEY(pracownik_id) REFERENCES pracownicy(id),
            FOREIGN KEY(firma_id) REFERENCES firmy(id),
            FOREIGN KEY(rola_id) REFERENCES role(id),
            UNIQUE(pracownik_id, firma_id, rola_id)
        )
    """)

    # === NOWE TABELE: EMAIL SYSTEM ===

    # Tabela kont email
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS konta_email (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pracownik_id INTEGER NOT NULL,
            email TEXT NOT NULL,
            serwer_imap TEXT NOT NULL,
            port_imap INTEGER DEFAULT 993,
            serwer_smtp TEXT NOT NULL,
            port_smtp INTEGER DEFAULT 587,
            haslo_enc TEXT NOT NULL,
            aktywna INTEGER DEFAULT 1,
            FOREIGN KEY(pracownik_id) REFERENCES pracownicy(id)
        )
    """)

    # Tabela wiadomości email (dodano raw_msg BLOB opcjonalnie)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wiadomosci (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            konto_id INTEGER NOT NULL,
            message_id TEXT UNIQUE,
            od TEXT NOT NULL,
            do TEXT NOT NULL,
            cc TEXT,
            temat TEXT,
            tresc TEXT,
            data_otrzymania TEXT,
            folder TEXT DEFAULT 'INBOX',
            przeczytana INTEGER DEFAULT 0,
            flaga INTEGER DEFAULT 0,
            raw_msg BLOB,
            FOREIGN KEY(konto_id) REFERENCES konta_email(id)
        )
    """)

    # Tabela folderów email
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS foldery_email (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            konto_id INTEGER NOT NULL,
            nazwa TEXT NOT NULL,
            liczba_nieprzeczytanych INTEGER DEFAULT 0,
            FOREIGN KEY(konto_id) REFERENCES konta_email(id),
            UNIQUE(konto_id, nazwa)
        )
    """)

    # Tabela reguł filtrów email
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reguly_filtrow (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            konto_id INTEGER NOT NULL,
            od TEXT,
            temat_zawiera TEXT,
            folder_docelowy TEXT,
            akcja TEXT,
            aktywna INTEGER DEFAULT 1,
            FOREIGN KEY(konto_id) REFERENCES konta_email(id)
        )
    """)

    # === NOWE TABELE: CHAT ===

    # Tabela konwersacji
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS konwersacje (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nazwa TEXT NOT NULL,
            pracownik_creator_id INTEGER NOT NULL,
            data_utworzenia TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(pracownik_creator_id) REFERENCES pracownicy(id)
        )
    """)

    # Tabela uczestników konwersacji
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS uczestnicy_chat (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            konwersacja_id INTEGER NOT NULL,
            pracownik_id INTEGER NOT NULL,
            FOREIGN KEY(konwersacja_id) REFERENCES konwersacje(id),
            FOREIGN KEY(pracownik_id) REFERENCES pracownicy(id),
            UNIQUE(konwersacja_id, pracownik_id)
        )
    """)

    # Tabela wiadomości czatu
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS wiadomosci_chat (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            konwersacja_id INTEGER NOT NULL,
            pracownik_id INTEGER NOT NULL,
            tresc TEXT NOT NULL,
            data_wysylki TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(konwersacja_id) REFERENCES konwersacje(id),
            FOREIGN KEY(pracownik_id) REFERENCES pracownicy(id)
        )
    """)

    # === DODATKOWE TABELLE: NOTATKI I UDZIAŁOWCY ===

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS company_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firma_id INTEGER NOT NULL,
            tresc TEXT NOT NULL,
            data_utworzenia TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(firma_id) REFERENCES firmy(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS udzialowcy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firma_id INTEGER NOT NULL,
            imie_nazwisko TEXT NOT NULL,
            procent REAL NOT NULL,
            FOREIGN KEY(firma_id) REFERENCES firmy(id)
        )
    """)

    # === AUTOMATYCZNE TWORZENIE PIERWSZEGO ADMINISTRATORA ===
    cursor.execute("SELECT COUNT(*) FROM pracownicy")
    if cursor.fetchone()[0] == 0:
        domyslne_haslo_hash = bcrypt.hashpw("admin123".encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        cursor.execute("""
            INSERT INTO pracownicy (id, email, haslo_hash, imie, nazwisko, aktywny)
            VALUES (1, 'admin@biuro.pl', ?, 'Glowny', 'Administrator', 1)
        """, (domyslne_haslo_hash,))

        # upewnij się, że istnieje firma o id=1 (może być pusta)
        cursor.execute("INSERT OR IGNORE INTO firmy (id, nazwa) VALUES (1, 'Domyślna firma')")

        cursor.execute("""
            INSERT OR IGNORE INTO pracownik_firma (pracownik_id, firma_id, rola_id)
            VALUES (1, 1, 4)
        """)

    conn.commit()
    conn.close()
