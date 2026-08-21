# app/db/init_db.py
import sqlite3
import bcrypt
from app.config import DB_NAME


def init_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("PRAGMA foreign_keys = ON;")

    # ============================================================
    # FIRMY
    # ============================================================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS firmy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nazwa TEXT NOT NULL,
            nip TEXT UNIQUE,
            miasto TEXT,
            uop INTEGER DEFAULT 0,
            uz INTEGER DEFAULT 0,
            uod INTEGER DEFAULT 0,
            sekundy_pracy INTEGER DEFAULT 0,

            glowny_cennik TEXT,
            limit_dokumentow INTEGER DEFAULT 0,
            typ_rozliczenia TEXT DEFAULT 'Ryczałt',
            vat_czynny INTEGER DEFAULT 0,
            glowny_cennik_manualny INTEGER DEFAULT 0
        )
    """)

    # ============================================================
    # STATUSY MIESIĘCZNE
    # ============================================================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS statusy_miesieczne (
            firma_id INTEGER NOT NULL,
            miesiac TEXT NOT NULL,
            etap TEXT DEFAULT 'Brak',
            ilosc_dok INTEGER DEFAULT 0,
            sekundy_pracy INTEGER DEFAULT 0,
            wartosc_uslug REAL DEFAULT 0,
            PRIMARY KEY(firma_id, miesiac),
            FOREIGN KEY(firma_id) REFERENCES firmy(id)
        )
    """)

    # ============================================================
    # DOKUMENTY
    # ============================================================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS dokumenty (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firma_id INTEGER NOT NULL,
            numer TEXT,
            typ TEXT,
            cena_netto REAL,
            data_wyst TEXT,
            FOREIGN KEY(firma_id) REFERENCES firmy(id)
        )
    """)

    # ============================================================
    # CENNIKI — NOWY FORMAT
    # ============================================================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cenniki (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            klucz TEXT UNIQUE NOT NULL,

            -- NOWY FORMAT
            cena_netto_ksiegowosc REAL NOT NULL,
            cena_za_dokument_ponad_limit REAL NOT NULL,
            zdrowotna REAL NOT NULL,
            netto REAL NOT NULL,
            brutto REAL NOT NULL,

            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ============================================================
    # USŁUGI DODATKOWE — globalne
    # ============================================================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS uslugi_dodatkowe (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nazwa TEXT NOT NULL,
            cena_netto REAL NOT NULL,
            stawka_vat TEXT NOT NULL
        )
    """)

    # ============================================================
    # USŁUGI DODATKOWE — miesięczne
    # ============================================================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS uslugi_dodatkowe_firma (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firma_id INTEGER NOT NULL,
            miesiac TEXT NOT NULL,
            usluga_id INTEGER NOT NULL,
            ilosc INTEGER NOT NULL,
            cena_netto REAL NOT NULL,
            FOREIGN KEY(firma_id) REFERENCES firmy(id),
            FOREIGN KEY(usluga_id) REFERENCES uslugi_dodatkowe(id)
        )
    """)

    # ============================================================
    # PRACOWNICY
    # ============================================================
    cur.execute("""
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

    # ROLE
    cur.execute("""
        CREATE TABLE IF NOT EXISTS role (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nazwa TEXT UNIQUE NOT NULL
        )
    """)

    cur.execute("INSERT OR IGNORE INTO role (id, nazwa) VALUES (1, 'księgowa')")
    cur.execute("INSERT OR IGNORE INTO role (id, nazwa) VALUES (2, 'kadrowa')")
    cur.execute("INSERT OR IGNORE INTO role (id, nazwa) VALUES (3, 'manager')")
    cur.execute("INSERT OR IGNORE INTO role (id, nazwa) VALUES (4, 'admin')")
    cur.execute("INSERT OR IGNORE INTO role (id, nazwa) VALUES (5, 'opiekun')")

    # PRACOWNIK-FIRMA
    cur.execute("""
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

    # ============================================================
    # EMAIL
    # ============================================================
    cur.execute("""
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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS wiadomosci (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            konto_id INTEGER NOT NULL,
            uid TEXT,
            message_id TEXT UNIQUE,
            od TEXT NOT NULL,
            do TEXT NOT NULL,
            cc TEXT,
            temat TEXT,
            tresc TEXT,
            data TEXT,
            folder TEXT DEFAULT 'INBOX',
            przeczytana INTEGER DEFAULT 0,
            flaga INTEGER DEFAULT 0,
            raw_msg BLOB,
            FOREIGN KEY(konto_id) REFERENCES konta_email(id)
        )
    """)

    # ============================================================
    # CHAT
    # ============================================================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS konwersacje (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nazwa TEXT NOT NULL,
            pracownik_creator_id INTEGER NOT NULL,
            data_utworzenia TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(pracownik_creator_id) REFERENCES pracownicy(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS uczestnicy_chat (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            konwersacja_id INTEGER NOT NULL,
            pracownik_id INTEGER NOT NULL,
            FOREIGN KEY(konwersacja_id) REFERENCES konwersacje(id),
            FOREIGN KEY(pracownik_id) REFERENCES pracownicy(id),
            UNIQUE(konwersacja_id, pracownik_id)
        )
    """)

    cur.execute("""
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

    # ============================================================
    # NOTATKI I UDZIAŁOWCY
    # ============================================================
    cur.execute("""
        CREATE TABLE IF NOT EXISTS company_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firma_id INTEGER NOT NULL,
            tresc TEXT NOT NULL,
            data_utworzenia TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(firma_id) REFERENCES firmy(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS udzialowcy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firma_id INTEGER NOT NULL,
            imie_nazwisko TEXT NOT NULL,
            procent REAL NOT NULL,
            FOREIGN KEY(firma_id) REFERENCES firmy(id)
        )
    """)

    # ============================================================
    # PIERWSZY ADMIN
    # ============================================================
    cur.execute("SELECT COUNT(*) AS cnt FROM pracownicy")
    if cur.fetchone()["cnt"] == 0:
        haslo = bcrypt.hashpw("admin123".encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        cur.execute("""
            INSERT INTO pracownicy (id, email, haslo_hash, imie, nazwisko, aktywny)
            VALUES (1, 'admin@biuro.pl', ?, 'Główny', 'Administrator', 1)
        """, (haslo,))

        cur.execute("INSERT OR IGNORE INTO firmy (id, nazwa) VALUES (1, 'Domyślna firma')")

        cur.execute("""
            INSERT OR IGNORE INTO pracownik_firma (pracownik_id, firma_id, rola_id)
            VALUES (1, 1, 4)
        """)

    conn.commit()
    conn.close()
