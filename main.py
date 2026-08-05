import sys
import sqlite3
import datetime
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QListWidget, QStackedWidget, QLabel,
                             QTableWidget, QTableWidgetItem, QHeaderView,
                             QPushButton, QLineEdit, QFormLayout,
                             QSplitter, QComboBox, QMessageBox, QFileDialog,
                             QDialog, QDialogButtonBox, QProgressDialog)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
import openpyxl
import bcrypt
import requests
import json

DB_NAME = "biuro.db"


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
        self.sidebar.addItems(["Widok Miesięczny", "Klienci i Pracownicy", "Pracownicy i Uprawnienia", "CEIDG Import", "Eksport Saldeo"])
        main_layout.addWidget(self.sidebar)

        # 2. SEKCJA STRONY (STACKED WIDGET)
        self.pages = QStackedWidget()
        main_layout.addWidget(self.pages)

        # Inicjalizacja podstron
        self.create_monthly_view_page()
        self.create_companies_page()
        self.create_employees_page()
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
    # WIDOK 5: EKSPORT DO PLIKU EXCEL (SALDEOSMART - 56 KOLUMN)
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
