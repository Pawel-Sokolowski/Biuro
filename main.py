import sys
import sqlite3
import datetime, time
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QListWidget, QStackedWidget, QLabel,
                             QTableWidget, QTableWidgetItem, QHeaderView,
                             QPushButton, QLineEdit, QFormLayout, QGridLayout,
                             QSplitter, QComboBox, QMessageBox, QFileDialog,
                             QDialog, QDialogButtonBox, QProgressDialog, QTextEdit,
                             QTabWidget, QCheckBox, QSpinBox, QListWidgetItem,
                             QGroupBox, QAbstractItemView, QScrollArea)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QFont
import openpyxl
import bcrypt
import requests
import json
import imaplib
import smtplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from cryptography.fernet import Fernet
import base64
from PyQt6.QtCore import QRunnable, QThreadPool

# Worker signals (komunikacja z GUI)
class _SyncWorkerSignals(QObject):
    finished = pyqtSignal()
    error = pyqtSignal(str)

class _SyncRunnable(QRunnable):
    def __init__(self, parent):
        super().__init__()
        self.parent = parent
        self.signals = _SyncWorkerSignals()

    def run(self):
        try:
            # Wywołaj metodę synchronizacji z instancji (powinna być bezpieczna do uruchomienia w tle)
            self.parent.sync_all_emails()
            self.signals.finished.emit()
        except Exception as e:
            self.signals.error.emit(str(e))
            self.signals.finished.emit()


DB_NAME = "biuro.db"

# Klucz szyfrowania haseł (powinien być w zmiennych środowiskowych w produkcji)
# Generuje się za pomocą: Fernet.generate_key()
ENCRYPTION_KEY = b'Qa6bILYFSFYxYL2wAskbJN29N3FOnJb0zcqMwXrgtXM='
cipher_suite = Fernet(ENCRYPTION_KEY)


# --- FUNKCJE SZYFROWANIA HASEŁ EMAIL ---
def encrypt_password(password):
    """Zaszyfruj hasło email"""
    return cipher_suite.encrypt(password.encode()).decode()


def decrypt_password(encrypted_password):
    """Odszyfruj hasło email"""
    return cipher_suite.decrypt(encrypted_password.encode()).decode()


# --- FUNKCJE AUTENTYKACJI ---
def hash_password(password):
    """Hashuj hasło za pomocą bcrypt"""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(password, password_hash):
    """Zweryfikuj hasło - bezpieczne dla typów str i bytes"""
    p_bytes = password.encode('utf-8') if isinstance(password, str) else password
    h_bytes = password_hash.encode('utf-8') if isinstance(password_hash, str) else password_hash
    return bcrypt.checkpw(p_bytes, h_bytes)


def login_user(email, password):
    """Zaloguj użytkownika - zwróć ID pracownika lub None"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, haslo_hash FROM pracownicy WHERE email = ? AND aktywny = 1", (email,))
        result = cursor.fetchone()
        if result and verify_password(password, result[1]):
            return result[0]
        return None
    finally:
        conn.close()


def register_user(email, password, imie, nazwisko):
    """Zarejestruj nowego pracownika"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        password_hash = hash_password(password)
        cursor.execute("""
            INSERT INTO pracownicy (email, haslo_hash, imie, nazwisko)
            VALUES (?, ?, ?, ?)
        """, (email, password_hash, imie, nazwisko))
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


# --- MIDDLEWARE AUTORYZACJI ---
def get_employee_info(pracownik_id):
    """Pobierz informacje o pracowniku"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, email, imie, nazwisko FROM pracownicy WHERE id = ?", (pracownik_id,))
        return cursor.fetchone()
    finally:
        conn.close()


def get_employee_assigned_companies(pracownik_id):
    """Pobierz listę firm przypisanych do pracownika"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT DISTINCT f.id, f.nazwa, f.nip, r.nazwa as rola
            FROM firmy f
            JOIN pracownik_firma pf ON f.id = pf.firma_id
            JOIN role r ON pf.rola_id = r.id
            WHERE pf.pracownik_id = ?
            ORDER BY f.nazwa
        """, (pracownik_id,))
        return cursor.fetchall()
    finally:
        conn.close()


def get_employee_role(pracownik_id):
    """Pobierz role pracownika"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT DISTINCT r.nazwa
            FROM role r
            JOIN pracownik_firma pf ON r.id = pf.rola_id
            WHERE pf.pracownik_id = ?
        """, (pracownik_id,))
        roles = cursor.fetchall()
        return [role[0] for role in roles]
    finally:
        conn.close()


def is_admin(pracownik_id):
    """Sprawdź czy pracownik jest adminem"""
    roles = get_employee_role(pracownik_id)
    return 'admin' in roles


# --- INTEGRACJA CEIDG API ---
import requests
from app.companies.ceidg_api import search_ceidg_by_nip, search_ceidg_by_name
from app.companies.importers import import_companies_from_excel_file, import_cennik_from_excel, _get_cell, parse_num
from app.companies.companies_model import ensure_columns, ensure_cenniki_table_and_columns, get_role_id, get_company_assigned_employee_ids, update_company_employee_assignments, serialize_company_payload, apply_default_main_pricing, save_company
from app.companies.constants import COMPANY_EXTRA_COLUMNS, COMPANY_FIELD_ORDER, COMPANY_IMPORT_HEADER_MAP, COMPANY_STAGE_OPTIONS
from app.utils.parsing import normalize_identifier, normalize_pkd_codes, parse_date_value, parse_int_value, split_multi_value, resolve_employee_ids

CEIDG_API_KEY = "TWÓJ_KLUCZ_API"
CEIDG_BASE_URL = "https://api-prod.ceidg.gov.pl/api/v2"







































































# --- FUNKCJE OBSŁUGI EMAIL (IMAP/SMTP) ---

class EmailSyncer:
    """Klasa do synchronizacji emaili z serwera IMAP"""

    @staticmethod
    def connect_imap(serwer_imap, port_imap, email_address, encrypted_password):
        """Połącz się z serwerem IMAP"""
        try:
            password = decrypt_password(encrypted_password)
            # Wymuszenie bezpiecznego połączenia IMAP przez SSL (port 993)
            server = imaplib.IMAP4_SSL(serwer_imap, int(port_imap))
            server.login(email_address, password)
            return server
        except Exception as e:
            print(f"Błąd połączenia IMAP: {e}")
            return None

    @staticmethod
    def sync_emails(konto_id, pracownik_id):
        """Synchronizuj emaile z IMAP do bazy"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        try:
            # Pobierz dane konta email
            cursor.execute("""
                SELECT email, serwer_imap, port_imap, haslo_enc FROM konta_email WHERE id = ?
            """, (konto_id,))
            account = cursor.fetchone()

            if not account:
                print(f"Konto email {konto_id} nie znalezione")
                return False

            email_addr, imap_server, imap_port, encrypted_pwd = account

            # Połącz się z IMAP
            imap = EmailSyncer.connect_imap(imap_server, imap_port, email_addr, encrypted_pwd)
            if not imap:
                return False

            # Pracujemy na uniwersalnym INBOX, żeby uniknąć problemów z lokalnymi
            # nazwami folderów i kodowaniem UTF-7 po stronie IMAP.
            status, _ = imap.select("INBOX", readonly=True)
            if status != "OK":
                print("Nie udało się otworzyć folderu INBOX")
                imap.logout()
                return False

            status, message_nums = imap.search(None, "ALL")
            if status != "OK" or not message_nums or not message_nums[0]:
                imap.logout()
                return True

            for num in message_nums[0].split()[-50:]:
                try:
                    status, msg_data = imap.fetch(num, "(RFC822)")
                    if status != "OK" or not msg_data or not msg_data[0]:
                        continue

                    msg = email.message_from_bytes(msg_data[0][1])

                    message_id = msg.get("Message-ID", f"unknown_{num.decode()}_{datetime.datetime.now().timestamp()}")
                    od = msg.get("From", "Unknown")
                    do = msg.get("To", "")
                    cc = msg.get("Cc", "")
                    temat = msg.get("Subject", "(brak tematu)")
                    data = msg.get("Date", datetime.datetime.now().isoformat())

                    try:
                        decoded_temat, encoding = email.header.decode_header(temat)[0]
                        if isinstance(decoded_temat, bytes):
                            temat = decoded_temat.decode(encoding or "utf-8", errors="ignore")
                    except Exception:
                        pass

                    tresc = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
                                payload = part.get_payload(decode=True) or b""
                                tresc = payload.decode("utf-8", errors="ignore")
                                break
                    else:
                        payload = msg.get_payload(decode=True) or b""
                        tresc = payload.decode("utf-8", errors="ignore")

                    final_folder = "INBOX"
                    cursor.execute("""
                        SELECT folder_docelowy FROM reguly_filtrow
                        WHERE konto_id = ? AND aktywna = 1
                        AND (od IS NULL OR od LIKE ?)
                        AND (temat_zawiera IS NULL OR ? LIKE '%' || temat_zawiera || '%')
                    """, (konto_id, str(od), str(temat)))
                    filter_result = cursor.fetchone()
                    if filter_result:
                        final_folder = filter_result[0]

                    try:
                        cursor.execute("""
                            INSERT INTO wiadomosci
                            (konto_id, message_id, od, do, cc, temat, tresc, data_otrzymania, folder)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (konto_id, str(message_id), str(od), str(do), str(cc), str(temat), str(tresc)[:490], str(data),
                              str(final_folder)))
                    except sqlite3.IntegrityError:
                        pass
                except Exception as msg_err:
                    print(f"Pominięto pojedynczą wiadomość: {msg_err}")
                    continue

            try:
                imap.close()
            except Exception:
                pass
            imap.logout()
            conn.commit()
            return True

        except Exception as e:
            print(f"Błąd synchronizacji email: {e}")
            return False
        finally:
            conn.close()

    @staticmethod
    def send_email(konto_id, do, temat, tresc):
        """Wyślij email"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        try:
            # Pobierz dane konta
            cursor.execute("""
                SELECT email, serwer_smtp, port_smtp, haslo_enc FROM konta_email WHERE id = ?
            """, (konto_id,))
            account = cursor.fetchone()

            if not account:
                return False, "Konto nie znalezione"

            email_addr, smtp_server, smtp_port, encrypted_pwd = account
            password = decrypt_password(encrypted_pwd)

            # Utwórz wiadomość
            msg = MIMEMultipart()
            msg['From'] = email_addr
            msg['To'] = do
            msg['Subject'] = temat
            msg.attach(MIMEText(tresc, 'plain'))

            # Wybór odpowiedniego protokołu szyfrowania w zależności od portu SMTP
            if int(smtp_port) == 465:
                # Pełne SSL od początku połączenia
                with smtplib.SMTP_SSL(smtp_server, int(smtp_port)) as server:
                    server.login(email_addr, password)
                    server.send_message(msg)
            else:
                # Standardowy port 587 z bezpiecznym przejściem na STARTTLS
                with smtplib.SMTP(smtp_server, int(smtp_port)) as server:
                    server.starttls()
                    server.login(email_addr, password)
                    server.send_message(msg)

            return True, "Wiadomość wysłana"

        except Exception as e:
            return False, f"Błąd wysyłania: {str(e)}"
        finally:
            conn.close()


# --- STRUKTURA BAZY DANYCH ---
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
            rola_id INTEGER NOT NULL,
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

    # Tabela wiadomości email
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

    # === AUTOMATYCZNE TWORZENIE PIERWSZEGO ADMINISTRATORA ===
    cursor.execute("SELECT COUNT(*) FROM pracownicy")
    if cursor.fetchone()[0] == 0:
        domyslne_haslo_hash = bcrypt.hashpw("admin123".encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        cursor.execute("""
            INSERT INTO pracownicy (id, email, haslo_hash, imie, nazwisko, aktywny)
            VALUES (1, 'admin@biuro.pl', ?, 'Glowny', 'Administrator', 1)
        """, (domyslne_haslo_hash,))

        cursor.execute("""
            INSERT OR IGNORE INTO pracownik_firma (pracownik_id, firma_id, rola_id)
            VALUES (1, 1, 4)
        """)

    conn.commit()
    conn.close()


# --- OKNO LOGOWANIA ---
class LoginWindow(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Logowanie - System Biura Rachunkowego")
        self.setFixedSize(400, 300)
        self.pracownik_id = None

        # Centruj okno
        self.move(100, 100)

        layout = QVBoxLayout()
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(15)

        # Tytuł
        lbl_title = QLabel("Zaloguj się do systemu")
        lbl_title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        layout.addWidget(lbl_title)

        # Formularz
        form_layout = QFormLayout()
        form_layout.setSpacing(10)

        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("Email")
        form_layout.addRow("Email:", self.email_input)

        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Hasło")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        form_layout.addRow("Hasło:", self.password_input)

        layout.addLayout(form_layout)

        # Przyciski
        btn_layout = QHBoxLayout()
        btn_login = QPushButton("Zaloguj")
        btn_login.clicked.connect(self.login)
        btn_login.setStyleSheet("background-color: #3498db; color: white; padding: 8px; border-radius: 4px; font-weight: bold;")

        btn_register = QPushButton("Zarejestruj")
        btn_register.clicked.connect(self.show_register_dialog)
        btn_register.setStyleSheet("background-color: #27ae60; color: white; padding: 8px; border-radius: 4px; font-weight: bold;")

        btn_layout.addWidget(btn_login)
        btn_layout.addWidget(btn_register)
        layout.addLayout(btn_layout)

        self.setLayout(layout)
        self.setStyleSheet("QDialog { background-color: #f8f9fa; }")

    def login(self):
        email = self.email_input.text().strip()
        password = self.password_input.text()

        if not email or not password:
            QMessageBox.warning(self, "Błąd", "Uzupełnij email i hasło")
            return

        pracownik_id = login_user(email, password)
        if pracownik_id:
            self.pracownik_id = pracownik_id
            self.accept()
        else:
            QMessageBox.critical(self, "Błąd", "Nieprawidłowy email lub hasło")
            self.password_input.clear()

    def show_register_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Rejestracja nowego pracownika")
        dialog.setFixedSize(400, 300)

        layout = QVBoxLayout()
        form = QFormLayout()

        email_field = QLineEdit()
        imie_field = QLineEdit()
        nazwisko_field = QLineEdit()
        haslo_field = QLineEdit()
        haslo_field.setEchoMode(QLineEdit.EchoMode.Password)

        form.addRow("Email:", email_field)
        form.addRow("Imię:", imie_field)
        form.addRow("Nazwisko:", nazwisko_field)
        form.addRow("Hasło:", haslo_field)

        layout.addLayout(form)

        # Przyciski
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(dialog.accept)
        btn_box.rejected.connect(dialog.reject)
        layout.addWidget(btn_box)

        dialog.setLayout(layout)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            email = email_field.text().strip()
            imie = imie_field.text().strip()
            nazwisko = nazwisko_field.text().strip()
            haslo = haslo_field.text()

            if not all([email, imie, nazwisko, haslo]):
                QMessageBox.warning(self, "Błąd", "Uzupełnij wszystkie pola")
                return

            user_id = register_user(email, haslo, imie, nazwisko)
            if user_id:
                QMessageBox.information(self, "Sukces", "Rejestracja przebiegła pomyślnie. Zaloguj się teraz.")
            else:
                QMessageBox.critical(self, "Błąd", "Ten email już jest zarejestrowany")


class CompanyDialog(QDialog):
    def __init__(self, pracownik_id, company_data=None, parent=None):
        super().__init__(parent)
        self.company_data = company_data or {}
        self.pracownik_id = pracownik_id
        self.employee_items = []
        self.setWindowTitle("Dodaj firmę" if not company_data else "Edytuj firmę")
        self.resize(980, 820)
        self.setMinimumSize(900, 720)
        self.setStyleSheet("""
            QDialog {
                background-color: #202428;
                color: #f2f2f2;
            }
            QGroupBox {
                border: 1px solid #495057;
                border-radius: 8px;
                margin-top: 12px;
                padding: 14px 12px 12px 12px;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
                color: #7fd6d2;
            }
            QLabel[sectionHint="true"] {
                color: #b6bdc6;
                font-size: 11px;
                margin-bottom: 4px;
            }
            QLabel[fieldLabel="true"] {
                color: #cfd5db;
                font-size: 11px;
            }
            QLineEdit, QSpinBox, QListWidget {
                background-color: #171a1f;
                border: 1px solid #3e4650;
                border-radius: 4px;
                padding: 6px 8px;
                color: #f2f2f2;
            }
            QLineEdit[required="true"] {
                border-color: #57bfc0;
            }
            QScrollArea {
                border: none;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        title = QLabel("Edycja danych firmy" if company_data else "Dodawanie firmy")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        main_layout.addWidget(title)

        subtitle = QLabel("Pola zostały pogrupowane tak, aby szybciej uzupełnić identyfikatory, kontakt, adresy i cenniki.")
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #aeb6bf;")
        main_layout.addWidget(subtitle)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(14)

        self.inputs = {}

        identifiers_group, identifiers_grid = self._create_section(
            "Identyfikatory",
            "Kluczowe numery rejestrowe i dane formalne firmy."
        )
        self._add_grid_line_edit(identifiers_grid, 0, 0, "NIP", "nip")
        self._add_grid_line_edit(identifiers_grid, 0, 1, "REGON", "regon")
        self._add_grid_line_edit(identifiers_grid, 1, 0, "KRS", "krs")
        self._add_grid_line_edit(identifiers_grid, 1, 1, "PESEL", "pesel")
        content_layout.addWidget(identifiers_group)

        basic_group, basic_grid = self._create_section(
            "Podstawowe informacje",
            "Najważniejsze dane opisowe i podatkowe widoczne od razu po otwarciu firmy."
        )
        self._add_grid_line_edit(basic_grid, 0, 0, "Nazwa firmy", "nazwa", required=True)
        self._add_grid_line_edit(basic_grid, 0, 1, "Nazwa skrócona", "nazwa_skrocona")
        self._add_grid_line_edit(basic_grid, 1, 0, "Forma działalności", "forma_dzialalnosci")
        self._add_grid_line_edit(basic_grid, 1, 1, "Forma opodatkowania", "forma_opodatkowania")
        self._add_grid_line_edit(basic_grid, 2, 0, "Analityka FK", "analityka_fk")
        content_layout.addWidget(basic_group)

        contact_group, contact_grid = self._create_section(
            "Kontakt",
            "Osoba kontaktowa oraz podstawowe kanały komunikacji z klientem."
        )
        self._add_grid_line_edit(contact_grid, 0, 0, "Osoba kontaktowa", "osoba_kontaktowa")
        self._add_grid_line_edit(contact_grid, 0, 1, "Adres email", "adres_email")
        self._add_grid_line_edit(contact_grid, 1, 0, "Nr telefonu", "nr_telefonu")
        content_layout.addWidget(contact_group)

        main_address_group, main_address_grid = self._create_section("Adres główny")
        self._add_grid_line_edit(main_address_grid, 0, 0, "Miasto", "miasto")
        self._add_grid_line_edit(main_address_grid, 0, 1, "Kod pocztowy", "kod_pocztowy")
        self._add_grid_line_edit(main_address_grid, 1, 0, "Ulica", "ulica", col_span=2)
        content_layout.addWidget(main_address_group)

        address_row = QWidget()
        address_row_layout = QHBoxLayout(address_row)
        address_row_layout.setContentsMargins(0, 0, 0, 0)
        address_row_layout.setSpacing(14)

        home_address_group, home_address_grid = self._create_section("Adres zamieszkania")
        self._add_grid_line_edit(home_address_grid, 0, 0, "Miasto", "adres_zam_miasto")
        self._add_grid_line_edit(home_address_grid, 0, 1, "Kod pocztowy", "adres_zam_kod_pocztowy")
        self._add_grid_line_edit(home_address_grid, 1, 0, "Ulica", "adres_zam_ulica", col_span=2)
        address_row_layout.addWidget(home_address_group)

        correspondence_group, correspondence_grid = self._create_section("Adres korespondencyjny")
        self._add_grid_line_edit(correspondence_grid, 0, 0, "Miasto", "adres_koresp_miasto")
        self._add_grid_line_edit(correspondence_grid, 0, 1, "Kod pocztowy", "adres_koresp_kod_pocztowy")
        self._add_grid_line_edit(correspondence_grid, 1, 0, "Ulica", "adres_koresp_ulica", col_span=2)
        address_row_layout.addWidget(correspondence_group)

        content_layout.addWidget(address_row)

        dates_group, dates_grid = self._create_section(
            "Daty i ZUS",
            "Daty działalności i zakończenia poszczególnych ulg."
        )
        date_fields = [
            ("Data rozpoczęcia działalności", "data_rozpoczecia_dg"),
            ("Data rozpoczęcia sprzedaży", "data_rozpoczecia_sprzedazy"),
            ("Koniec ulgi ZUS na start", "data_koniec_zus_start"),
            ("Koniec ZUS preferencyjny", "data_koniec_zus_pref"),
            ("Koniec małego ZUS PLUS", "data_koniec_maly_zus"),
            ("Koniec A1", "data_koniec_a1"),
            ("Data rozpoczęcia obsługi", "data_rozpoczecia_obslugi"),
            ("Data zakończenia obsługi", "data_zakonczenia_obslugi"),
        ]
        for index, (label_text, field_name) in enumerate(date_fields):
            self._add_grid_line_edit(
                dates_grid,
                index // 2,
                index % 2,
                label_text,
                field_name,
                placeholder="YYYY-MM-DD"
            )
        self._add_grid_line_edit(
            dates_grid,
            len(date_fields) // 2,
            0,
            "Zawieszenia działalności",
            "zawieszenia_dzialalnosci",
            col_span=2
        )
        content_layout.addWidget(dates_group)

        business_group, business_grid = self._create_section(
            "PKD, cenniki i zatrudnienie",
            "Układ zbliżony do roboczego formularza: kody, limity i stawki w jednym miejscu."
        )
        self._add_grid_line_edit(
            business_grid,
            0,
            0,
            "Kody PKD",
            "kody_pkd",
            placeholder="62.01.Z, 69.20.Z",
            col_span=2
        )
        self._add_grid_line_edit(business_grid, 1, 0, "Cennik usług księgowych", "cennik_ksiegowy")
        self._add_grid_line_edit(business_grid, 1, 1, "Cennik usług kadrowych", "cennik_kadrowy")
        self._add_grid_line_edit(business_grid, 2, 0, "Cennik czasowy", "cennik_czasowy")
        self._add_grid_line_edit(business_grid, 2, 1, "Główny cennik", "glowny_cennik")

        self.main_price_manual_checkbox = QCheckBox("Ręcznie nadpisz główny cennik")
        self.main_price_manual_checkbox.setChecked(bool(self.company_data.get("glowny_cennik_manualny", 0)))
        self.main_price_manual_checkbox.toggled.connect(self._toggle_main_price_mode)
        business_grid.addWidget(self.main_price_manual_checkbox, 3, 0, 1, 2)

        self._add_grid_spin_box(business_grid, 4, 0, "Limit dokumentów", "limit_dokumentow")
        self._add_grid_spin_box(business_grid, 4, 1, "Liczba pracowników", "liczba_pracownikow")
        self._add_grid_spin_box(business_grid, 5, 0, "Pracownicy UoP", "uop")
        self._add_grid_spin_box(business_grid, 5, 1, "Umowy zlecenia", "uz")
        self._add_grid_spin_box(business_grid, 6, 0, "Umowy o dzieło", "uod")
        content_layout.addWidget(business_group)

        employee_group, employee_grid = self._create_section(
            "Przypisani pracownicy",
            "Wybierz osoby odpowiedzialne za obsługę tej firmy."
        )
        self.employee_list = QListWidget()
        self.employee_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.employee_list.setMinimumHeight(120)
        employee_grid.addWidget(self.employee_list, 0, 0, 1, 2)
        content_layout.addWidget(employee_group)
        content_layout.addStretch(1)

        scroll_area.setWidget(content_widget)
        main_layout.addWidget(scroll_area, 1)

        self._load_employees()
        self._toggle_main_price_mode(self.main_price_manual_checkbox.isChecked())

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.validate_and_accept)
        buttons.rejected.connect(self.reject)
        main_layout.addWidget(buttons)

    def _create_section(self, title, description=""):
        section = QGroupBox(title)
        layout = QVBoxLayout(section)
        layout.setSpacing(10)
        if description:
            hint = QLabel(description)
            hint.setProperty("sectionHint", True)
            hint.setWordWrap(True)
            layout.addWidget(hint)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        return section, grid

    def _create_text_input(self, field_name, placeholder="", required=False):
        input_widget = QLineEdit()
        input_widget.setText(str(self.company_data.get(field_name, "")))
        if placeholder:
            input_widget.setPlaceholderText(placeholder)
        if required:
            input_widget.setProperty("required", True)
        self.inputs[field_name] = input_widget
        return input_widget

    def _create_spin_input(self, field_name):
        spin_box = QSpinBox()
        spin_box.setMaximum(999999)
        spin_box.setValue(parse_int_value(self.company_data.get(field_name), 0))
        self.inputs[field_name] = spin_box
        return spin_box

    def _create_field_container(self, label_text, widget):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(label_text)
        label.setProperty("fieldLabel", True)
        layout.addWidget(label)
        layout.addWidget(widget)
        return container

    def _add_grid_line_edit(self, grid_layout, row, column, label_text, field_name, placeholder="", required=False, col_span=1):
        input_widget = self._create_text_input(field_name, placeholder=placeholder, required=required)
        field_container = self._create_field_container(label_text, input_widget)
        grid_layout.addWidget(field_container, row, column, 1, col_span)

    def _add_grid_spin_box(self, grid_layout, row, column, label_text, field_name, col_span=1):
        spin_box = self._create_spin_input(field_name)
        field_container = self._create_field_container(label_text, spin_box)
        grid_layout.addWidget(field_container, row, column, 1, col_span)

    def _toggle_main_price_mode(self, manual_enabled):
        self.inputs["glowny_cennik"].setReadOnly(not manual_enabled)
        if not manual_enabled and not self.inputs["glowny_cennik"].text().strip():
            self.inputs["glowny_cennik"].setPlaceholderText("Zostanie dobrany po przekroczeniu limitu dokumentów")
        else:
            self.inputs["glowny_cennik"].setPlaceholderText("")

    def _load_employees(self):
        selected_ids = set(self.company_data.get("assigned_employee_ids", []))
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, imie, nazwisko, email FROM pracownicy WHERE aktywny = 1 ORDER BY imie, nazwisko")
        for employee_id, first_name, last_name, email_address in cursor.fetchall():
            item = QListWidgetItem(f"{first_name} {last_name} ({email_address})")
            item.setData(Qt.ItemDataRole.UserRole, employee_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if employee_id in selected_ids else Qt.CheckState.Unchecked)
            self.employee_list.addItem(item)
        conn.close()

    def validate_and_accept(self):
        if not self.inputs["nazwa"].text().strip():
            QMessageBox.warning(self, "Błąd", "Nazwa firmy jest wymagana.")
            return

        if self.inputs["nip"].text().strip():
            normalized_nip = normalize_identifier(self.inputs["nip"].text(), {10})
            if len(normalized_nip) != 10:
                QMessageBox.warning(self, "Błąd", "NIP powinien zawierać 10 cyfr.")
                return

        self.accept()

    def get_payload(self):
        payload = {}
        for field_name, widget in self.inputs.items():
            if isinstance(widget, QSpinBox):
                payload[field_name] = widget.value()
            else:
                payload[field_name] = widget.text().strip()
        payload["glowny_cennik_manualny"] = 1 if self.main_price_manual_checkbox.isChecked() else 0
        payload["assigned_employee_ids"] = [
            self.employee_list.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(self.employee_list.count())
            if self.employee_list.item(index).checkState() == Qt.CheckState.Checked
        ]
        return payload


class OfficeManagerApp(QMainWindow):
    def __init__(self, pracownik_id=None):
        super().__init__()
        self.pracownik_id = pracownik_id  # ID zalogowanego pracownika

        self.setWindowTitle("System Zarządzania Biurem Rachunkowym v1.1")
        self.resize(1250, 750)

        # Zmienne stopera
        self.aktywna_firma_stoper = None
        self.aktywna_firma_stoper_nazwa = ""
        self.czas_start = None
        self.aktualny_miesiac = datetime.datetime.now().strftime("%Y-%m")
        self.status_autosave_timers = {}
        self.status_widgets = {}

        init_db()
        self.init_ui()

        # Główny stoper systemowy odświeżający GUI co 1 sekundę
        self.global_timer = QTimer()
        self.global_timer.timeout.connect(self.aktualizuj_stoper_gui)
        self.global_timer.start(1000)

    def init_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 1. PANEL NAVIGATION (BOCZNY)
        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(220)
        self.sidebar.setStyleSheet("""
            QListWidget { background-color: #2c3e50; color: #ecf0f1; font-size: 14px; border: none; }
            QListWidget::item { padding: 15px 10px; border-bottom: 1px solid #34495e; }
            QListWidget::item:selected { background-color: #3498db; color: white; font-weight: bold; }
            QListWidget::item:hover { background-color: #34495e; }
        """)
        self.sidebar.addItems(["Widok Miesięczny", "Klienci i Pracownicy", "Pracownicy i Uprawnienia", "Email", "Chat", "CEIDG Import", "Eksport Saldeo"])
        main_layout.addWidget(self.sidebar)

        # 2. SEKCJA STRONY (STACKED WIDGET)
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        global_header = QWidget()
        global_header.setStyleSheet("background-color: #f5f7fa; border-bottom: 1px solid #dfe6e9;")
        global_header_layout = QHBoxLayout(global_header)
        global_header_layout.setContentsMargins(16, 10, 16, 10)

        emp_info = get_employee_info(self.pracownik_id)
        employee_name = f"{emp_info[2]} {emp_info[3]}" if emp_info else "Nieznany"
        self.lbl_global_user = QLabel(f"<b>Zalogowany:</b> {employee_name}")
        self.lbl_global_user.setStyleSheet("color: #2c3e50;")
        global_header_layout.addWidget(self.lbl_global_user)

        self.lbl_global_timer_status = QLabel("Timer: brak aktywnej firmy")
        self.lbl_global_timer_status.setStyleSheet(
            "color: #2c3e50; font-weight: bold; background-color: #ecf0f1; padding: 6px 10px; border-radius: 4px;"
        )
        global_header_layout.addWidget(self.lbl_global_timer_status, alignment=Qt.AlignmentFlag.AlignRight)
        content_layout.addWidget(global_header)

        self.pages = QStackedWidget()
        content_layout.addWidget(self.pages)
        main_layout.addWidget(content_widget)

        # Inicjalizacja podstron
        self.create_monthly_view_page()
        self.create_companies_page()
        self.create_employees_page()
        self.create_email_page()
        self.create_chat_page()
        self.create_ceidg_page()
        self.create_saldeo_page()

        # Obsługa nawigacji
        self.sidebar.currentRowChanged.connect(self.zmien_zakladke)
        self.sidebar.setCurrentRow(0)

    def zmien_zakladke(self, index):
        self.pages.setCurrentIndex(index)
        if index == 0:
            self.refresh_monthly_table()
        elif index == 1:
            self.refresh_firms_table()
        elif index == 2:
            self.refresh_employees_table()
        elif index == 3:
            # Email page - refresh accounts
            self.refresh_email_accounts()
        elif index == 4:
            # Chat page - refresh conversations
            self.refresh_chat_conversations()
        elif index == 5:
            # CEIDG page - nothing to refresh
            pass

    # =========================================================================
    # WIDOK 1: WIDOK MIESIĘCZNY (ETAPY, DOKUMENTY, CZASOMIERZ)
    # =========================================================================
    def create_monthly_view_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)

        # Pasek statusu u góry strony
        header = QHBoxLayout()
        lbl_month = QLabel(f"Bieżący miesiąc rozliczeniowy: <b>{self.aktualny_miesiac}</b>")
        lbl_month.setFont(QFont("Arial", 14))
        header.addWidget(lbl_month)
        layout.addLayout(header)

        # Główna tabela stanu
        self.table_monthly = QTableWidget()
        self.table_monthly.setColumnCount(7)
        self.table_monthly.setHorizontalHeaderLabels([
            "ID", "Nazwa Firmy", "Ilość dokumentów", "Etap pracy", "Czas pracy (Suma)", "Stoper", "Akcja Saldeo"
        ])
        self.table_monthly.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_monthly.hideColumn(0)  # Ukrywamy kolumnę techniczną ID
        layout.addWidget(self.table_monthly)

        self.refresh_monthly_table()
        self.pages.addWidget(page)

    def refresh_monthly_table(self):
        self.table_monthly.setRowCount(0)
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        # Filtrowanie firm na podstawie uprawnień pracownika
        if is_admin(self.pracownik_id):
            # Admin widzi wszystkie firmy
            cursor.execute("""
                SELECT f.id, f.nazwa, f.sekundy_pracy, sm.etap, sm.ilosc_dok 
                FROM firmy f
                LEFT JOIN statusy_miesieczne sm ON f.id = sm.firma_id AND sm.miesiac = ?
                ORDER BY f.nazwa
            """, (self.aktualny_miesiac,))
        else:
            # Zwykły pracownik widzi tylko przypisane firmy
            cursor.execute("""
                SELECT f.id, f.nazwa, f.sekundy_pracy, sm.etap, sm.ilosc_dok 
                FROM firmy f
                LEFT JOIN statusy_miesieczne sm ON f.id = sm.firma_id AND sm.miesiac = ?
                WHERE f.id IN (
                    SELECT DISTINCT firma_id FROM pracownik_firma WHERE pracownik_id = ?
                )
                ORDER BY f.nazwa
            """, (self.aktualny_miesiac, self.pracownik_id))

        dane = cursor.fetchall()
        conn.close()

        kolory_etapow = {
            "Dostarczone": "#e67e22",
            "Komplet dokumentów": "#f1c40f",
            "Zaksięgowane": "#3498db",
            "Wysłane": "#2ecc71",
            "Brak": "#ffffff"
        }
        self.status_widgets = {}

        for row_idx, row in enumerate(dane):
            f_id, nazwa, sekundy, etap, ilosc_dok = row
            etap = etap if etap else "Brak"
            ilosc_dok = ilosc_dok if ilosc_dok else 0

            self.table_monthly.insertRow(row_idx)

            # ID i Nazwa
            self.table_monthly.setItem(row_idx, 0, QTableWidgetItem(str(f_id)))
            self.table_monthly.setItem(row_idx, 1, QTableWidgetItem(nazwa))

            # Komórka z edycją ilości dokumentów
            txt_docs = QLineEdit(str(ilosc_dok))
            txt_docs.setAlignment(Qt.AlignmentFlag.AlignCenter)
            txt_docs.setFixedWidth(70)
            self.table_monthly.setCellWidget(row_idx, 2, txt_docs)

            # Lista rozwijana etapów (ComboBox)
            cb_etap = QComboBox()

            # KRYTYCZNA POPRAWKA: Blokujemy sygnały przed przypisaniem wartości,
            # aby nie wywołać nieskończonej pętli zdarzeń w silniku C++ (crash 0xC0000409)
            cb_etap.blockSignals(True)
            cb_etap.addItems(COMPANY_STAGE_OPTIONS)
            cb_etap.setCurrentText(etap)
            cb_etap.setStyleSheet(f"background-color: {kolory_etapow.get(etap, '#fff')}; padding: 3px;")
            cb_etap.blockSignals(False) # Odblokowujemy sygnały po bezpiecznym ustawieniu stanu

            cb_etap.currentTextChanged.connect(lambda txt, cb=cb_etap: cb.setStyleSheet(
                f"background-color: {kolory_etapow.get(txt, '#fff')}; padding: 3px;"))
            cb_etap.currentTextChanged.connect(lambda _txt, fid=f_id: self.schedule_status_autosave(fid))
            txt_docs.textChanged.connect(lambda _txt, fid=f_id: self.schedule_status_autosave(fid))
            self.table_monthly.setCellWidget(row_idx, 3, cb_etap)
            self.status_widgets[f_id] = (txt_docs, cb_etap)

            # Formatowanie sumarycznego czasu pracy
            czas_format = str(datetime.timedelta(seconds=sekundy if sekundy else 0))
            lbl_time = QLabel(czas_format)
            lbl_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table_monthly.setCellWidget(row_idx, 4, lbl_time)

            # Przycisk sterowania stoperem
            btn_stoper = QPushButton("Start" if self.aktywna_firma_stoper != f_id else "Stop")
            if self.aktywna_firma_stoper == f_id:
                btn_stoper.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold;")
            else:
                btn_stoper.setStyleSheet("background-color: #2ecc71; color: white; font-weight: bold;")
            btn_stoper.clicked.connect(lambda checked, fid=f_id, r=row_idx: self.obsluga_stopera(fid, r))
            self.table_monthly.setCellWidget(row_idx, 5, btn_stoper)

            # Szybkie generowanie przykładowej faktury pod Saldeo
            btn_add_faktura = QPushButton("+Faktura")
            btn_add_faktura.setStyleSheet("background-color: #9b59b6; color: white;")
            btn_add_faktura.clicked.connect(lambda checked, fid=f_id: self.dodaj_fakture_okno(fid))
            self.table_monthly.setCellWidget(row_idx, 6, btn_add_faktura)

    def obsluga_stopera(self, firma_id, row_idx):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        if self.aktywna_firma_stoper is None:
            # Włączenie mierzenia czasu
            self.aktywna_firma_stoper = firma_id
            self.czas_start = datetime.datetime.now()
            cursor.execute("SELECT nazwa FROM firmy WHERE id=?", (firma_id,))
            res = cursor.fetchone()
            nazwa = res[0] if res else str(firma_id)
            self.aktywna_firma_stoper_nazwa = nazwa
            self.lbl_global_timer_status.setText(f"Timer: {nazwa}")
        elif self.aktywna_firma_stoper == firma_id:
            # Wyłączenie mierzenia czasu i zapis do bazy
            roznica_sekund = int((datetime.datetime.now() - self.czas_start).total_seconds())
            cursor.execute("UPDATE firmy SET sekundy_pracy = sekundy_pracy + ? WHERE id = ?", (roznica_sekund, firma_id))
            conn.commit()
            # Reset stopera
            self.aktywna_firma_stoper = None
            self.aktywna_firma_stoper_nazwa = ""
            self.czas_start = None
            self.lbl_global_timer_status.setText("Timer: brak aktywnej firmy")
        else:
            previous_company_id = self.aktywna_firma_stoper
            previous_delta = int((datetime.datetime.now() - self.czas_start).total_seconds())
            cursor.execute(
                "UPDATE firmy SET sekundy_pracy = sekundy_pracy + ? WHERE id = ?",
                (previous_delta, previous_company_id)
            )
            self.aktywna_firma_stoper = firma_id
            self.czas_start = datetime.datetime.now()
            cursor.execute("SELECT nazwa FROM firmy WHERE id=?", (firma_id,))
            res = cursor.fetchone()
            nazwa = res[0] if res else str(firma_id)
            self.aktywna_firma_stoper_nazwa = nazwa
            self.lbl_global_timer_status.setText(f"Timer: {nazwa}")
            conn.commit()

        conn.close()
        self.refresh_monthly_table()

    def aktualizuj_stoper_gui(self):
        if self.aktywna_firma_stoper is not None and self.czas_start is not None:
            roznica = int((datetime.datetime.now() - self.czas_start).total_seconds())
            self.lbl_global_timer_status.setText(
                f"Timer: {self.aktywna_firma_stoper_nazwa or f'firma #{self.aktywna_firma_stoper}'} od {roznica} sek."
            )
        elif hasattr(self, "lbl_global_timer_status"):
            self.lbl_global_timer_status.setText("Timer: brak aktywnej firmy")

    def schedule_status_autosave(self, firma_id):
        timer = self.status_autosave_timers.get(firma_id)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda fid=firma_id: self.zapisz_wiersz_statusu(fid, show_feedback=False))
            self.status_autosave_timers[firma_id] = timer
        timer.start(700)

    def zapisz_wiersz_statusu(self, firma_id, show_feedback=True):
        txt_widget, cb_widget = self.status_widgets.get(firma_id, (None, None))
        txt_docs = txt_widget.text() if txt_widget is not None else "0"
        cb_etap = cb_widget.currentText() if cb_widget is not None else "Brak"

        try:
            ilosc_dok = int(txt_docs)
        except ValueError:
            if show_feedback:
                QMessageBox.critical(self, "Błąd", "Pole ilość dokumentów musi posiadać wartość liczbową!")
            return

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO statusy_miesieczne (firma_id, miesiac, etap, ilosc_dok)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(firma_id, miesiac) DO UPDATE SET etap = excluded.etap, ilosc_dok = excluded.ilosc_dok
        """, (firma_id, self.aktualny_miesiac, cb_etap, ilosc_dok))
        apply_default_main_pricing(cursor, firma_id, ilosc_dok)
        conn.commit()
        conn.close()
        if show_feedback:
            QMessageBox.information(self, "Zapisano", "Stan firmy został zaktualizowany pomyślnie.")
        else:
            self.statusBar().showMessage("Status firmy zapisano automatycznie.", 1500)

    def dodaj_fakture_okno(self, firma_id):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        # Dynamiczne generowanie numeru dokumentu pod system Saldeo
        nr_faktury = f"FV/{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}/{firma_id}"

        cursor.execute(
            "INSERT INTO dokumenty (firma_id, numer, typ, cena_netto, data_wyst) VALUES (?, ?, ?, ?, ?)",
            (firma_id, nr_faktury, 'Usługa księgowa', 1500.00, datetime.datetime.now().strftime("%Y-%m-%d"))
        )

        # Automatyczne podbicie licznika dokumentów w tym miesiącu
        cursor.execute(
            """
            INSERT INTO statusy_miesieczne (firma_id, miesiac, ilosc_dok)
            VALUES (?, ?, 1)
            ON CONFLICT(firma_id, miesiac) DO UPDATE SET ilosc_dok = ilosc_dok + 1
            """,
            (firma_id, self.aktualny_miesiac)
        )
        cursor.execute("""
            SELECT ilosc_dok FROM statusy_miesieczne
            WHERE firma_id = ? AND miesiac = ?
        """, (firma_id, self.aktualny_miesiac))
        document_count = cursor.fetchone()
        apply_default_main_pricing(cursor, firma_id, document_count[0] if document_count else 0)

        conn.commit()
        conn.close()
        self.refresh_monthly_table()
        QMessageBox.information(self, "Dodano dokument", f"Utworzono fakturę próbną: {nr_faktury}\nTrafi ona bezpośrednio do raportu Saldeo.")

    # =========================================================================
    # WIDOK 2: KLIENCI I PRACOWNICY (KADRY: UoP, UZ, UoD)
    # =========================================================================
    def create_companies_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)

        header_layout = QHBoxLayout()
        header_layout.addWidget(QLabel("Lista firm i parametry obsługi"))
        btn_add_company = QPushButton("Dodaj firmę")
        btn_add_company.setStyleSheet("background-color: #3498db; color: white; font-weight: bold; padding: 8px 14px;")
        btn_add_company.clicked.connect(self.open_new_company_dialog)
        header_layout.addWidget(btn_add_company, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addLayout(header_layout)

        self.table_firms = QTableWidget()
        self.table_firms.setColumnCount(8)
        self.table_firms.setHorizontalHeaderLabels([
            "ID", "Nazwa firmy", "NIP", "Miasto", "Liczba pracowników", "Główny cennik", "Limit dokumentów", "Akcje"
        ])
        self.table_firms.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_firms.hideColumn(0)
        layout.addWidget(self.table_firms)
        self.pages.addWidget(page)

    def refresh_firms_table(self):
        self.table_firms.setRowCount(0)
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        # Filtrowanie firm na podstawie uprawnień
        if is_admin(self.pracownik_id):
            # Admin widzi wszystkie firmy
            cursor.execute("""
                SELECT id, nazwa, nip, miasto, liczba_pracownikow, glowny_cennik, limit_dokumentow
                FROM firmy
                ORDER BY nazwa
            """)
        else:
            # Zwykły pracownik widzi tylko przypisane firmy
            cursor.execute("""
                SELECT DISTINCT f.id, f.nazwa, f.nip, f.miasto, f.liczba_pracownikow, f.glowny_cennik, f.limit_dokumentow
                FROM firmy f
                WHERE f.id IN (
                    SELECT firma_id FROM pracownik_firma WHERE pracownik_id = ?
                )
                ORDER BY f.nazwa
            """, (self.pracownik_id,))

        for row_idx, row in enumerate(cursor.fetchall()):
            self.table_firms.insertRow(row_idx)
            company_id, company_name, nip, city, employee_count, main_price, document_limit = row
            self.table_firms.setItem(row_idx, 0, QTableWidgetItem(str(company_id)))
            self.table_firms.setItem(row_idx, 1, QTableWidgetItem(company_name or ""))
            self.table_firms.setItem(row_idx, 2, QTableWidgetItem(nip or ""))
            self.table_firms.setItem(row_idx, 3, QTableWidgetItem(city or ""))

            employee_spin = QSpinBox()
            employee_spin.setMaximum(999999)
            employee_spin.setValue(parse_int_value(employee_count, 0))
            employee_spin.valueChanged.connect(
                lambda value, company_id=company_id: self.update_company_employee_count(company_id, value)
            )
            self.table_firms.setCellWidget(row_idx, 4, employee_spin)

            self.table_firms.setItem(row_idx, 5, QTableWidgetItem(main_price or ""))
            self.table_firms.setItem(row_idx, 6, QTableWidgetItem(str(parse_int_value(document_limit, 0))))

            action_widget = QWidget()
            action_layout = QHBoxLayout(action_widget)
            action_layout.setContentsMargins(4, 0, 4, 0)
            btn_edit = QPushButton("Edytuj")
            btn_edit.setStyleSheet("background-color: #2ecc71; color: white;")
            btn_edit.clicked.connect(lambda checked, company_id=company_id: self.open_edit_company_dialog(company_id))
            action_layout.addWidget(btn_edit)
            self.table_firms.setCellWidget(row_idx, 7, action_widget)
        conn.close()

    def get_company_details(self, company_id):
        conn = sqlite3.connect(DB_NAME)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM firmy WHERE id = ?", (company_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return None
        company_data = dict(row)
        company_data["assigned_employee_ids"] = get_company_assigned_employee_ids(cursor, company_id)
        conn.close()
        return company_data

    def open_new_company_dialog(self):
        dialog = CompanyDialog(self.pracownik_id, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.save_company_to_db(dialog.get_payload())

    def open_edit_company_dialog(self, company_id):
        company_data = self.get_company_details(company_id)
        if not company_data:
            QMessageBox.warning(self, "Błąd", "Nie znaleziono firmy do edycji.")
            return
        dialog = CompanyDialog(self.pracownik_id, company_data=company_data, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.save_company_to_db(dialog.get_payload(), company_id=company_id)

    def save_company_to_db(self, payload, company_id=None):
        if not str(payload.get("nazwa", "")).strip():
            QMessageBox.warning(self, "Błąd", "Pole 'Nazwa firmy' jest wymagane.")
            return

        normalized_nip = normalize_identifier(payload.get("nip"), {10})
        if payload.get("nip") and len(normalized_nip) != 10:
            QMessageBox.warning(self, "Błąd", "Podany numer NIP jest nieprawidłowy.")
            return
        payload["nip"] = normalized_nip

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        try:
            final_company_id = save_company(cursor, payload, company_id=company_id)
            conn.commit()
            self.refresh_firms_table()
            self.refresh_employee_combos()
            QMessageBox.information(
                self,
                "Zapisano",
                "Dane firmy zostały zapisane." if company_id else f"Dodano nową firmę (ID: {final_company_id})."
            )
        except sqlite3.IntegrityError:
            QMessageBox.critical(self, "Błąd", "Podany numer NIP figuruje już w systemie!")
        finally:
            conn.close()

    def update_company_employee_count(self, company_id, value):
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE firmy SET liczba_pracownikow = ? WHERE id = ?", (value, company_id))
        conn.commit()
        conn.close()
        self.statusBar().showMessage("Zapisano liczbę pracowników.", 1200)

    # =========================================================================
    # WIDOK 3: PRACOWNICY I UPRAWNIENIA
    # =========================================================================
    def create_employees_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)

        # Tytuł
        lbl_title = QLabel("Zarządzanie pracownikami i uprawnieniami")
        lbl_title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        layout.addWidget(lbl_title)

        # Splitter - tabela po lewej, formularz po prawej
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # === LEWA STRONA - TABELA PRACOWNIKÓW ===
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.addWidget(QLabel("Pracownicy w systemie:"))

        self.table_employees = QTableWidget()
        self.table_employees.setColumnCount(6)
        self.table_employees.setHorizontalHeaderLabels(["ID", "Email", "Imię", "Nazwisko", "Aktywny", "Działania"])
        self.table_employees.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_employees.hideColumn(0)
        left_layout.addWidget(self.table_employees)

        # === PRAWA STRONA - FORMULARZ ===
        right_widget = QWidget()
        right_widget.setFixedWidth(350)
        right_layout = QVBoxLayout(right_widget)
        right_layout.addWidget(QLabel("Dodaj nowego pracownika"))

        form_layout = QFormLayout()
        self.emp_email = QLineEdit()
        self.emp_imie = QLineEdit()
        self.emp_nazwisko = QLineEdit()
        self.emp_haslo = QLineEdit()
        self.emp_haslo.setEchoMode(QLineEdit.EchoMode.Password)

        form_layout.addRow("Email:", self.emp_email)
        form_layout.addRow("Imię:", self.emp_imie)
        form_layout.addRow("Nazwisko:", self.emp_nazwisko)
        form_layout.addRow("Hasło:", self.emp_haslo)

        right_layout.addLayout(form_layout)

        btn_add_emp = QPushButton("Dodaj pracownika")
        btn_add_emp.setStyleSheet("background-color: #3498db; color: white; font-weight: bold; padding: 10px;")
        btn_add_emp.clicked.connect(self.add_employee)
        right_layout.addWidget(btn_add_emp)

        # === PRZYPISANIE DO FIRMY Z ROLĄ ===
        right_layout.addWidget(QLabel("Przypisz do firmy"))

        assign_form = QFormLayout()
        self.assign_employee_combo = QComboBox()
        self.assign_firma_combo = QComboBox()
        self.assign_rola_combo = QComboBox()
        self.assign_rola_combo.addItems(["księgowa", "kadrowa", "manager", "admin", "opiekun"])

        assign_form.addRow("Pracownik:", self.assign_employee_combo)
        assign_form.addRow("Firma:", self.assign_firma_combo)
        assign_form.addRow("Rola:", self.assign_rola_combo)

        right_layout.addLayout(assign_form)

        btn_assign = QPushButton("Przypisz pracownika")
        btn_assign.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold; padding: 10px;")
        btn_assign.clicked.connect(self.assign_employee_to_company)
        right_layout.addWidget(btn_assign)

        right_layout.addStretch()

        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        layout.addWidget(splitter)

        self.pages.addWidget(page)

    def refresh_employees_table(self):
        self.table_employees.setRowCount(0)
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, email, imie, nazwisko, aktywny FROM pracownicy")

        for row_idx, row in enumerate(cursor.fetchall()):
            self.table_employees.insertRow(row_idx)
            emp_id, email, imie, nazwisko, aktywny = row

            for col_idx, value in enumerate(row):
                if col_idx == 4:  # Kolumna 'Aktywny'
                    value = "Tak" if value else "Nie"
                self.table_employees.setItem(row_idx, col_idx, QTableWidgetItem(str(value)))

            # Przycisk usuwania
            btn_delete = QPushButton("Usuń")
            btn_delete.setStyleSheet("background-color: #e74c3c; color: white;")
            btn_delete.clicked.connect(lambda checked, eid=emp_id: self.delete_employee(eid))
            self.table_employees.setCellWidget(row_idx, 5, btn_delete)

        # Odśwież combo-boxy
        self.refresh_employee_combos()
        conn.close()

    def refresh_employee_combos(self):
        """Odświeża listy rozwijane pracowników i firm"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        # Pracownicy
        cursor.execute("SELECT id, imie || ' ' || nazwisko FROM pracownicy WHERE aktywny = 1")
        employees = cursor.fetchall()
        self.assign_employee_combo.clear()
        for emp_id, emp_name in employees:
            self.assign_employee_combo.addItem(f"{emp_name} ({emp_id})", emp_id)

        # Firmy
        cursor.execute("SELECT id, nazwa FROM firmy")
        companies = cursor.fetchall()
        self.assign_firma_combo.clear()
        for firma_id, firma_name in companies:
            self.assign_firma_combo.addItem(firma_name, firma_id)

        conn.close()

    def add_employee(self):
        email = self.emp_email.text().strip()
        imie = self.emp_imie.text().strip()
        nazwisko = self.emp_nazwisko.text().strip()
        haslo = self.emp_haslo.text()

        if not all([email, imie, nazwisko, haslo]):
            QMessageBox.warning(self, "Błąd", "Uzupełnij wszystkie pola")
            return

        user_id = register_user(email, haslo, imie, nazwisko)
        if user_id:
            QMessageBox.information(self, "Sukces", f"Pracownik {imie} {nazwisko} został dodany")
            # Czyszczenie formularza
            self.emp_email.clear()
            self.emp_imie.clear()
            self.emp_nazwisko.clear()
            self.emp_haslo.clear()
            self.refresh_employees_table()
        else:
            QMessageBox.critical(self, "Błąd", "Ten email jest już zarejestrowany")

    def delete_employee(self, emp_id):
        reply = QMessageBox.question(self, "Potwierdzenie", "Usunąć tego pracownika?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("UPDATE pracownicy SET aktywny = 0 WHERE id = ?", (emp_id,))
            conn.commit()
            conn.close()
            QMessageBox.information(self, "Sukces", "Pracownik został deaktywowany")
            self.refresh_employees_table()

    def assign_employee_to_company(self):
        emp_id = self.assign_employee_combo.currentData()
        firma_id = self.assign_firma_combo.currentData()
        rola_nazwa = self.assign_rola_combo.currentText()

        if not emp_id or not firma_id:
            QMessageBox.warning(self, "Błąd", "Wybierz pracownika i firmę")
            return

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        # Pobierz ID roli
        cursor.execute("SELECT id FROM role WHERE nazwa = ?", (rola_nazwa,))
        role_result = cursor.fetchone()
        if not role_result:
            QMessageBox.critical(self, "Błąd", "Rola nie istnieje")
            conn.close()
            return

        rola_id = role_result[0]

        try:
            cursor.execute("""
                INSERT INTO pracownik_firma (pracownik_id, firma_id, rola_id)
                VALUES (?, ?, ?)
            """, (emp_id, firma_id, rola_id))
            conn.commit()
            QMessageBox.information(self, "Sukces", "Pracownik został przypisany do firmy")
            self.refresh_employees_table()
        except sqlite3.IntegrityError:
            QMessageBox.warning(self, "Błąd", "Ten pracownik jest już przypisany do tej firmy w tej roli")
        finally:
            conn.close()

    # =========================================================================
    # WIDOK 4: CEIDG IMPORT
    # =========================================================================
    def create_ceidg_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)

        lbl_title = QLabel("Import firm z rejestru CEIDG")
        lbl_title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        layout.addWidget(lbl_title)

        lbl_info = QLabel("Wyszukaj firmy w Centralnym Rejestrze Ewidencji i Informacji o Działalności Gospodarczej")
        layout.addWidget(lbl_info)

        import_layout = QHBoxLayout()
        import_layout.addWidget(QLabel("Lub zaimportuj wiele firm z Excela zgodnego z uzgodnionymi kolumnami."))
        btn_excel_import = QPushButton("Importuj z Excela")
        btn_excel_import.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold;")
        btn_excel_import.clicked.connect(self.import_companies_from_excel)
        import_layout.addWidget(btn_excel_import, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addLayout(import_layout)

        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel("Szukaj po:"))

        self.ceidg_search_type = QComboBox()
        self.ceidg_search_type.addItems(["NIP", "Nazwa firmy"])
        search_layout.addWidget(self.ceidg_search_type)

        self.ceidg_search_input = QLineEdit()
        self.ceidg_search_input.setPlaceholderText("Wpisz NIP lub nazwę firmy...")
        search_layout.addWidget(self.ceidg_search_input)

        btn_search = QPushButton("Szukaj")
        btn_search.setStyleSheet("background-color: #3498db; color: white; font-weight: bold;")
        btn_search.clicked.connect(self.search_ceidg)
        search_layout.addWidget(btn_search)

        layout.addLayout(search_layout)

        layout.addWidget(QLabel("Wyniki wyszukiwania:"))
        self.table_ceidg_results = QTableWidget()
        self.table_ceidg_results.setColumnCount(6)
        self.table_ceidg_results.setHorizontalHeaderLabels(["NIP", "Nazwa", "REGON", "Adres", "Miasto", "Akcje"])
        self.table_ceidg_results.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table_ceidg_results)

        self.pages.addWidget(page)

    def search_ceidg(self):
        search_type = self.ceidg_search_type.currentText()
        search_query = self.ceidg_search_input.text().strip()

        if not search_query:
            QMessageBox.warning(self, "Błąd", "Wpisz NIP lub nazwę firmy")
            return

        self.table_ceidg_results.setRowCount(0)

        try:
            if search_type == "NIP":
                result = search_ceidg_by_nip(search_query)
                if result:
                    self.show_ceidg_result([result])
                else:
                    QMessageBox.information(self, "Wynik", "Nie znaleziono firmy z tym NIP")
            else:
                results = search_ceidg_by_name(search_query)
                if results:
                    self.show_ceidg_result(results)
                else:
                    QMessageBox.information(self, "Wynik", "Nie znaleziono firm o tej nazwie")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Błąd wyszukiwania: {str(e)}")

    def show_ceidg_result(self, companies):
        self.table_ceidg_results.setRowCount(0)

        for row_idx, company in enumerate(companies):
            self.table_ceidg_results.insertRow(row_idx)

            nip = company.get('nip', 'N/A')
            nazwa = company.get('nazwa', 'N/A')
            regon = company.get('regon', 'N/A')
            adres = company.get('adres', 'N/A')
            miasto = company.get('miasto', 'N/A')

            self.table_ceidg_results.setItem(row_idx, 0, QTableWidgetItem(str(nip)))
            self.table_ceidg_results.setItem(row_idx, 1, QTableWidgetItem(str(nazwa)))
            self.table_ceidg_results.setItem(row_idx, 2, QTableWidgetItem(str(regon)))
            self.table_ceidg_results.setItem(row_idx, 3, QTableWidgetItem(str(adres)))
            self.table_ceidg_results.setItem(row_idx, 4, QTableWidgetItem(str(miasto)))

            btn_import = QPushButton("Importuj")
            btn_import.setStyleSheet("background-color: #27ae60; color: white;")
            btn_import.clicked.connect(lambda checked, c=company: self.import_company_from_ceidg(c))
            self.table_ceidg_results.setCellWidget(row_idx, 5, btn_import)

    def import_company_from_ceidg(self, company_data):
        nip = company_data.get('nip')
        nazwa = company_data.get('nazwa')
        miasto = company_data.get('miasto')

        if not nazwa:
            QMessageBox.warning(self, "Błąd", "Brakuje danych firmy")
            return

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        try:
            cursor.execute("SELECT id FROM firmy WHERE nip = ?", (nip,))
            existing_row = cursor.fetchone()

            save_company(cursor, {
                "nazwa": nazwa,
                "nip": nip,
                "miasto": miasto,
                "regon": company_data.get("regon", ""),
                "glowny_cennik": "",
                "glowny_cennik_manualny": 0,
                "limit_dokumentow": 0,
                "liczba_pracownikow": 0,
                "uop": 0,
                "uz": 0,
                "uod": 0,
                "assigned_employee_ids": []
            }, company_id=existing_row[0] if existing_row else None)

            conn.commit()
            QMessageBox.information(self, "Sukces", f"Firma {nazwa} została zaimportowana!")
            self.refresh_firms_table()

        finally:
            conn.close()

    def import_companies_from_excel(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "Wybierz plik Excel z firmami", "",
                                                  "Pliki Excel (*.xlsx *.xlsm)")
        if not filepath:
            return

        result = import_companies_from_excel_file(filepath)
        summary_lines = [
            f"Utworzono: {result['created']}",
            f"Zaktualizowano: {result['updated']}"
        ]
        if result["warnings"]:
            summary_lines.append("")
            summary_lines.append("Uwagi:")
            summary_lines.extend(result["warnings"][:10])
            if len(result["warnings"]) > 10:
                summary_lines.append("...")

        QMessageBox.information(self, "Import firm z Excela", "\n".join(summary_lines))
        self.refresh_firms_table()

    # =========================================================================
    # WIDOK 4B: EMAIL CLIENT
    # =========================================================================
    # Wklej ten fragment do klasy Twojej aplikacji (np. OfficeManagerApp).
    # Zakłada istnienie: DB_NAME, encrypt_password, decrypt_password, EmailSyncer (opcjonalnie).
    # Upewnij się, że masz importy PyQt6 i modułów standardowych w górnej części pliku:
    # from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QTabWidget, QSplitter, QHBoxLayout, QFormLayout, QLineEdit, QSpinBox, QPushButton, QTableWidget, QTableWidgetItem, QComboBox, QTextEdit, QMessageBox
    # from PyQt6.QtWidgets import QHeaderView
    # from PyQt6.QtGui import QFont
    # from PyQt6.QtCore import Qt, QTimer, QRunnable, QThreadPool, QObject, pyqtSignal
    # import sqlite3, imaplib, email

    # --- Worker signals and runnable for background sync ---
    class _SyncWorkerSignals(QObject):
        finished = pyqtSignal()
        error = pyqtSignal(str)

    class _SyncRunnable(QRunnable):
        def __init__(self, parent):
            super().__init__()
            self.parent = parent
            self.signals = _SyncWorkerSignals()

        def run(self):
            try:
                # Wywołanie synchronizacji (metoda klasy powinna być bezpieczna do uruchomienia w tle)
                self.parent.sync_all_emails()
                self.signals.finished.emit()
            except Exception as e:
                self.signals.error.emit(str(e))
                self.signals.finished.emit()

    # ------------------ UI: zakładka Email ------------------

    def create_email_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)

        # Tytuł
        lbl_title = QLabel("Zarządzanie kontami Email")
        lbl_title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        layout.addWidget(lbl_title)

        # Karty - zarządzanie kontami i czytanie emaili
        self.email_tabs = QTabWidget()

        # Upewnij się, że combo dla filtrów istnieje zanim odświeżymy konta
        # (zapobiega błędowi gdy zakładka filtrów tworzy się później)
        if not hasattr(self, "filter_konto_combo"):
            self.filter_konto_combo = QComboBox()

        # === KARTA 1: CZYTANIE EMAILI (Inicjalizowana najpierw, aby stworzyć combo_box) ===
        reader_tab = self._create_email_reader_tab()
        self.email_tabs.addTab(reader_tab, "Przegląd Emaili")

        # === KARTA 2: ZARZĄDZANIE KONTAMI ===
        accounts_tab = self._create_email_accounts_tab()
        self.email_tabs.addTab(accounts_tab, "Konta Email")

        # === KARTA 3: FILTRY EMAILI ===
        filters_tab = self._create_email_filters_tab()
        self.email_tabs.addTab(filters_tab, "Filtry")

        layout.addWidget(self.email_tabs)
        self.pages.addWidget(page)

        # Inicjalizacja auto-sync co 5 minut (uruchom natychmiast)
        try:
            self._init_email_auto_sync(interval_minutes=5, start_immediately=True)
        except Exception:
            # jeśli metoda już istnieje lub nie można uruchomić, ignoruj
            pass

    def _create_email_accounts_tab(self):
        """Tab do zarządzania kontami email"""
        tab_widget = QWidget()
        layout = QVBoxLayout(tab_widget)

        # Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # === LEWA STRONA - LISTA KONT ===
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.addWidget(QLabel("Moje konta email:"))

        self.table_email_accounts = QTableWidget()
        self.table_email_accounts.setColumnCount(5)
        self.table_email_accounts.setHorizontalHeaderLabels(["ID", "Email", "Serwer IMAP", "Aktywne", "Akcje"])
        self.table_email_accounts.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_email_accounts.hideColumn(0)
        left_layout.addWidget(self.table_email_accounts)

        # === PRAWA STRONA - DODAWANIE KONTA ===
        right_widget = QWidget()
        right_widget.setFixedWidth(380)
        right_layout = QVBoxLayout(right_widget)
        right_layout.addWidget(QLabel("Dodaj nowe konto"))

        form = QFormLayout()
        self.email_input = QLineEdit()
        self.imap_server = QLineEdit()
        self.imap_port = QSpinBox()
        self.imap_port.setMaximum(9999)
        self.imap_port.setValue(993)
        self.smtp_server = QLineEdit()
        self.smtp_port = QSpinBox()
        self.smtp_port.setMaximum(9999)
        self.smtp_port.setValue(587)
        self.email_password = QLineEdit()
        self.email_password.setEchoMode(QLineEdit.EchoMode.Password)

        form.addRow("Email:", self.email_input)
        form.addRow("Serwer IMAP:", self.imap_server)
        form.addRow("Port IMAP:", self.imap_port)
        form.addRow("Serwer SMTP:", self.smtp_server)
        form.addRow("Port SMTP:", self.smtp_port)
        form.addRow("Hasło:", self.email_password)

        right_layout.addLayout(form)

        btn_add_account = QPushButton("Dodaj konto")
        btn_add_account.setStyleSheet("background-color: #3498db; color: white; font-weight: bold;")
        btn_add_account.clicked.connect(self.add_email_account)
        right_layout.addWidget(btn_add_account)

        btn_sync = QPushButton("🔄 Synchronizuj emaile")
        btn_sync.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold;")
        # uruchomienie synchronizacji w tle zamiast bezpośrednio
        btn_sync.clicked.connect(lambda: self._trigger_email_sync())
        right_layout.addWidget(btn_sync)

        right_layout.addStretch()

        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        layout.addWidget(splitter)

        # Refresh tabeli
        self.refresh_email_accounts()

        return tab_widget

    def _create_email_reader_tab(self):
        """Tab do czytania emaili"""
        tab_widget = QWidget()
        layout = QVBoxLayout(tab_widget)

        # Pasek narzędzi
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Folder:"))
        self.email_folder_combo = QComboBox()
        # domyślne foldery + miejsce na foldery firm
        self.email_folder_combo.addItems(["INBOX", "firma1", "firma2", "firma3", "Sent", "Drafts", "Trash"])
        toolbar.addWidget(self.email_folder_combo)

        toolbar.addWidget(QLabel("Konto:"))
        self.email_account_combo = QComboBox()
        toolbar.addWidget(self.email_account_combo)

        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self.refresh_email_list)
        toolbar.addWidget(btn_refresh)

        layout.addLayout(toolbar)

        # Tabela emaili
        self.table_email_list = QTableWidget()
        self.table_email_list.setColumnCount(5)
        self.table_email_list.setHorizontalHeaderLabels(["ID", "Od", "Temat", "Data", "Przeczytana"])
        self.table_email_list.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_email_list.hideColumn(0)
        self.table_email_list.itemClicked.connect(self.on_email_selected)
        layout.addWidget(self.table_email_list)

        # Panel treści emaila
        layout.addWidget(QLabel("Treść wiadomości:"))
        self.email_content = QTextEdit()
        self.email_content.setReadOnly(True)
        layout.addWidget(self.email_content)

        # Przyciski akcji
        action_layout = QHBoxLayout()
        btn_reply = QPushButton("Odpowiedź")
        btn_reply.clicked.connect(self.reply_to_email)
        action_layout.addWidget(btn_reply)

        btn_forward = QPushButton("Prześlij dalej")
        btn_forward.clicked.connect(self.forward_email)
        action_layout.addWidget(btn_forward)

        btn_move = QPushButton("Przenieś do folderu")
        btn_move.clicked.connect(self.move_selected_email)
        action_layout.addWidget(btn_move)

        btn_delete = QPushButton("Usuń")
        btn_delete.setStyleSheet("background-color: #e74c3c; color: white;")
        btn_delete.clicked.connect(self.delete_selected_email)
        action_layout.addWidget(btn_delete)

        layout.addLayout(action_layout)

        return tab_widget

    def _create_email_filters_tab(self):
        """Tab do zarządzania filtrami emaili"""
        tab_widget = QWidget()
        layout = QVBoxLayout(tab_widget)

        layout.addWidget(QLabel("Automatyczne filtry dla emaili"))

        # Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # === LEWA STRONA - LISTA FILTRÓW ===
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.addWidget(QLabel("Aktywne filtry:"))

        self.table_email_filters = QTableWidget()
        self.table_email_filters.setColumnCount(5)
        self.table_email_filters.setHorizontalHeaderLabels(["ID", "Od", "Temat zawiera", "Folder docelowy", "Akcje"])
        self.table_email_filters.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_email_filters.hideColumn(0)
        left_layout.addWidget(self.table_email_filters)

        # === PRAWA STRONA - DODAWANIE FILTRU ===
        right_widget = QWidget()
        right_widget.setFixedWidth(350)
        right_layout = QVBoxLayout(right_widget)
        right_layout.addWidget(QLabel("Dodaj nowy filtr"))

        form = QFormLayout()

        # Używamy wcześniej utworzonego self.filter_konto_combo jeśli istnieje
        if not hasattr(self, "filter_konto_combo") or self.filter_konto_combo is None:
            self.filter_konto_combo = QComboBox()

        self.filter_od = QLineEdit()
        self.filter_od.setPlaceholderText("np: boss@firma.pl")
        self.filter_temat = QLineEdit()
        self.filter_temat.setPlaceholderText("np: Faktura")
        self.filter_folder = QLineEdit()
        self.filter_folder.setPlaceholderText("np: Faktury")

        form.addRow("Konto:", self.filter_konto_combo)
        form.addRow("Od:", self.filter_od)
        form.addRow("Temat zawiera:", self.filter_temat)
        form.addRow("Folder docelowy:", self.filter_folder)

        right_layout.addLayout(form)

        # Info
        info_label = QLabel(
            "Uwaga: Jeśli pola 'Od' i 'Temat' są puste, filtr stosuje się do wszystkich emaili\nFiltry są stosowane automatycznie przy synchronizacji")
        info_label.setStyleSheet("color: #7f8c8d; font-size: 10px;")
        right_layout.addWidget(info_label)

        btn_add_filter = QPushButton("Dodaj filtr")
        btn_add_filter.setStyleSheet("background-color: #3498db; color: white; font-weight: bold;")
        btn_add_filter.clicked.connect(self.add_email_filter)
        right_layout.addWidget(btn_add_filter)

        right_layout.addStretch()

        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        layout.addWidget(splitter)

        # Refresh tabeli
        self._refresh_email_account_combos()
        self.refresh_email_filters()

        return tab_widget

    def refresh_email_filters(self):
        """Odśwież listę filtrów"""
        self.table_email_filters.setRowCount(0)
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        # Pobierz filtry dla kont tego użytkownika
        cursor.execute("""
            SELECT rf.id, rf.od, rf.temat_zawiera, rf.folder_docelowy
            FROM reguly_filtrow rf
            JOIN konta_email ke ON rf.konto_id = ke.id
            WHERE ke.pracownik_id = ?
        """, (self.pracownik_id,))

        for row_idx, row in enumerate(cursor.fetchall()):
            self.table_email_filters.insertRow(row_idx)
            filter_id, od, temat, folder = row

            self.table_email_filters.setItem(row_idx, 0, QTableWidgetItem(str(filter_id)))
            self.table_email_filters.setItem(row_idx, 1, QTableWidgetItem(od or "Dowolny"))
            self.table_email_filters.setItem(row_idx, 2, QTableWidgetItem(temat or "Dowolny"))
            self.table_email_filters.setItem(row_idx, 3, QTableWidgetItem(folder or "N/A"))

            btn_delete = QPushButton("Usuń")
            btn_delete.setStyleSheet("background-color: #e74c3c; color: white;")
            btn_delete.clicked.connect(lambda checked, fid=filter_id: self.delete_email_filter(fid))
            self.table_email_filters.setCellWidget(row_idx, 4, btn_delete)

        conn.close()

    def add_email_filter(self):
        """Dodaj nowy filtr emaili"""
        konto_id = self.filter_konto_combo.currentData()
        od = self.filter_od.text().strip() or None
        temat = self.filter_temat.text().strip() or None
        folder = self.filter_folder.text().strip()

        if not konto_id or not folder:
            QMessageBox.warning(self, "Błąd", "Wybierz konto i folder docelowy")
            return

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO reguly_filtrow (konto_id, od, temat_zawiera, folder_docelowy, aktywna)
                VALUES (?, ?, ?, ?, 1)
            """, (konto_id, od, temat, folder))
            conn.commit()
            QMessageBox.information(self, "Sukces", "Filtr został dodany")

            # Czyszczenie
            self.filter_od.clear()
            self.filter_temat.clear()
            self.filter_folder.clear()

            self.refresh_email_filters()
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Błąd dodawania filtru: {str(e)}")
        finally:
            conn.close()

    def delete_email_filter(self, filter_id):
        """Usuń filtr"""
        reply = QMessageBox.question(self, "Potwierdzenie", "Usunąć ten filtr?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM reguly_filtrow WHERE id = ?", (filter_id,))
            conn.commit()
            conn.close()
            self.refresh_email_filters()

    def refresh_email_accounts(self):
        """Odśwież listę kont email"""
        self.table_email_accounts.setRowCount(0)
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, email, serwer_imap, aktywna FROM konta_email WHERE pracownik_id = ?
        """, (self.pracownik_id,))

        for row_idx, row in enumerate(cursor.fetchall()):
            self.table_email_accounts.insertRow(row_idx)
            konto_id, email_addr, imap_server, aktywna = row

            self.table_email_accounts.setItem(row_idx, 0, QTableWidgetItem(str(konto_id)))
            self.table_email_accounts.setItem(row_idx, 1, QTableWidgetItem(email_addr))
            self.table_email_accounts.setItem(row_idx, 2, QTableWidgetItem(imap_server))
            self.table_email_accounts.setItem(row_idx, 3, QTableWidgetItem("Tak" if aktywna else "Nie"))

            # Przycisk usuwania
            btn_delete = QPushButton("Usuń")
            btn_delete.setStyleSheet("background-color: #e74c3c; color: white;")
            btn_delete.clicked.connect(lambda checked, kid=konto_id: self.delete_email_account(kid))
            self.table_email_accounts.setCellWidget(row_idx, 4, btn_delete)

        # Odśwież combo boxy
        self._refresh_email_account_combos()
        conn.close()

    def _refresh_email_account_combos(self):
        """Odśwież combo boxy kont email (bez błędu gdy widgety nie istnieją jeszcze)"""
        # Upewnij się, że widgety istnieją
        if not hasattr(self, "email_account_combo") or self.email_account_combo is None:
            self.email_account_combo = QComboBox()
        if not hasattr(self, "filter_konto_combo") or self.filter_konto_combo is None:
            self.filter_konto_combo = QComboBox()

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, email FROM konta_email WHERE pracownik_id = ?", (self.pracownik_id,))
        accounts = cursor.fetchall()

        self.email_account_combo.clear()
        self.filter_konto_combo.clear()
        for kid, email_addr in accounts:
            self.email_account_combo.addItem(email_addr, kid)
            self.filter_konto_combo.addItem(email_addr, kid)

        conn.close()

    def add_email_account(self):
        """Dodaj nowe konto email"""
        email_addr = self.email_input.text().strip()
        imap_srv = self.imap_server.text().strip()
        imap_p = self.imap_port.value()
        smtp_srv = self.smtp_server.text().strip()
        smtp_p = self.smtp_port.value()
        password = self.email_password.text()

        if not all([email_addr, imap_srv, smtp_srv, password]):
            QMessageBox.warning(self, "Błąd", "Uzupełnij wszystkie pola")
            return

        # Zaszyfruj hasło
        encrypted_pwd = encrypt_password(password)

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO konta_email 
                (pracownik_id, email, serwer_imap, port_imap, serwer_smtp, port_smtp, haslo_enc)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (self.pracownik_id, email_addr, imap_srv, imap_p, smtp_srv, smtp_p, encrypted_pwd))
            conn.commit()
            QMessageBox.information(self, "Sukces", "Konto email zostało dodane!")

            # Czyszczenie formularza
            self.email_input.clear()
            self.imap_server.clear()
            self.smtp_server.clear()
            self.email_password.clear()

            self.refresh_email_accounts()
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Błąd dodawania konta: {str(e)}")
        finally:
            conn.close()

    def delete_email_account(self, konto_id):
        """Usuń konto email"""
        reply = QMessageBox.question(self, "Potwierdzenie", "Usunąć to konto?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM konta_email WHERE id = ?", (konto_id,))
            conn.commit()
            conn.close()
            self.refresh_email_accounts()

    # ------------------ Treść wiadomości, przenoszenie, usuwanie ------------------

    def _decode_email_body(self, msg):
        """Zwraca poprawnie zdekodowaną treść emaila (HTML lub tekst)"""
        body = ""
        html = None

        try:
            if msg.is_multipart():
                for part in msg.walk():
                    ctype = part.get_content_type()
                    if part.get_content_maintype() == 'multipart':
                        continue
                    payload = part.get_payload(decode=True)
                    if payload is None:
                        continue
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        decoded = payload.decode(charset, errors="replace")
                    except Exception:
                        decoded = payload.decode("utf-8", errors="replace")
                    if ctype == "text/plain" and not body:
                        body = decoded
                    elif ctype == "text/html":
                        html = decoded
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    charset = msg.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="replace")
        except Exception:
            try:
                body = str(msg)
            except Exception:
                body = ""

        return html if html else body

    def on_email_selected(self, item):
        """Pokaż treść wybranego emaila (pobierane z lokalnej bazy wiadomosci)"""
        row = item.row()
        msg_id_item = self.table_email_list.item(row, 0)
        if not msg_id_item:
            return
        msg_id = int(msg_id_item.text())

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT od, temat, tresc, raw_msg FROM wiadomosci WHERE id = ?", (msg_id,))
        result = cursor.fetchone()
        conn.close()

        if result:
            od, temat, tresc, raw = result
            # Jeśli w bazie jest surowa wiadomość RFC822 (raw_msg), spróbuj zdekodować i renderować HTML
            if raw:
                try:
                    import email
                    msg = email.message_from_bytes(raw)
                    decoded = self._decode_email_body(msg)
                    if decoded and "<html" in decoded.lower():
                        self.email_content.setHtml(decoded)
                        return
                    else:
                        # fallback do tekstu z bazy
                        pass
                except Exception:
                    pass
            # domyślnie pokazujemy treść z kolumny tresc
            self.email_content.setPlainText(f"Od: {od}\nTemat: {temat}\n\n{tresc or ''}")

    def _load_email_message(self, konto_id, folder, email_uid):
        """
        Pobiera wiadomość bezpośrednio z serwera IMAP i zwraca email.message.Message.
        Używane w funkcjach przenoszenia lub gdy chcesz pobrać pełny RFC822.
        """
        try:
            conn = sqlite3.connect(DB_NAME)
            cur = conn.cursor()
            cur.execute("SELECT serwer_imap, port_imap, haslo_enc, email FROM konta_email WHERE id = ?", (konto_id,))
            row = cur.fetchone()
            conn.close()
            if not row:
                return None
            imap_srv, imap_port, haslo_enc, email_addr = row
            password = decrypt_password(haslo_enc)

            import imaplib, email
            imap = imaplib.IMAP4_SSL(imap_srv, imap_port, timeout=30)
            imap.login(email_addr, password)
            imap.select(folder or "INBOX")
            status, data = imap.uid('fetch', email_uid, '(RFC822)')
            if status != 'OK' or not data or not data[0]:
                imap.logout()
                return None
            raw = data[0][1]
            msg = email.message_from_bytes(raw)
            imap.logout()
            return msg
        except Exception:
            return None

    def move_email_to_folder(self, imap, email_uid, target_folder):
        """
        Przenieś wiadomość na serwerze IMAP do target_folder.
        imap to aktywne połączenie imaplib.IMAP4/IMAP4_SSL.
        """
        try:
            imap.uid("COPY", email_uid, target_folder)
            imap.uid("STORE", email_uid, "+FLAGS", r"(\Deleted)")
            imap.expunge()
            return True
        except Exception:
            return False

    def move_selected_email(self):
        """Przenieś aktualnie zaznaczoną wiadomość do folderu wybranego w combo"""
        row = self.table_email_list.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Błąd", "Wybierz wiadomość do przeniesienia")
            return

        email_uid_item = self.table_email_list.item(row, 0)
        if not email_uid_item:
            QMessageBox.warning(self, "Błąd", "Brak identyfikatora wiadomości")
            return
        email_uid = email_uid_item.text()
        konto_id = self.email_account_combo.currentData()
        target_folder = self.email_folder_combo.currentText()

        # Pobierz dane konta
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("SELECT serwer_imap, port_imap, haslo_enc, email FROM konta_email WHERE id = ?", (konto_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            QMessageBox.critical(self, "Błąd", "Nie można pobrać danych konta")
            return
        imap_srv, imap_port, haslo_enc, email_addr = row
        password = decrypt_password(haslo_enc)

        import imaplib
        try:
            imap = imaplib.IMAP4_SSL(imap_srv, imap_port, timeout=30)
            imap.login(email_addr, password)
            # upewnij się, że folder docelowy istnieje; jeśli nie, spróbuj utworzyć
            try:
                imap.create(target_folder)
            except Exception:
                pass
            ok = self.move_email_to_folder(imap, email_uid, target_folder)
            imap.logout()
            if ok:
                QMessageBox.information(self, "Sukces", "Wiadomość przeniesiona")
                self.refresh_email_list()
            else:
                QMessageBox.critical(self, "Błąd", "Nie udało się przenieść wiadomości")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Błąd połączenia IMAP: {str(e)}")

    def delete_selected_email(self):
        """Usuń (przenieś do kosza) zaznaczoną wiadomość"""
        row = self.table_email_list.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Błąd", "Wybierz wiadomość do usunięcia")
            return

        email_uid_item = self.table_email_list.item(row, 0)
        if not email_uid_item:
            QMessageBox.warning(self, "Błąd", "Brak identyfikatora wiadomości")
            return
        email_uid = email_uid_item.text()
        konto_id = self.email_account_combo.currentData()

        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("SELECT serwer_imap, port_imap, haslo_enc, email FROM konta_email WHERE id = ?", (konto_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            QMessageBox.critical(self, "Błąd", "Nie można pobrać danych konta")
            return
        imap_srv, imap_port, haslo_enc, email_addr = row
        password = decrypt_password(haslo_enc)

        import imaplib
        try:
            imap = imaplib.IMAP4_SSL(imap_srv, imap_port, timeout=30)
            imap.login(email_addr, password)
            destination = "Trash"
            try:
                imap.create(destination)
            except Exception:
                pass
            ok = self.move_email_to_folder(imap, email_uid, destination)
            imap.logout()
            if ok:
                QMessageBox.information(self, "Sukces", "Wiadomość przeniesiona do kosza")
                self.refresh_email_list()
            else:
                QMessageBox.critical(self, "Błąd", "Nie udało się usunąć wiadomości")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Błąd połączenia IMAP: {str(e)}")

    # ------------------ Synchronizacja i auto-sync co 5 minut ------------------

    def sync_all_emails(self):
        """
        Synchronizuj emaile dla wszystkich aktywnych kont i zastosuj reguły filtrów.
        Implementacja używa imaplib i zapisuje/przetwarza wiadomości zgodnie z regułami.
        """
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute(
            "SELECT id, email, serwer_imap, port_imap, haslo_enc FROM konta_email WHERE pracownik_id = ? AND aktywna = 1",
            (self.pracownik_id,))
        accounts = cur.fetchall()
        conn.close()

        import imaplib, email, time

        for konto in accounts:
            konto_id, email_addr, imap_srv, imap_port, haslo_enc = konto
            password = decrypt_password(haslo_enc)
            try:
                imap = imaplib.IMAP4_SSL(imap_srv, imap_port, timeout=30)
                imap.login(email_addr, password)
                imap.select("INBOX")

                status, data = imap.uid('search', None, 'ALL')
                if status != 'OK':
                    imap.logout()
                    continue
                uids = data[0].split()
                uids = uids[-200:]  # ograniczenie

                # załaduj reguły dla konta
                conn = sqlite3.connect(DB_NAME)
                cur = conn.cursor()
                cur.execute(
                    "SELECT id, od, temat_zawiera, folder_docelowy FROM reguly_filtrow WHERE konto_id = ? AND aktywna = 1",
                    (konto_id,))
                filters = [{"id": r[0], "od": r[1], "temat": r[2], "folder": r[3]} for r in cur.fetchall()]
                conn.close()

                for uid in uids:
                    try:
                        status, msg_data = imap.uid('fetch', uid, '(RFC822)')
                        if status != 'OK' or not msg_data or not msg_data[0]:
                            continue
                        raw = msg_data[0][1]
                        msg = email.message_from_bytes(raw)

                        # dekoduj nagłówki
                        try:
                            subject = email.header.make_header(email.header.decode_header(msg.get("Subject") or ""))
                            subject = str(subject)
                        except Exception:
                            subject = msg.get("Subject") or ""
                        try:
                            from_hdr = email.header.make_header(email.header.decode_header(msg.get("From") or ""))
                            from_hdr = str(from_hdr)
                        except Exception:
                            from_hdr = msg.get("From") or ""

                        # Zapisz wiadomość do lokalnej bazy jeśli jeszcze nie istnieje (unikat po uid i koncie)
                        conn = sqlite3.connect(DB_NAME)
                        cur = conn.cursor()
                        cur.execute("SELECT COUNT(1) FROM wiadomosci WHERE uid = ? AND konto_id = ?",
                                    (uid.decode() if isinstance(uid, bytes) else str(uid), konto_id))
                        exists = cur.fetchone()[0]
                        if not exists:
                            # pobierz treść tekstową do kolumny tresc (pierwszy text/plain lub text/html fallback)
                            body = self._decode_email_body(msg)
                            # zapisz raw RFC822 do raw_msg (bytes) jeśli masz kolumnę BLOB
                            try:
                                cur.execute("""
                                    INSERT INTO wiadomosci (konto_id, uid, od, temat, tresc, data_otrzymania, folder, raw_msg, przeczytana)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                                """, (
                                    konto_id,
                                    uid.decode() if isinstance(uid, bytes) else str(uid),
                                    from_hdr,
                                    subject,
                                    body if body else "",
                                    time.strftime("%Y-%m-%d %H:%M:%S"),
                                    "INBOX",
                                    raw
                                ))
                                conn.commit()
                            except Exception:
                                # jeśli tabela nie ma raw_msg lub innego pola, spróbuj prostszego insertu
                                try:
                                    cur.execute("""
                                        INSERT INTO wiadomosci (konto_id, uid, od, temat, tresc, data_otrzymania, folder, przeczytana)
                                        VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                                    """, (
                                        konto_id,
                                        uid.decode() if isinstance(uid, bytes) else str(uid),
                                        from_hdr,
                                        subject,
                                        body if body else "",
                                        time.strftime("%Y-%m-%d %H:%M:%S"),
                                        "INBOX"
                                    ))
                                    conn.commit()
                                except Exception:
                                    pass
                        conn.close()

                        # zastosuj reguły
                        for f in filters:
                            match_from = (f["od"] is None) or (f["od"] and f["od"].lower() in from_hdr.lower())
                            match_subject = (f["temat"] is None) or (
                                        f["temat"] and f["temat"].lower() in subject.lower())
                            if match_from and match_subject:
                                try:
                                    imap.create(f["folder"])
                                except Exception:
                                    pass
                                self.move_email_to_folder(imap, uid, f["folder"])
                                # zaktualizuj wpis w bazie (jeśli istnieje) - ustaw folder
                                try:
                                    conn = sqlite3.connect(DB_NAME)
                                    cur = conn.cursor()
                                    cur.execute("UPDATE wiadomosci SET folder = ? WHERE uid = ? AND konto_id = ?",
                                                (f["folder"], uid.decode() if isinstance(uid, bytes) else str(uid),
                                                 konto_id))
                                    conn.commit()
                                    conn.close()
                                except Exception:
                                    pass
                                break

                    except Exception:
                        continue

                imap.logout()
            except Exception:
                continue

        # po synchronizacji odśwież widoki w GUI (wywoływane z wątku głównego przez worker signals)
        return

    # ------------------ Auto-sync (QTimer + QThreadPool) ------------------

    def _init_email_auto_sync(self, interval_minutes: int = 5, start_immediately: bool = True):
        """
        Inicjalizuje automatyczną synchronizację emaili.
        - interval_minutes: co ile minut uruchamiać sync (domyślnie 5)
        - start_immediately: czy uruchomić pierwszą synchronizację od razu
        Wywołaj tę metodę np. w create_email_page.
        """
        self._email_sync_running = False
        self._email_threadpool = QThreadPool.globalInstance()

        self._email_sync_timer = QTimer(self)
        self._email_sync_timer.setInterval(max(1, interval_minutes) * 60 * 1000)  # ms
        self._email_sync_timer.timeout.connect(lambda: self._trigger_email_sync())

        if start_immediately:
            self._trigger_email_sync()

        self._email_sync_timer.start()

    def _trigger_email_sync(self):
        """Wywołanie synchronizacji w tle, jeśli nie jest już uruchomiona."""
        if getattr(self, "_email_sync_running", False):
            return

        self._email_sync_running = True

        runnable = _SyncRunnable(self)
        runnable.signals.finished.connect(self._on_email_sync_finished)
        runnable.signals.error.connect(self._on_email_sync_error)

        self._email_threadpool.start(runnable)

    def _on_email_sync_finished(self):
        """Wywoływane po zakończeniu synchronizacji."""
        self._email_sync_running = False
        try:
            self.refresh_email_list()
            self.refresh_email_accounts()
            self.refresh_email_filters()
        except Exception:
            pass

    def _on_email_sync_error(self, error_msg):
        """Obsługa błędu synchronizacji (opcjonalne logowanie)."""
        self._email_sync_running = False
        print("Błąd synchronizacji emaili:", error_msg)

    # ------------------ Widok listy i proste akcje ------------------

    def refresh_email_list(self):
        """Odśwież listę emaili z lokalnej bazy"""
        konto_id = self.email_account_combo.currentData()
        folder = self.email_folder_combo.currentText()

        if not konto_id:
            self.table_email_list.setRowCount(0)
            return

        self.table_email_list.setRowCount(0)
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, od, temat, data_otrzymania, przeczytana 
            FROM wiadomosci 
            WHERE konto_id = ? AND folder = ?
            ORDER BY data_otrzymania DESC
        """, (konto_id, folder))

        for row_idx, row in enumerate(cursor.fetchall()):
            self.table_email_list.insertRow(row_idx)
            msg_id, od, temat, data, przeczytana = row

            self.table_email_list.setItem(row_idx, 0, QTableWidgetItem(str(msg_id)))
            self.table_email_list.setItem(row_idx, 1, QTableWidgetItem((od or "")[:50]))
            self.table_email_list.setItem(row_idx, 2, QTableWidgetItem((temat or "")[:60]))
            self.table_email_list.setItem(row_idx, 3, QTableWidgetItem(str(data)))
            self.table_email_list.setItem(row_idx, 4, QTableWidgetItem("✓" if przeczytana else ""))

        conn.close()

    def reply_to_email(self):
        """Otwiera okno odpowiedzi z wstępnie wypełnionymi polami (Do, Temat, Treść)."""
        row = self.table_email_list.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Błąd", "Wybierz wiadomość, na którą chcesz odpowiedzieć")
            return

        msg_id_item = self.table_email_list.item(row, 0)
        if not msg_id_item:
            QMessageBox.warning(self, "Błąd", "Brak identyfikatora wiadomości")
            return
        msg_id = int(msg_id_item.text())

        # Pobierz wiadomość z bazy
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("SELECT od, temat, tresc, raw_msg FROM wiadomosci WHERE id = ?", (msg_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            QMessageBox.critical(self, "Błąd", "Nie można pobrać wiadomości z bazy")
            return

        od, temat, tresc, raw = row

        # Spróbuj wydobyć adres e-mail z nagłówka "From"
        to_addr = od
        try:
            import email
            hdr = email.header.make_header(email.header.decode_header(od or ""))
            hdr = str(hdr)
            # prosty ekstraktor adresu
            import re
            m = re.search(r'[\w\.-]+@[\w\.-]+', hdr)
            if m:
                to_addr = m.group(0)
        except Exception:
            to_addr = od

        # Przygotuj temat
        reply_subject = temat or ""
        if not reply_subject.lower().startswith("re:"):
            reply_subject = "Re: " + reply_subject

        # Przygotuj treść cytowaną
        quoted = ""
        # jeśli mamy raw RFC822, spróbuj zdekodować ładnie
        if raw:
            try:
                import email
                msg = email.message_from_bytes(raw)
                body = self._decode_email_body(msg)
                quoted = "\n\n--- Oryginalna wiadomość ---\n" + (body or tresc or "")
            except Exception:
                quoted = "\n\n--- Oryginalna wiadomość ---\n" + (tresc or "")
        else:
            quoted = "\n\n--- Oryginalna wiadomość ---\n" + (tresc or "")

        # Dialog kompozycji odpowiedzi
        dlg = QDialog(self)
        dlg.setWindowTitle("Odpowiedz")
        dlg_layout = QVBoxLayout(dlg)

        form = QFormLayout()
        to_edit = QLineEdit()
        to_edit.setText(to_addr or "")
        subj_edit = QLineEdit()
        subj_edit.setText(reply_subject)
        body_edit = QTextEdit()
        body_edit.setPlainText("\n\n" + quoted)

        form.addRow("Do:", to_edit)
        form.addRow("Temat:", subj_edit)
        dlg_layout.addLayout(form)
        dlg_layout.addWidget(QLabel("Treść:"))
        dlg_layout.addWidget(body_edit)

        btn_layout = QHBoxLayout()
        btn_send = QPushButton("Wyślij")
        btn_cancel = QPushButton("Anuluj")
        btn_layout.addWidget(btn_send)
        btn_layout.addWidget(btn_cancel)
        dlg_layout.addLayout(btn_layout)

        def _send():
            to_val = to_edit.text().strip()
            subj_val = subj_edit.text().strip()
            body_val = body_edit.toPlainText()
            if not to_val:
                QMessageBox.warning(dlg, "Błąd", "Pole 'Do' nie może być puste")
                return
            # Wywołaj istniejącą funkcję wysyłki, jeśli jest zaimplementowana
            try:
                if hasattr(self, "send_email") and callable(self.send_email):
                    # send_email(to, subject, body, attachments=None, reply_to_uid=None)
                    self.send_email([to_val], subj_val, body_val, attachments=None, reply_to_uid=str(msg_id))
                    QMessageBox.information(dlg, "Sukces", "Wiadomość została wysłana")
                else:
                    # fallback: użyj compose_email tool jeśli dostępny (nie wywołujemy tutaj narzędzi)
                    QMessageBox.information(dlg, "Info",
                                            "Funkcja wysyłki nie jest zaimplementowana. Skopiuj treść i wyślij ręcznie.")
            except Exception as e:
                QMessageBox.critical(dlg, "Błąd wysyłki", f"Nie udało się wysłać wiadomości: {e}")
            dlg.accept()

        btn_send.clicked.connect(_send)
        btn_cancel.clicked.connect(dlg.reject)

        dlg.exec()

    def forward_email(self):
        """Otwiera okno przesyłania dalej z wstępnie wypełnionym tematem i treścią oryginalnej wiadomości."""
        row = self.table_email_list.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Błąd", "Wybierz wiadomość do przesłania dalej")
            return

        msg_id_item = self.table_email_list.item(row, 0)
        if not msg_id_item:
            QMessageBox.warning(self, "Błąd", "Brak identyfikatora wiadomości")
            return
        msg_id = int(msg_id_item.text())

        # Pobierz wiadomość z bazy
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("SELECT od, temat, tresc, raw_msg FROM wiadomosci WHERE id = ?", (msg_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            QMessageBox.critical(self, "Błąd", "Nie można pobrać wiadomości z bazy")
            return

        od, temat, tresc, raw = row

        fwd_subject = temat or ""
        if not fwd_subject.lower().startswith("fwd:") and not fwd_subject.lower().startswith("fw:"):
            fwd_subject = "Fwd: " + fwd_subject

        # Przygotuj treść do przesłania (dołącz oryginał)
        original = ""
        if raw:
            try:
                import email
                msg = email.message_from_bytes(raw)
                original = self._decode_email_body(msg)
            except Exception:
                original = tresc or ""
        else:
            original = tresc or ""

        fwd_body = "\n\n--- Oryginalna wiadomość ---\n"
        fwd_body += f"Od: {od}\nTemat: {temat}\n\n"
        fwd_body += original

        # Dialog kompozycji forward
        dlg = QDialog(self)
        dlg.setWindowTitle("Prześlij dalej")
        dlg_layout = QVBoxLayout(dlg)

        form = QFormLayout()
        to_edit = QLineEdit()
        subj_edit = QLineEdit()
        subj_edit.setText(fwd_subject)
        body_edit = QTextEdit()
        body_edit.setPlainText(fwd_body)

        form.addRow("Do:", to_edit)
        form.addRow("Temat:", subj_edit)
        dlg_layout.addLayout(form)
        dlg_layout.addWidget(QLabel("Treść:"))
        dlg_layout.addWidget(body_edit)

        btn_layout = QHBoxLayout()
        btn_send = QPushButton("Wyślij")
        btn_cancel = QPushButton("Anuluj")
        btn_layout.addWidget(btn_send)
        btn_layout.addWidget(btn_cancel)
        dlg_layout.addLayout(btn_layout)

        def _send_forward():
            to_val = to_edit.text().strip()
            subj_val = subj_edit.text().strip()
            body_val = body_edit.toPlainText()
            if not to_val:
                QMessageBox.warning(dlg, "Błąd", "Pole 'Do' nie może być puste")
                return
            try:
                if hasattr(self, "send_email") and callable(self.send_email):
                    # Nie obsługujemy załączników w tej prostej implementacji
                    self.send_email([to_val], subj_val, body_val, attachments=None, forward_uid=str(msg_id))
                    QMessageBox.information(dlg, "Sukces", "Wiadomość została przesłana dalej")
                else:
                    QMessageBox.information(dlg, "Info",
                                            "Funkcja wysyłki nie jest zaimplementowana. Skopiuj treść i wyślij ręcznie.")
            except Exception as e:
                QMessageBox.critical(dlg, "Błąd wysyłki", f"Nie udało się wysłać wiadomości: {e}")
            dlg.accept()

        btn_send.clicked.connect(_send_forward)
        btn_cancel.clicked.connect(dlg.reject)

        dlg.exec()

    # =========================================================================
    # WIDOK 5: CHAT WEWNĘTRZNY
    # =========================================================================
    def create_chat_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)

        lbl_title = QLabel("Chat wewnętrzny")
        lbl_title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        layout.addWidget(lbl_title)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # === LEWA STRONA - LISTA KONWERSACJI ===
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.addWidget(QLabel("Konwersacje:"))

        self.chat_conversations = QListWidget()
        self.chat_conversations.itemSelectionChanged.connect(self.on_chat_selected)
        left_layout.addWidget(self.chat_conversations)

        btn_new_chat = QPushButton("➕ Nowa konwersacja")
        btn_new_chat.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold;")
        btn_new_chat.clicked.connect(self.create_new_conversation)
        left_layout.addWidget(btn_new_chat)

        left_widget.setFixedWidth(300)
        splitter.addWidget(left_widget)

        # === PRAWA STRONA - WIADOMOŚCI ===
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.addWidget(QLabel("Wiadomości:"))

        self.chat_messages = QTextEdit()
        self.chat_messages.setReadOnly(True)
        right_layout.addWidget(self.chat_messages)

        # Formularz wysyłania wiadomości
        bottom_layout = QHBoxLayout()
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Wpisz wiadomość...")
        bottom_layout.addWidget(self.chat_input)

        btn_send = QPushButton("Wyślij")
        btn_send.setStyleSheet("background-color: #3498db; color: white; font-weight: bold;")
        btn_send.clicked.connect(self.send_chat_message)
        bottom_layout.addWidget(btn_send)

        right_layout.addLayout(bottom_layout)
        splitter.addWidget(right_widget)

        layout.addWidget(splitter)
        self.pages.addWidget(page)

        # Odśwież co 2 sekundy
        self.chat_timer = QTimer()
        self.chat_timer.timeout.connect(self.refresh_chat_display)
        self.chat_timer.start(2000)

    def refresh_chat_conversations(self):
        """Odśwież listę konwersacji"""
        self.chat_conversations.blockSignals(True)
        try:
            self.chat_conversations.clear()
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT DISTINCT k.id, k.nazwa 
                FROM konwersacje k
                JOIN uczestnicy_chat uc ON k.id = uc.konwersacja_id
                WHERE uc.pracownik_id = ?
                ORDER BY k.data_utworzenia DESC
            """, (self.pracownik_id,))

            for konwersacja_id, nazwa in cursor.fetchall():
                item = QListWidgetItem(nazwa)
                item.setData(Qt.ItemDataRole.UserRole, konwersacja_id)
                self.chat_conversations.addItem(item)

            conn.close()
        finally:
            self.chat_conversations.blockSignals(False)

    def refresh_chat_display(self):
        if self.chat_conversations.currentRow() < 0:
            return

        current_item = self.chat_conversations.currentItem()
        if not current_item:
            return

        konwersacja_id = current_item.data(Qt.ItemDataRole.UserRole)
        if konwersacja_id is None:
            return

        stary_tekst = self.chat_messages.toHtml()

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT p.imie, p.nazwisko, wc.tresc, wc.data_wysylki
            FROM wiadomosci_chat wc
            JOIN pracownicy p ON wc.pracownik_id = p.id
            WHERE wc.konwersacja_id = ?
            ORDER BY wc.data_wysylki ASC
        """, (konwersacja_id,))

        nowy_tekst = ""
        for imie, nazwisko, tresc, data in cursor.fetchall():
            nowy_tekst += f"<b>{imie} {nazwisko}</b> ({data}):<br>{tresc}<br><br>"

        conn.close()

        # Aktualizujemy widget tekstowy tylko wtedy, gdy pojawiły się nowe wiadomości
        if self.chat_messages.toPlainText().strip() != nowy_tekst.replace('<br>', '\n').replace('<b>', '').replace(
                '</b>', '').strip():
            self.chat_messages.setHtml(nowy_tekst)
            # Przewijamy chat na sam dół do najnowszej wiadomości
            self.chat_messages.verticalScrollBar().setValue(self.chat_messages.verticalScrollBar().maximum())

    def on_chat_selected(self):
        """Odśwież wiadomości gdy zmieni się wybrana konwersacja"""
        self.refresh_chat_display()

    def send_chat_message(self):
        """Wyślij wiadomość w chacie"""
        if self.chat_conversations.currentRow() < 0:
            QMessageBox.warning(self, "Błąd", "Wybierz konwersację")
            return

        message = self.chat_input.text().strip()
        if not message:
            return

        current_item = self.chat_conversations.currentItem()
        if not current_item:
            return

        konwersacja_id = current_item.data(Qt.ItemDataRole.UserRole)
        if konwersacja_id is None:
            QMessageBox.warning(self, "Błąd", "Nie można odczytać wybranej konwersacji. Odśwież listę.")
            return

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO wiadomosci_chat (konwersacja_id, pracownik_id, tresc, data_wysylki)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """, (konwersacja_id, self.pracownik_id, message))
            conn.commit()
            self.chat_input.clear()
            self.refresh_chat_display()
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Błąd wysyłania: {str(e)}")
        finally:
            conn.close()

    def create_new_conversation(self):
        """Utwórz nową konwersację"""
        dialog = QDialog(self)
        dialog.setWindowTitle("Nowa konwersacja")
        dialog.setFixedSize(400, 250)

        layout = QVBoxLayout()
        form = QFormLayout()

        nazwa_field = QLineEdit()
        nazwa_field.setPlaceholderText("np: Projekt X - Team")
        form.addRow("Nazwa:", nazwa_field)

        # Wybór uczestników
        uczestnicy_list = QListWidget()
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, imie, nazwisko FROM pracownicy WHERE aktywny = 1 ORDER BY imie")

        for emp_id, imie, nazwisko in cursor.fetchall():
            uczestnicy_list.addItem(f"{imie} {nazwisko}")
            widget = uczestnicy_list.item(uczestnicy_list.count() - 1)
            if widget:
                widget.setData(Qt.ItemDataRole.UserRole, emp_id)

        form.addRow("Uczestnicy:", uczestnicy_list)
        layout.addLayout(form)

        # Przyciski
        btn_layout = QHBoxLayout()
        btn_create = QPushButton("Utwórz")
        btn_create.setStyleSheet("background-color: #27ae60; color: white;")
        btn_cancel = QPushButton("Anuluj")
        btn_layout.addWidget(btn_create)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)

        dialog.setLayout(layout)

        def on_create():
            nazwa = nazwa_field.text().strip()
            if not nazwa:
                QMessageBox.warning(dialog, "Błąd", "Wpisz nazwę konwersacji")
                return

            if uczestnicy_list.selectedItems() == []:
                QMessageBox.warning(dialog, "Błąd", "Wybierz co najmniej jednego uczestnika")
                return

            conn2 = sqlite3.connect(DB_NAME)
            cursor2 = conn2.cursor()

            try:
                cursor2.execute("""
                    INSERT INTO konwersacje (nazwa, pracownik_creator_id)
                    VALUES (?, ?)
                """, (nazwa, self.pracownik_id))
                konwersacja_id = cursor2.lastrowid

                # Dodaj twórcę
                cursor2.execute("""
                    INSERT INTO uczestnicy_chat (konwersacja_id, pracownik_id)
                    VALUES (?, ?)
                """, (konwersacja_id, self.pracownik_id))

                # Dodaj wybranych uczestników
                for item in uczestnicy_list.selectedItems():
                    emp_id = item.data(Qt.ItemDataRole.UserRole)
                    if emp_id != self.pracownik_id:
                        cursor2.execute("""
                            INSERT OR IGNORE INTO uczestnicy_chat (konwersacja_id, pracownik_id)
                            VALUES (?, ?)
                        """, (konwersacja_id, emp_id))

                conn2.commit()
                QMessageBox.information(dialog, "Sukces", "Konwersacja została utworzona")
                dialog.accept()
                self.refresh_chat_conversations()
            except Exception as e:
                QMessageBox.critical(dialog, "Błąd", f"Błąd: {str(e)}")
            finally:
                conn2.close()

        btn_create.clicked.connect(on_create)
        btn_cancel.clicked.connect(dialog.reject)

        conn.close()
        dialog.exec()

    # =========================================================================
    # WIDOK 6: EKSPORT DO PLIKU EXCEL (SALDEOSMART - 56 KOLUMN)
    # =========================================================================
    def create_saldeo_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.addWidget(QLabel("Generator struktury arkusza integracyjnego SaldeoSMART"))
        layout.addWidget(QLabel("Aplikacja mapuje wprowadzone dokumenty i tworzy plik Excel z 56 nagłówkami wymaganymi przez system Saldeo. Dane zostaną uzupełnione w odpowiednich kolumnach automatycznie."))
        btn_export = QPushButton("📁 Wygeneruj i zapisz plik Excel (*.xlsx)")
        btn_export.setStyleSheet("background-color: #2ecc71; color: white; font-size: 15px; padding: 15px; font-weight: bold;")
        btn_export.clicked.connect(self.export_to_saldeo_excel)
        layout.addWidget(btn_export)
        layout.addStretch()
        self.pages.addWidget(page)

    def export_to_saldeo_excel(self):
        filepath, _ = QFileDialog.getSaveFileName(self, "Wskaż miejsce zapisu szablonu Saldeo", "", "Pliki Excel (*.xlsx)")
        if not filepath:
            return

        # Pełna i ścisła specyfikacja 56 nagłówków z pliku wzorcowego SaldeoSMART
        headers = [
            "Lp.", "Sufiks", "Własny opis faktury", "Metoda kasowa", "VAT marża", "Data wystawienia",
            "Data dostawy", "Termin płatności", "Wyślij do KSeF", "Wyślij do kontrahenta",
            "Nabywca (nazwa skrócona kontrahenta)", "Nazwa pełna kontrahenta", "Adres", "Kod pocztowy",
            "Miejscowość", "NIP", "REGON", "Kraj - skrót kraju w formacie ISO", "Adres e-mail",
            "Odbiorca (nazwa skrócona kontrahenta)", "Nazwa pełna (odbiorcy)", "Rola odbiorcy", "ID WEW",
            "Adres (odbiorcy)", "Kod pocztowy (odbiorcy)", "Miejscowość (odbiorcy)", "NIP (odbiorcy)",
            "REGON (odbiorcy)", "Kraj - skrót kraju w formacie ISO (odbiorcy)", "Adres e-mail (odbiorcy)",
            "Waluta", "Nazwa towaru", "PKWiU", "Ilość", "Jednostka", "Cena jedn. netto", "Cena jedn. brutto",
            "Stawka VAT", "Podstawa zastosowania stawki ZW", "Forma płatności", "Zapłacono", "Data wpłaty",
            "Konto bankowe", "Rodzaj środka transportu", "Data dopuszczenia", "Liczba rbh / Przebieg pojazdu",
            "Uwagi", "Wystawca faktury", "Grupa Towarowa", "Numer zamówienia", "Data zamówienia",
            "Numer umowy", "Data umowy", "Kategoria", "Opis", "Rejestr"
        ]

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Faktury"

        # 1. Zapis wiersza nagłówkowego
        ws.append(headers)

        # 2. Pobranie danych o powiązanych dokumentach z bazy SQL
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT d.id, d.numer, d.typ, d.cena_netto, d.data_wyst, f.nazwa, f.nip, f.miasto "
            "FROM dokumenty d JOIN firmy f ON d.firma_id = f.id"
        )
        rows = cursor.fetchall()
        conn.close()

        for idx, row in enumerate(rows, start=1):
            db_id, numer, typ, netto, data_wyst, f_nazwa, f_nip, f_miasto = row
            # Reprezentacja pustego szablonu (56 pustych komórek)
            row_data = [""] * len(headers)
            # Mapowanie pól bazy danych na indeksy kolumn struktury SaldeoSMART
            row_data[0] = numer          # Kolumna 'Lp.'
            row_data[5] = data_wyst      # Kolumna 'Data wystawienia'
            row_data[6] = data_wyst      # Kolumna 'Data dostawy'
            row_data[7] = data_wyst      # Kolumna 'Termin płatności'
            row_data[10] = f_nazwa       # Kolumna 'Nabywca (nazwa skrócona kontrahenta)'
            row_data[11] = f_nazwa       # Kolumna 'Nazwa pełna kontrahenta'
            row_data[14] = f_miasto      # Kolumna 'Miejscowość'
            row_data[15] = f_nip         # Kolumna 'NIP'
            row_data[30] = "PLN"        # Kolumna 'Waluta'
            row_data[31] = typ           # Kolumna 'Nazwa towaru'
            row_data[33] = 1             # Kolumna 'Ilość'
            row_data[34] = "szt."       # Kolumna 'Jednostka'
            row_data[35] = netto         # Kolumna 'Cena jedn. netto'
            row_data[37] = "23%"        # Kolumna 'Stawka VAT'
            row_data[39] = "przelew"    # Kolumna 'Forma płatności'
            ws.append(row_data)

        # Zapis fizyczny pliku skoroszytu Excel
        try:
            wb.save(filepath)
            QMessageBox.information(self, "Sukces eksportu", f"Plik integracyjny został wygenerowany poprawnie.\nŚcieżka: {filepath}")
        except Exception as e:
            QMessageBox.critical(self, "Błąd zapisu", f"Nie można było utworzyć pliku Excel. Powód:\n{str(e)}")


if __name__ == "__main__":
    app = QApplication(sys.argv)

    # KROK 1: Wymuszenie natychmiastowej inicjalizacji bazy danych
    # zanim okno logowania w ogóle zapyta o użytkowników!
    init_db()

    # Pokaż okno logowania
    login_window = LoginWindow()
    if login_window.exec() == QDialog.DialogCode.Accepted:
        pracownik_id = login_window.pracownik_id
        window = OfficeManagerApp(pracownik_id)
        window.show()
        sys.exit(app.exec())
    else:
        sys.exit()