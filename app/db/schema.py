import sqlite3
import bcrypt
from app.companies.companies_model import ensure_columns
from app.companies.constants import COMPANY_EXTRA_COLUMNS
from app.config import DB_NAME


def _column_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return column in {r[1] for r in cursor.fetchall()}


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # ============================================================
    # FIRMY
    # ============================================================
    cursor.execute("""
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

    ensure_columns(cursor, "firmy", COMPANY_EXTRA_COLUMNS)

    # MIGRACJE
    if not _column_exists(cursor, "firmy", "typ_rozliczenia"):
        cursor.execute("ALTER TABLE firmy ADD COLUMN typ_rozliczenia TEXT DEFAULT 'Ryczałt'")
    if not _column_exists(cursor, "firmy", "vat_czynny"):
        cursor.execute("ALTER TABLE firmy ADD COLUMN vat_czynny INTEGER DEFAULT 0")
    if not _column_exists(cursor, "firmy", "glowny_cennik_manualny"):
        cursor.execute("ALTER TABLE firmy ADD COLUMN glowny_cennik_manualny INTEGER DEFAULT 0")
    if not _column_exists(cursor, "firmy", "glowny_cennik"):
        cursor.execute("ALTER TABLE firmy ADD COLUMN glowny_cennik TEXT")
    if not _column_exists(cursor, "firmy", "limit_dokumentow"):
        cursor.execute("ALTER TABLE firmy ADD COLUMN limit_dokumentow INTEGER DEFAULT 0")

    # ============================================================
    # STATUSY MIESIĘCZNE — rozszerzone
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS statusy_miesieczne (
            firma_id INTEGER,
            miesiac TEXT,
            etap TEXT DEFAULT 'Brak',
            ilosc_dok INTEGER DEFAULT 0,
            sekundy_pracy INTEGER DEFAULT 0,
            wartosc_uslug REAL DEFAULT 0,
            PRIMARY KEY(firma_id, miesiac)
        )
    """)

    # MIGRACJE
    if not _column_exists(cursor, "statusy_miesieczne", "sekundy_pracy"):
        cursor.execute("ALTER TABLE statusy_miesieczne ADD COLUMN sekundy_pracy INTEGER DEFAULT 0")
    if not _column_exists(cursor, "statusy_miesieczne", "wartosc_uslug"):
        cursor.execute("ALTER TABLE statusy_miesieczne ADD COLUMN wartosc_uslug REAL DEFAULT 0")

    # ============================================================
    # DOKUMENTY
    # ============================================================
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

    # ============================================================
    # CENNIKI GLOBALNE
    # ============================================================
    cursor.execute("""
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
        )
    """)

    # ============================================================
    # GLOBALNE CENY ZA DOKUMENT
    # ============================================================
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS globalne_ceny_za_dokument (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            typ TEXT NOT NULL,
            vat INTEGER NOT NULL,
            cena REAL NOT NULL,
            UNIQUE(typ, vat)
        )
    """)

    # ============================================================
    # USŁUGI DODATKOWE — globalny cennik
    # ============================================================
    cursor.execute("""
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
    cursor.execute("""
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
    # SYSTEM PRACOWNIKÓW
    # ============================================================

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

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS role (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nazwa TEXT UNIQUE NOT NULL
        )
    """)

    cursor.execute("INSERT OR IGNORE INTO role (id, nazwa) VALUES (1, 'księgowa')")
    cursor.execute("INSERT OR IGNORE INTO role (id, nazwa) VALUES (2, 'kadrowa')")
    cursor.execute("INSERT OR IGNORE INTO role (id, nazwa) VALUES (3, 'manager')")
    cursor.execute("INSERT OR IGNORE INTO role (id, nazwa) VALUES (4, 'admin')")
    cursor.execute("INSERT OR IGNORE INTO role (id, nazwa) VALUES (5, 'opiekun')")

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

    # ============================================================
    # EMAIL SYSTEM
    # ============================================================

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

    cursor.execute("""
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

    if not _column_exists(cursor, "wiadomosci", "uid"):
        cursor.execute("ALTER TABLE wiadomosci ADD COLUMN uid TEXT")

    if not _column_exists(cursor, "wiadomosci", "data"):
        cursor.execute("ALTER TABLE wiadomosci ADD COLUMN data TEXT")

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

    # ============================================================
    # CHAT
    # ============================================================

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS konwersacje (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nazwa TEXT NOT NULL,
            pracownik_creator_id INTEGER NOT NULL,
            data_utworzenia TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(pracownik_creator_id) REFERENCES pracownicy(id)
        )
    """)

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

    # ============================================================
    # NOTATKI I UDZIAŁOWCY
    # ============================================================

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

    # ============================================================
    # PIERWSZY ADMIN
    # ============================================================

    cursor.execute("SELECT COUNT(*) FROM pracownicy")
    if cursor.fetchone()[0] == 0:
        domyslne_haslo_hash = bcrypt.hashpw("admin123".encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        cursor.execute("""
            INSERT INTO pracownicy (id, email, haslo_hash, imie, nazwisko, aktywny)
            VALUES (1, 'admin@biuro.pl', ?, 'Glowny', 'Administrator', 1)
        """, (domyslne_haslo_hash,))

        cursor.execute("INSERT OR IGNORE INTO firmy (id, nazwa) VALUES (1, 'Domyślna firma')")

        cursor.execute("""
            INSERT OR IGNORE INTO pracownik_firma (pracownik_id, firma_id, rola_id)
            VALUES (1, 1, 4)
        """)

    conn.commit()
    conn.close()
