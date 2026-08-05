import sys
import sqlite3
import datetime
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QListWidget, QStackedWidget, QLabel,
                             QTableWidget, QTableWidgetItem, QHeaderView,
                             QPushButton, QLineEdit, QFormLayout,
                             QSplitter, QComboBox, QMessageBox, QFileDialog,
                             QDialog, QDialogButtonBox, QProgressDialog, QTextEdit,
                             QTabWidget, QCheckBox, QSpinBox)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
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
    """Zweryfikuj hasło"""
    return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))


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
def search_ceidg_by_nip(nip):
    """Szukaj firmy w CEIDG po NIP
    Zwraca słownik z danymi firmy lub None jeśli nie znaleziono
    """
    try:
        nip_clean = nip.replace('-', '').replace(' ', '')
        
        # Endpoint publiczny CEIDG (https://api.ceidg.gov.pl/)
        # Alternatywnie można użyć GUS API lub innego dostawcy
        url = f"https://api.ceidg.gov.pl/ceidg/CompanyData/GetByNip/{nip_clean}"
        
        # Timeout 10 sekund
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if data:
                return {
                    'nip': data.get('Nip'),
                    'nazwa': data.get('Regon'),  # W odpowiedzi CEIDG zmienią się nazwy pól
                    'regon': data.get('Regon'),
                    'adres': data.get('Address'),
                    'miasto': data.get('City'),
                    'kod_pocztowy': data.get('PostalCode')
                }
        return None
    except requests.RequestException as e:
        print(f"Błąd połączenia CEIDG: {e}")
        return None
    except Exception as e:
        print(f"Błąd podczas parsowania CEIDG: {e}")
        return None


def search_ceidg_by_name(nazwa):
    """Szukaj firm w CEIDG po nazwie
    Zwraca listę słowników z danymi firm
    """
    try:
        # Parametr wyszukiwania
        params = {'Query': nazwa, 'PageSize': 10}
        url = "https://api.ceidg.gov.pl/ceidg/CompanyData/Search"
        
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            companies = []
            for item in data.get('Results', []):
                companies.append({
                    'nip': item.get('Nip'),
                    'nazwa': item.get('Name'),
                    'regon': item.get('Regon'),
                    'adres': item.get('Address'),
                    'miasto': item.get('City'),
                    'kod_pocztowy': item.get('PostalCode')
                })
            return companies
        return []
    except requests.RequestException as e:
        print(f"Błąd połączenia CEIDG: {e}")
        return []
    except Exception as e:
        print(f"Błąd podczas parsowania CEIDG: {e}")
        return []


# --- FUNKCJE OBSŁUGI EMAIL (IMAP/SMTP) ---

class EmailSyncer:
    """Klasa do synchronizacji emaili z serwera IMAP"""
    
    @staticmethod
    def connect_imap(serwer_imap, port_imap, email_address, encrypted_password):
        """Połącz się z serwerem IMAP"""
        try:
            password = decrypt_password(encrypted_password)
            if port_imap == 993:
                server = imaplib.IMAP4_SSL(serwer_imap, port_imap)
            else:
                server = imaplib.IMAP4(serwer_imap, port_imap)
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
            
            # Listuj foldery
            status, mailboxes = imap.list()
            for mailbox in mailboxes:
                folder_name = mailbox.decode().split('"')[-2]
                
                # Wybierz folder
                status, count = imap.select(folder_name)
                
                # Pobierz wiadomości
                status, message_nums = imap.search(None, 'ALL')
                
                for num in message_nums[0].split()[-50:]:  # Ostatnie 50 wiadomości
                    status, msg_data = imap.fetch(num, '(RFC822)')
                    msg = email.message_from_bytes(msg_data[0][1])
                    
                    message_id = msg.get('Message-ID', f"unknown_{num}_{datetime.datetime.now().timestamp()}")
                    od = msg.get('From', 'Unknown')
                    do = msg.get('To', '')
                    cc = msg.get('Cc', '')
                    temat = msg.get('Subject', '(brak tematu)')
                    data = msg.get('Date', datetime.datetime.now().isoformat())
                    
                    # Pobierz treść
                    tresc = ""
                    if msg.is_multipart():
                        for part in msg.get_payload():
                            if part.get_content_type() == 'text/plain':
                                tresc = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                                break
                    else:
                        tresc = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
                    
                    # Stosuj filtry
                    final_folder = folder_name
                    cursor.execute("""
                        SELECT folder_docelowy FROM reguly_filtrow
                        WHERE konto_id = ? AND aktywna = 1
                        AND (od IS NULL OR od LIKE ?)
                        AND (temat_zawiera IS NULL OR ? LIKE '%' || temat_zawiera || '%')
                    """, (konto_id, od, temat))
                    filter_result = cursor.fetchone()
                    if filter_result:
                        final_folder = filter_result[0]
                    
                    # Dodaj do bazy (ignoruj duplikaty)
                    try:
                        cursor.execute("""
                            INSERT INTO wiadomosci 
                            (konto_id, message_id, od, do, cc, temat, tresc, data_otrzymania, folder)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (konto_id, message_id, od, do, cc, temat, tresc[:500], data, final_folder))
                    except sqlite3.IntegrityError:
                        pass  # Wiadomość już istnieje
            
            imap.close()
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
            
            # Wyślij przez SMTP
            with smtplib.SMTP(smtp_server, smtp_port) as server:
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


class OfficeManagerApp(QMainWindow):
    def __init__(self, pracownik_id=None):
        super().__init__()
        self.pracownik_id = pracownik_id  # ID zalogowanego pracownika
        
        self.setWindowTitle("System Zarządzania Biurem Rachunkowym v1.1")
        self.resize(1250, 750)

        # Zmienne stopera
        self.aktywna_firma_stoper = None
        self.czas_start = None
        self.aktualny_miesiac = datetime.datetime.now().strftime("%Y-%m")

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
        self.pages = QStackedWidget()
        main_layout.addWidget(self.pages)

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
        
        # Informacja o zalogowanym użytkowniku
        emp_info = get_employee_info(self.pracownik_id)
        if emp_info:
            emp_name = f"{emp_info[2]} {emp_info[3]}"
        else:
            emp_name = "Nieznany"
        
        lbl_user = QLabel(f"<b>Zalogowany:</b> {emp_name}")
        lbl_user.setFont(QFont("Arial", 11))
        lbl_user.setStyleSheet("color: #3498db; margin-left: 20px;")
        header.addWidget(lbl_user)

        self.lbl_stoper_status = QLabel("Stoper: Zatrzymany")
        self.lbl_stoper_status.setStyleSheet(
            "color: #7f8c8d; font-weight: bold; font-size: 13px; background-color: #f8f9fa; padding: 5px; border-radius: 4px;")
        header.addWidget(self.lbl_stoper_status, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addLayout(header)

        # Główna tabela stanu
        self.table_monthly = QTableWidget()
        self.table_monthly.setColumnCount(8)
        self.table_monthly.setHorizontalHeaderLabels([
            "ID", "Nazwa Firmy", "Ilość dokumentów", "Etap pracy", "Czas pracy (Suma)", "Stoper", "Akcja Saldeo",
            "Zapisz stan"
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

        etapy = ["Brak", "Dostarczone", "Komplet dokumentów", "Zaksięgowane", "Wysłane"]
        kolory_etapow = {
            "Dostarczone": "#e67e22",
            "Komplet dokumentów": "#f1c40f",
            "Zaksięgowane": "#3498db",
            "Wysłane": "#2ecc71",
            "Brak": "#ffffff"
        }

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
            cb_etap.addItems(etapy)
            cb_etap.setCurrentText(etap)
            cb_etap.setStyleSheet(f"background-color: {kolory_etapow.get(etap, '#fff')}; padding: 3px;")
            cb_etap.currentTextChanged.connect(lambda txt, cb=cb_etap: cb.setStyleSheet(
                f"background-color: {kolory_etapow.get(txt, '#fff')}; padding: 3px;"))
            self.table_monthly.setCellWidget(row_idx, 3, cb_etap)

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

            # Zapisz pojedynczy wiersz
            btn_save = QPushButton("Zapisz")
            btn_save.setStyleSheet("background-color: #34495e; color: white; font-weight: bold;")
            btn_save.clicked.connect(lambda checked, r=row_idx, fid=f_id: self.zapisz_wiersz_statusu(r, fid))
            self.table_monthly.setCellWidget(row_idx, 7, btn_save)

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
            self.lbl_stoper_status.setText(f"Mierzysz czas dla: {nazwa}")
        elif self.aktywna_firma_stoper == firma_id:
            # Wyłączenie mierzenia czasu i zapis do bazy
            roznica_sekund = int((datetime.datetime.now() - self.czas_start).total_seconds())
            cursor.execute("UPDATE firmy SET sekundy_pracy = sekundy_pracy + ? WHERE id = ?", (roznica_sekund, firma_id))
            conn.commit()
            # Reset stopera
            self.aktywna_firma_stoper = None
            self.czas_start = None
            self.lbl_stoper_status.setText("Stoper: Zatrzymany")
        else:
            QMessageBox.warning(self, "Uwaga", "Inny czasomierz jest już aktywny! Zatrzymaj go najpierw.")

        conn.close()
        self.refresh_monthly_table()

    def aktualizuj_stoper_gui(self):
        if self.aktywna_firma_stoper is not None and self.czas_start is not None:
            roznica = int((datetime.datetime.now() - self.czas_start).total_seconds())
            self.lbl_stoper_status.setText(f"⏱️ ID:{self.aktywna_firma_stoper} pracuje od: {roznica} sek.")

    def zapisz_wiersz_statusu(self, row_idx, firma_id):
        txt_widget = self.table_monthly.cellWidget(row_idx, 2)
        cb_widget = self.table_monthly.cellWidget(row_idx, 3)

        txt_docs = txt_widget.text() if txt_widget is not None else "0"
        cb_etap = cb_widget.currentText() if cb_widget is not None else "Brak"

        try:
            ilosc_dok = int(txt_docs)
        except ValueError:
            QMessageBox.critical(self, "Błąd", "Pole ilość dokumentów musi posiadać wartość liczbową!")
            return

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO statusy_miesieczne (firma_id, miesiac, etap, ilosc_dok)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(firma_id, miesiac) DO UPDATE SET etap = excluded.etap, ilosc_dok = excluded.ilosc_dok
            """,
            (firma_id, self.aktualny_miesiac, cb_etap, ilosc_dok)
        )
        conn.commit()
        conn.close()
        QMessageBox.information(self, "Zapisano", "Stan firmy został zaktualizowany pomyślnie.")

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

        conn.commit()
        conn.close()
        self.refresh_monthly_table()
        QMessageBox.information(self, "Dodano dokument", f"Utworzono fakturę próbną: {nr_faktury}\nTrafi ona bezpośrednio do raportu Saldeo.")

    # =========================================================================
    # WIDOK 2: KLIENCI I PRACOWNICY (KADRY: UoP, UZ, UoD)
    # =========================================================================
    def create_companies_page(self):
        page = QWidget()
        layout = QHBoxLayout(page)

        # Tabela (Lewa strona splitter'a)
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.addWidget(QLabel("Zarejestrowane firmy i stan zatrudnienia kadr"))
        self.table_firms = QTableWidget()
        self.table_firms.setColumnCount(6)
        self.table_firms.setHorizontalHeaderLabels(["ID", "Nazwa firmy", "NIP", "Pracownicy (UoP)", "Zlecenia (UZ)", "O Dzieło (UoD)"])
        self.table_firms.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        left_layout.addWidget(self.table_firms)

        # Formularz wprowadzania danych (Prawa strona splitter'a)
        right_widget = QWidget()
        right_widget.setFixedWidth(340)
        right_layout = QVBoxLayout(right_widget)
        right_layout.addWidget(QLabel("Dodaj kontrahenta i kadry"))
        form_layout = QFormLayout()
        self.in_nazwa = QLineEdit()
        self.in_nip = QLineEdit()
        self.in_miasto = QLineEdit()
        self.in_uop = QLineEdit("0")
        self.in_uz = QLineEdit("0")
        self.in_uod = QLineEdit("0")
        form_layout.addRow("Nazwa pełna:", self.in_nazwa)
        form_layout.addRow("NIP:", self.in_nip)
        form_layout.addRow("Miejscowość:", self.in_miasto)
        form_layout.addRow("Pracownicy UoP:", self.in_uop)
        form_layout.addRow("Umowy Zlecenia (UZ):", self.in_uz)
        form_layout.addRow("Umowy o Dzieło (UoD):", self.in_uod)
        right_layout.addLayout(form_layout)
        btn_save_f = QPushButton("Zapisz do bazy")
        btn_save_f.setStyleSheet("background-color: #3498db; color: white; font-weight: bold; padding: 10px; font-size: 13px;")
        btn_save_f.clicked.connect(self.save_company_to_db)
        right_layout.addWidget(btn_save_f)
        right_layout.addStretch()

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        layout.addWidget(splitter)
        self.pages.addWidget(page)

    def refresh_firms_table(self):
        self.table_firms.setRowCount(0)
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # Filtrowanie firm na podstawie uprawnień
        if is_admin(self.pracownik_id):
            # Admin widzi wszystkie firmy
            cursor.execute("SELECT id, nazwa, nip, uop, uz, uod FROM firmy ORDER BY nazwa")
        else:
            # Zwykły pracownik widzi tylko przypisane firmy
            cursor.execute("""
                SELECT DISTINCT f.id, f.nazwa, f.nip, f.uop, f.uz, f.uod 
                FROM firmy f
                WHERE f.id IN (
                    SELECT firma_id FROM pracownik_firma WHERE pracownik_id = ?
                )
                ORDER BY f.nazwa
            """, (self.pracownik_id,))
        
        for row_idx, row in enumerate(cursor.fetchall()):
            self.table_firms.insertRow(row_idx)
            for col_idx, value in enumerate(row):
                self.table_firms.setItem(row_idx, col_idx, QTableWidgetItem(str(value)))
        conn.close()

    def save_company_to_db(self):
        nazwa = self.in_nazwa.text().strip()
        nip = self.in_nip.text().strip()
        miasto = self.in_miasto.text().strip()
        try:
            uop = int(self.in_uop.text())
            uz = int(self.in_uz.text())
            uod = int(self.in_uod.text())
        except ValueError:
            QMessageBox.critical(self, "Błąd", "Pola dotyczące liczby pracowników muszą zawierać wyłącznie cyfry!")
            return
        if not nazwa or not nip:
            QMessageBox.warning(self, "Błąd", "Pola 'Nazwa pełna' oraz 'NIP' są wymagane do rejestracji firmy!")
            return
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO firmy (nazwa, nip, miasto, uop, uz, uod) VALUES (?, ?, ?, ?, ?, ?)",
                (nazwa, nip, miasto, uop, uz, uod)
            )
            conn.commit()
            QMessageBox.information(self, "Zapisano", "Nowy podmiot został dodany do bazy danych.")
            self.refresh_firms_table()
            # Czyszczenie pól formularza
            self.in_nazwa.clear()
            self.in_nip.clear()
            self.in_miasto.clear()
            self.in_uop.setText("0")
            self.in_uz.setText("0")
            self.in_uod.setText("0")
        except sqlite3.IntegrityError:
            QMessageBox.critical(self, "Błąd", "Podany numer NIP figuruje już w systemie!")
        finally:
            conn.close()

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
        self.assign_rola_combo.addItems(["księgowa", "kadrowa", "manager", "admin"])
        
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
        
        # Tytuł
        lbl_title = QLabel("Import firm z rejestru CEIDG")
        lbl_title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        layout.addWidget(lbl_title)
        
        lbl_info = QLabel("Wyszukaj firmy w Centralnym Rejestrze Ewidencji i Informacji o Działalności Gospodarczej")
        layout.addWidget(lbl_info)
        
        # Formularz wyszukiwania
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
        
        # Tabela wyników
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
                # Wyszukiwanie po NIP
                result = search_ceidg_by_nip(search_query)
                if result:
                    self.show_ceidg_result([result])
                else:
                    QMessageBox.information(self, "Wynik", "Nie znaleziono firmy z tym NIP")
            else:
                # Wyszukiwanie po nazwie
                results = search_ceidg_by_name(search_query)
                if results:
                    self.show_ceidg_result(results)
                else:
                    QMessageBox.information(self, "Wynik", "Nie znaleziono firm o tej nazwie")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Błąd wyszukiwania: {str(e)}")
    
    def show_ceidg_result(self, companies):
        """Pokaż wyniki wyszukiwania w tabeli"""
        self.table_ceidg_results.setRowCount(0)
        
        for row_idx, company in enumerate(companies):
            self.table_ceidg_results.insertRow(row_idx)
            
            # Dane
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
            
            # Przycisk importu
            btn_import = QPushButton("Importuj")
            btn_import.setStyleSheet("background-color: #27ae60; color: white;")
            btn_import.clicked.connect(lambda checked, c=company: self.import_company_from_ceidg(c))
            self.table_ceidg_results.setCellWidget(row_idx, 5, btn_import)
    
    def import_company_from_ceidg(self, company_data):
        """Importuj firmę z CEIDG do bazy danych"""
        nip = company_data.get('nip')
        nazwa = company_data.get('nazwa')
        miasto = company_data.get('miasto')
        
        if not nip or not nazwa:
            QMessageBox.warning(self, "Błąd", "Brakuje danych firmy")
            return
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        try:
            cursor.execute(
                "INSERT INTO firmy (nazwa, nip, miasto) VALUES (?, ?, ?)",
                (nazwa, nip, miasto)
            )
            conn.commit()
            QMessageBox.information(self, "Sukces", f"Firma {nazwa} została zaimportowana!")
            self.refresh_firms_table()
        except sqlite3.IntegrityError:
            QMessageBox.warning(self, "Błąd", "Firma z tym NIP już istnieje w bazie danych")
        finally:
            conn.close()

    # =========================================================================
    # WIDOK 4B: EMAIL CLIENT
    # =========================================================================
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
        
        # === KARTA 1: ZARZĄDZANIE KONTAMI ===
        accounts_tab = self._create_email_accounts_tab()
        self.email_tabs.addTab(accounts_tab, "Konta Email")
        
        # === KARTA 2: CZYTANIE EMAILI ===
        reader_tab = self._create_email_reader_tab()
        self.email_tabs.addTab(reader_tab, "Przegląd Emaili")
        
        # === KARTA 3: FILTRY EMAILI ===
        filters_tab = self._create_email_filters_tab()
        self.email_tabs.addTab(filters_tab, "Filtry")
        
        layout.addWidget(self.email_tabs)
        self.pages.addWidget(page)
    
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
        self.imap_port.setValue(993)
        self.smtp_server = QLineEdit()
        self.smtp_port = QSpinBox()
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
        btn_sync.clicked.connect(self.sync_all_emails)
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
        self.email_folder_combo.addItems(["INBOX", "Sent", "Drafts", "Trash"])
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
        
        btn_delete = QPushButton("Usuń")
        btn_delete.setStyleSheet("background-color: #e74c3c; color: white;")
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
        info_label = QLabel("Uwaga: Jeśli pola 'Od' i 'Temat' są puste, filtr stosuje się do wszystkich emaili\nFiltry są stosowane automatycznie przy synchronizacji")
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
        """Odśwież combo boxy kont email"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, email FROM konta_email WHERE pracownik_id = ?", (self.pracownik_id,))
        accounts = cursor.fetchall()
        
        self.email_account_combo.clear()
        for kid, email_addr in accounts:
            self.email_account_combo.addItem(email_addr, kid)
        
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
    
    def sync_all_emails(self):
        """Synchronizuj wszystkie emaile"""
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM konta_email WHERE pracownik_id = ?", (self.pracownik_id,))
        accounts = cursor.fetchall()
        conn.close()
        
        if not accounts:
            QMessageBox.warning(self, "Błąd", "Brak kont email do synchronizacji")
            return
        
        # Synchronizuj każde konto
        for (konto_id,) in accounts:
            success = EmailSyncer.sync_emails(konto_id, self.pracownik_id)
            if success:
                QMessageBox.information(self, "Sukces", f"Synchronizacja konta {konto_id} zakończona")
            else:
                QMessageBox.warning(self, "Błąd", f"Błąd synchronizacji konta {konto_id}")
        
        self.refresh_email_list()
    
    def refresh_email_list(self):
        """Odśwież listę emaili"""
        konto_id = self.email_account_combo.currentData()
        folder = self.email_folder_combo.currentText()
        
        if not konto_id:
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
            self.table_email_list.setItem(row_idx, 1, QTableWidgetItem(od[:50]))
            self.table_email_list.setItem(row_idx, 2, QTableWidgetItem(temat[:60]))
            self.table_email_list.setItem(row_idx, 3, QTableWidgetItem(str(data)))
            self.table_email_list.setItem(row_idx, 4, QTableWidgetItem("✓" if przeczytana else ""))
        
        conn.close()
    
    def on_email_selected(self, item):
        """Pokaż treść wybranego emaila"""
        row = item.row()
        msg_id_item = self.table_email_list.item(row, 0)
        msg_id = int(msg_id_item.text())
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT od, temat, tresc FROM wiadomosci WHERE id = ?", (msg_id,))
        result = cursor.fetchone()
        conn.close()
        
        if result:
            od, temat, tresc = result
            self.email_content.setText(f"Od: {od}\nTemat: {temat}\n\n{tresc}")
    
    def reply_to_email(self):
        """Odpowiedź na emaila"""
        QMessageBox.information(self, "Info", "Funkcja odpowiedzi - będzie zaimplementowana")
    
    def forward_email(self):
        """Prześlij dalej emaila"""
        QMessageBox.information(self, "Info", "Funkcja przesyłania - będzie zaimplementowana")

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
            self.chat_conversations.addItem(nazwa, konwersacja_id)
        
        conn.close()
    
    def refresh_chat_display(self):
        """Odśwież wiadomości w wybranej konwersacji"""
        if self.chat_conversations.currentRow() < 0:
            return
        
        current_item = self.chat_conversations.currentItem()
        if not current_item:
            return
        
        konwersacja_id = current_item.data(Qt.ItemDataRole.UserRole)
        self.chat_messages.clear()
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT p.imie, p.nazwisko, wc.tresc, wc.data_wysylki
            FROM wiadomosci_chat wc
            JOIN pracownicy p ON wc.pracownik_id = p.id
            WHERE wc.konwersacja_id = ?
            ORDER BY wc.data_wysylki ASC
        """, (konwersacja_id,))
        
        for imie, nazwisko, tresc, data in cursor.fetchall():
            self.chat_messages.append(f"<b>{imie} {nazwisko}</b> ({data}):\n{tresc}\n")
        
        conn.close()
        self.chat_input.setFocus()
    
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
        konwersacja_id = current_item.data(Qt.ItemDataRole.UserRole)
        
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
    
    # Pokaż okno logowania
    login_window = LoginWindow()
    if login_window.exec() == QDialog.DialogCode.Accepted:
        pracownik_id = login_window.pracownik_id
        window = OfficeManagerApp(pracownik_id)
        window.show()
        sys.exit(app.exec())
    else:
        # Zamknięcie aplikacji jeśli użytkownik anuluje logowanie
        sys.exit()
