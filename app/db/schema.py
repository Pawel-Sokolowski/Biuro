# app/db/schema.py
import sqlite3
import bcrypt
from pathlib import Path
from app.config import DB_NAME


def _safe_alter_add_column(cur: sqlite3.Cursor, table: str, column_def: str):
    """
    Try to add a column to a table. Ignore if column already exists or other OperationalError.
    column_def example: "can_edit_companies INTEGER DEFAULT 0"
    """
    try:
        cur.execute(f"ALTER TABLE {table} ADD COLUMN {column_def};")
    except sqlite3.OperationalError:
        pass


def init_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("PRAGMA foreign_keys = ON;")
    cur.execute("PRAGMA journal_mode = WAL;")
    cur.execute("PRAGMA synchronous = NORMAL;")

    # -------------------------
    # FIRMY
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS firmy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nazwa TEXT NOT NULL,
            nazwa_skrocona TEXT,
            nip TEXT,
            regon TEXT,
            krs TEXT,
            pesel TEXT,

            kraj TEXT,
            wojewodztwo TEXT,
            powiat TEXT,
            gmina TEXT,
            miasto TEXT,
            kod_pocztowy TEXT,
            ulica TEXT,
            adres_budynek TEXT,
            adres_lokal TEXT,

            forma_dzialalnosci TEXT,
            forma_opodatkowania TEXT,
            analityka_fk TEXT,
            osoba_kontaktowa TEXT,
            adres_email TEXT,
            nr_telefonu TEXT,

            adres_zam_kraj TEXT,
            adres_zam_wojewodztwo TEXT,
            adres_zam_powiat TEXT,
            adres_zam_gmina TEXT,
            adres_zam_miasto TEXT,
            adres_zam_kod_pocztowy TEXT,
            adres_zam_ulica TEXT,
            adres_zam_budynek TEXT,
            adres_zam_lokal TEXT,

            adres_koresp_kraj TEXT,
            adres_koresp_wojewodztwo TEXT,
            adres_koresp_powiat TEXT,
            adres_koresp_gmina TEXT,
            adres_koresp_miasto TEXT,
            adres_koresp_kod_pocztowy TEXT,
            adres_koresp_ulica TEXT,
            adres_koresp_budynek TEXT,
            adres_koresp_lokal TEXT,

            data_rozpoczecia_dg TEXT,
            data_rozpoczecia_sprzedazy TEXT,
            data_koniec_zus_start TEXT,
            data_koniec_zus_pref TEXT,
            data_koniec_maly_zus TEXT,
            data_koniec_a1 TEXT,
            data_rozpoczecia_obslugi TEXT,
            data_zakonczenia_obslugi TEXT,

            zawieszenia_dzialalnosci TEXT,
            kody_pkd TEXT,

            cennik_ksiegowy TEXT,
            cennik_kadrowy TEXT,
            cennik_czasowy TEXT,

            glowny_cennik TEXT,
            glowny_cennik_manualny INTEGER DEFAULT 0,

            limit_dokumentow INTEGER DEFAULT 0,
            liczba_pracownikow INTEGER DEFAULT 0,

            uop_ilosc INTEGER DEFAULT 0,
            uz_ilosc INTEGER DEFAULT 0,
            uod_ilosc INTEGER DEFAULT 0,

            uop_cennik TEXT,
            uz_cennik TEXT,
            uod_cennik TEXT,

            typ_rozliczenia TEXT DEFAULT 'Ryczałt',
            vat_czynny INTEGER DEFAULT 0,
            status TEXT DEFAULT 'Aktywne',

            cennik_kpir_auto TEXT,
            cena_ksiegowosc_auto REAL DEFAULT 0,
            cennik_kpir_manual TEXT,
            manual_cennik_override INTEGER DEFAULT 0,

            zdrowotna REAL DEFAULT 0,

            fakturowac INTEGER DEFAULT 0,
            wysylac_do_ksef INTEGER DEFAULT 0,
            wysylac_do_kontrahenta INTEGER DEFAULT 0,
            metoda_kasowa INTEGER DEFAULT 0
            );
    """)

    # ensure commonly referenced columns exist (backwards compatibility)
    _safe_alter_add_column(cur, "firmy", "konto_bankowe TEXT")
    _safe_alter_add_column(cur, "firmy", "uwagi TEXT")
    _safe_alter_add_column(cur, "firmy", "kategoria_domyslna TEXT")
    _safe_alter_add_column(cur, "firmy", "glowny_cennik TEXT")
    _safe_alter_add_column(cur, "firmy", "limit_dokumentow INTEGER DEFAULT 0")
    _safe_alter_add_column(cur, "firmy", "vat_czynny INTEGER DEFAULT 0")
    _safe_alter_add_column(cur, "firmy", "glowny_cennik_manualny INTEGER DEFAULT 0")

    # -------------------------
    # preferencje cennikowe
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS firma_cennik_preference (
            firma_id INTEGER PRIMARY KEY,
            wybrany_klucz TEXT,
            cena_netto REAL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(firma_id) REFERENCES firmy(id) ON DELETE CASCADE
        );
    """)

    # -------------------------
    # statusy_miesieczne
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS statusy_miesieczne (
            firma_id INTEGER NOT NULL,
            miesiac TEXT NOT NULL,
            etap TEXT DEFAULT 'Brak',
            ilosc_dok INTEGER DEFAULT 0,
            sekundy_ksiegowosc INTEGER DEFAULT 0,
            sekundy_kadry INTEGER DEFAULT 0,
            wartosc_uslug REAL DEFAULT 0,
            PRIMARY KEY(firma_id, miesiac),
            FOREIGN KEY(firma_id) REFERENCES firmy(id) ON DELETE CASCADE
        );
    """)

    # -------------------------
    # dokumenty
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS dokumenty (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firma_id INTEGER NOT NULL,
            numer TEXT,
            typ TEXT,
            cena_netto REAL,
            data_wyst TEXT,
            opis TEXT,
            zrodlo TEXT,
            FOREIGN KEY(firma_id) REFERENCES firmy(id) ON DELETE CASCADE
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS dokumenty_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dokument_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            path TEXT,
            mime TEXT,
            data_dodania TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(dokument_id) REFERENCES dokumenty(id) ON DELETE CASCADE
        );
    """)

    # -------------------------
    # cenniki księgowość
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cenniki (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            klucz TEXT UNIQUE NOT NULL,
            typ TEXT DEFAULT 'KSIEGOWOSC',
            cena_netto_ksiegowosc REAL NOT NULL DEFAULT 0,
            cena_brutto_ksiegowosc REAL NOT NULL DEFAULT 0,
            cena_za_dokument_ponad_limit REAL NOT NULL DEFAULT 0,
            zdrowotna REAL NOT NULL DEFAULT 0,
            netto REAL,
            brutto REAL,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cenniki_typ_klucz ON cenniki(typ, klucz);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cenniki_klucz ON cenniki(klucz);")

    # -------------------------
    # cenniki kadry
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cenniki_kadry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            klucz TEXT UNIQUE NOT NULL,
            cena_netto_kadry REAL NOT NULL DEFAULT 0,
            cena_brutto_kadry REAL NOT NULL DEFAULT 0,
            opis TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cenniki_kadry_klucz ON cenniki_kadry(klucz);")

    # -------------------------
    # usługi dodatkowe
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS uslugi_dodatkowe (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nazwa TEXT NOT NULL,
            cena_netto REAL NOT NULL,
            stawka_vat TEXT NOT NULL,
            kategoria TEXT DEFAULT 'księgowość'
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS uslugi_dodatkowe_firma (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firma_id INTEGER NOT NULL,
            miesiac TEXT NOT NULL,
            usluga_id INTEGER NOT NULL,
            ilosc INTEGER NOT NULL,
            cena_netto REAL NOT NULL,
            FOREIGN KEY(firma_id) REFERENCES firmy(id) ON DELETE CASCADE,
            FOREIGN KEY(usluga_id) REFERENCES uslugi_dodatkowe(id) ON DELETE RESTRICT
        );
    """)

    # -------------------------
    # pracownicy
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pracownicy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            haslo_hash TEXT NOT NULL,
            imie TEXT NOT NULL,
            nazwisko TEXT NOT NULL,
            aktywny INTEGER DEFAULT 1,
            data_utworzenia TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pracownicy_email ON pracownicy(email);")

    # -------------------------
    # role
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS role (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nazwa TEXT UNIQUE NOT NULL,
            can_view_invoices INTEGER DEFAULT 0,
            can_edit_companies INTEGER DEFAULT 0,
            can_manage_users INTEGER DEFAULT 0,
            can_view_finances INTEGER DEFAULT 0,
            can_export_data INTEGER DEFAULT 0,
            can_access_admin_settings INTEGER DEFAULT 0
        );
    """)
    # ensure role columns exist for older schemas
    _safe_alter_add_column(cur, "role", "can_view_invoices INTEGER DEFAULT 0")
    _safe_alter_add_column(cur, "role", "can_edit_companies INTEGER DEFAULT 0")
    _safe_alter_add_column(cur, "role", "can_manage_users INTEGER DEFAULT 0")
    _safe_alter_add_column(cur, "role", "can_view_finances INTEGER DEFAULT 0")
    _safe_alter_add_column(cur, "role", "can_export_data INTEGER DEFAULT 0")
    _safe_alter_add_column(cur, "role", "can_access_admin_settings INTEGER DEFAULT 0")

    cur.execute("INSERT OR IGNORE INTO role (id, nazwa) VALUES (1, 'księgowa');")
    cur.execute("INSERT OR IGNORE INTO role (id, nazwa) VALUES (2, 'kadrowa');")
    cur.execute("INSERT OR IGNORE INTO role (id, nazwa) VALUES (3, 'manager');")
    cur.execute("INSERT OR IGNORE INTO role (id, nazwa) VALUES (4, 'admin');")
    cur.execute("INSERT OR IGNORE INTO role (id, nazwa) VALUES (5, 'opiekun');")

    # -------------------------
    # pracownik_firma
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pracownik_firma (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pracownik_id INTEGER NOT NULL,
            firma_id INTEGER NOT NULL,
            rola_id INTEGER,
            FOREIGN KEY(pracownik_id) REFERENCES pracownicy(id) ON DELETE CASCADE,
            FOREIGN KEY(firma_id) REFERENCES firmy(id) ON DELETE CASCADE,
            FOREIGN KEY(rola_id) REFERENCES role(id) ON DELETE SET NULL,
            UNIQUE(pracownik_id, firma_id, rola_id)
        );
    """)

    # -------------------------
    # company_notes
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS company_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firma_id INTEGER NOT NULL,
            tresc TEXT NOT NULL,
            data_utworzenia TEXT DEFAULT CURRENT_TIMESTAMP,
            autor_id INTEGER,
            FOREIGN KEY(firma_id) REFERENCES firmy(id) ON DELETE CASCADE,
            FOREIGN KEY(autor_id) REFERENCES pracownicy(id) ON DELETE SET NULL
        );
    """)

    # -------------------------
    # udzialowcy
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS udzialowcy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firma_id INTEGER NOT NULL,
            imie_nazwisko TEXT NOT NULL,
            procent REAL NOT NULL,
            FOREIGN KEY(firma_id) REFERENCES firmy(id) ON DELETE CASCADE
        );
    """)

    # -------------------------
    # company_contacts
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS company_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firma_id INTEGER NOT NULL,
            imie TEXT,
            nazwisko TEXT,
            email TEXT,
            telefon TEXT,
            rola TEXT,
            FOREIGN KEY(firma_id) REFERENCES firmy(id) ON DELETE CASCADE
        );
    """)

    # -------------------------
    # konta_email
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS konta_email (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pracownik_id INTEGER,
            email TEXT NOT NULL,
            haslo_enc TEXT,
            serwer_imap TEXT,
            port_imap INTEGER DEFAULT 993,
            ssl_imap TEXT DEFAULT 'SSL/TLS',
            serwer_smtp TEXT,
            port_smtp INTEGER DEFAULT 465,
            ssl_smtp TEXT DEFAULT 'SSL/TLS',
            aktywne INTEGER DEFAULT 1,
            FOREIGN KEY(pracownik_id) REFERENCES pracownicy(id) ON DELETE SET NULL
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_konta_email ON konta_email(email);")

    # -------------------------
    # foldery_email
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS foldery_email (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            konto_id INTEGER NOT NULL,
            nazwa TEXT NOT NULL,
            FOREIGN KEY(konto_id) REFERENCES konta_email(id) ON DELETE CASCADE
        );
    """)

    # -------------------------
    # wiadomosci_email
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS wiadomosci_email (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            konto_id INTEGER NOT NULL,
            folder_id INTEGER,
            uid TEXT,
            nadawca TEXT,
            odbiorca TEXT,
            temat TEXT,
            data TEXT,
            tresc TEXT,
            przeczytana INTEGER DEFAULT 0,
            usunieta INTEGER DEFAULT 0,
            FOREIGN KEY(konto_id) REFERENCES konta_email(id) ON DELETE CASCADE,
            FOREIGN KEY(folder_id) REFERENCES foldery_email(id) ON DELETE SET NULL
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_wiadomosci_email_uid ON wiadomosci_email(uid);")

    # -------------------------
    # email_attachments
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS email_attachments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wiadomosc_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            path TEXT,
            mime TEXT,
            data_dodania TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(wiadomosc_id) REFERENCES wiadomosci_email(id) ON DELETE CASCADE
        );
    """)

    # -------------------------
    # pracownik_konto_email
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pracownik_konto_email (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pracownik_id INTEGER NOT NULL,
            konto_id INTEGER NOT NULL,
            rola TEXT,
            FOREIGN KEY(pracownik_id) REFERENCES pracownicy(id) ON DELETE CASCADE,
            FOREIGN KEY(konto_id) REFERENCES konta_email(id) ON DELETE CASCADE,
            UNIQUE(pracownik_id, konto_id)
        );
    """)

    # -------------------------
    # reguly_filtrow
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS reguly_filtrow (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pracownik_id INTEGER NOT NULL,
            konto_id INTEGER NOT NULL,
            od TEXT,
            do TEXT,
            nadawca_zawiera TEXT,
            temat_zawiera TEXT,
            tresc_zawiera TEXT,
            folder_docelowy TEXT,
            oznacz_jako_przeczytane INTEGER DEFAULT 0,
            usun INTEGER DEFAULT 0,
            aktywna INTEGER DEFAULT 1,
            FOREIGN KEY(pracownik_id) REFERENCES pracownicy(id) ON DELETE CASCADE,
            FOREIGN KEY(konto_id) REFERENCES konta_email(id) ON DELETE CASCADE
        );
    """)

    # -------------------------
    # chat
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS konwersacje (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nazwa TEXT NOT NULL,
            pracownik_creator_id INTEGER,
            data_utworzenia TEXT DEFAULT CURRENT_TIMESTAMP,
            ostatnia_aktualizacja TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(pracownik_creator_id) REFERENCES pracownicy(id) ON DELETE SET NULL
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS uczestnicy_chat (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            konwersacja_id INTEGER NOT NULL,
            pracownik_id INTEGER NOT NULL,
            FOREIGN KEY(konwersacja_id) REFERENCES konwersacje(id) ON DELETE CASCADE,
            FOREIGN KEY(pracownik_id) REFERENCES pracownicy(id) ON DELETE CASCADE,
            UNIQUE(konwersacja_id, pracownik_id)
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS wiadomosci_chat (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            konwersacja_id INTEGER NOT NULL,
            pracownik_id INTEGER NOT NULL,
            tresc TEXT NOT NULL,
            data_wysylki TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(konwersacja_id) REFERENCES konwersacje(id) ON DELETE CASCADE,
            FOREIGN KEY(pracownik_id) REFERENCES pracownicy(id) ON DELETE CASCADE
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_wiadomosci_chat_konwersacja ON wiadomosci_chat(konwersacja_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_uczestnicy_chat_konwersacja ON uczestnicy_chat(konwersacja_id);")

    # -------------------------
    # faktury
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS faktury (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firma_id INTEGER NOT NULL,
            miesiac TEXT NOT NULL,

            kwota_netto REAL NOT NULL,
            kwota_brutto REAL NOT NULL,

            data_wystawienia TEXT,
            data_sprzedazy TEXT,
            termin_platnosci TEXT,

            metoda_kasowa TEXT DEFAULT '',
            wyslij_do_ksef TEXT DEFAULT '',
            wyslij_do_kontrahenta TEXT DEFAULT '',

            nabywca_skrocona TEXT,
            nabywca_pelna TEXT,
            nabywca_adres TEXT,
            nabywca_kod TEXT,
            nabywca_miasto TEXT,
            nabywca_nip TEXT,
            nabywca_regon TEXT,
            nabywca_kraj TEXT,
            nabywca_email TEXT,

            odbiorca_skrocona TEXT,
            odbiorca_pelna TEXT,
            odbiorca_rola TEXT DEFAULT 'Odbiorca',
            odbiorca_adres TEXT,
            odbiorca_kod TEXT,
            odbiorca_miasto TEXT,
            odbiorca_nip TEXT,
            odbiorca_regon TEXT,
            odbiorca_kraj TEXT,
            odbiorca_email TEXT,

            wystawca TEXT,

            konto_bankowe TEXT,

            gtu TEXT DEFAULT '',

            pozycje_json TEXT NOT NULL,

            eksportowana INTEGER DEFAULT 0,
            data_eksportu TEXT,

            status TEXT DEFAULT '',

            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY(firma_id) REFERENCES firmy(id) ON DELETE CASCADE,
            UNIQUE(firma_id, miesiac)
        );
    """)

    # faktury_pozycje - zgodne z DAO (invoice_id) oraz kompatybilność z faktura_id
    cur.execute("""
        CREATE TABLE IF NOT EXISTS faktury_pozycje (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER,          -- preferowana nazwa używana w DAO
            faktura_id INTEGER,         -- starsze nazewnictwo / kompatybilność
            usluga_id INTEGER,
            nazwa TEXT,
            opis TEXT,
            ilosc REAL DEFAULT 1,
            jednostka TEXT,
            cena_netto REAL,
            cena_brutto REAL,
            stawka_vat TEXT,
            pkwiu TEXT,
            gtu TEXT,
            wartosc_netto REAL,
            wartosc_brutto REAL,
            generated INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(invoice_id) REFERENCES faktury(id) ON DELETE CASCADE,
            FOREIGN KEY(faktura_id) REFERENCES faktury(id) ON DELETE CASCADE,
            FOREIGN KEY(usluga_id) REFERENCES uslugi_dodatkowe(id) ON DELETE SET NULL
        );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_faktury_firma_miesiac ON faktury(firma_id, miesiac);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_faktury_updated_at ON faktury(updated_at);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_faktury_pozycje_faktura ON faktury_pozycje(invoice_id);")

    # -------------------------
    # tags
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nazwa TEXT UNIQUE NOT NULL
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS tag_map (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tag_id INTEGER NOT NULL,
            tabela TEXT NOT NULL,
            rekord_id INTEGER NOT NULL,
            FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE CASCADE
        );
    """)

    # -------------------------
    # logs, settings, sessions, audit
    # -------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS system_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            poziom TEXT,
            modul TEXT,
            wiadomosc TEXT,
            data TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            klucz TEXT PRIMARY KEY,
            wartosc TEXT
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pracownik_id INTEGER,
            token TEXT,
            data_utworzenia TEXT DEFAULT CURRENT_TIMESTAMP,
            data_wygasniecia TEXT,
            FOREIGN KEY(pracownik_id) REFERENCES pracownicy(id) ON DELETE CASCADE
        );
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tabela TEXT,
            rekord_id INTEGER,
            akcja TEXT,
            uzytkownik_id INTEGER,
            data TEXT DEFAULT CURRENT_TIMESTAMP,
            szczegoly TEXT
        );
    """)

    # -------------------------
    # indexes (some already created above)
    # -------------------------
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pracownicy_email ON pracownicy(email);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_firmy_nip ON firmy(nip);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_wiadomosci_email_uid ON wiadomosci_email(uid);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_faktury_firma_miesiac ON faktury(firma_id, miesiac);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_faktury_updated_at ON faktury(updated_at);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_faktury_pozycje_faktura ON faktury_pozycje(invoice_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cenniki_klucz ON cenniki(klucz);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cenniki_kadry_klucz ON cenniki_kadry(klucz);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_konta_email ON konta_email(email);")

    # -------------------------
    # initial data
    # -------------------------
    cur.execute("SELECT COUNT(*) AS cnt FROM pracownicy")
    cnt_row = cur.fetchone()
    cnt = cnt_row["cnt"] if cnt_row else 0

    if cnt == 0:
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

        cur.execute("""
                UPDATE role
                SET can_view_invoices=1,
                    can_edit_companies=1,
                    can_manage_users=1,
                    can_view_finances=1,
                    can_export_data=1,
                    can_access_admin_settings=1
                WHERE id = 4
            """)

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
