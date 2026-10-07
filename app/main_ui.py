from PyQt6.QtWidgets import QApplication
from app.utils.ui_imports import *

from app.db.dao_employees import get_employee_info
from app.db.dao_companies import get_company

from app.monthly_view.monthly_view_ui import MonthlyViewPage
from app.companies.company_ui import CompanyUI
from app.email.email_ui import EmailPage
from app.chat.chat_ui import ChatPage
from app.reports.reports_ui import ReportsPage
from app.admin.settings.settings_ui import GlobalSettingsPage
from app.invoices.faktury_ui import FakturyPage
from app.tasks.tasks_ui import TasksPage

from PyQt6.QtCore import QTimer
from typing import Optional


class LoginWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Logowanie")
        self.setFixedSize(420, 320)
        self.pracownik_id: Optional[int] = None
        self._build_ui()

    def _build_ui(self):
        self.setStyleSheet("""
            QDialog { background-color: #eae0d5; }
            QLabel { color: #1c70b7; font-weight: bold; }
            QLineEdit {
                padding: 8px;
                border-radius: 6px;
                border: 1px solid #b8b1a8;
                background-color: white;
            }
            QPushButton {
                background-color: #1c70b7;
                color: white;
                padding: 8px;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #155a8a; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)

        lbl_title = QLabel("Zaloguj się")
        lbl_title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        layout.addWidget(lbl_title)

        form = QFormLayout()
        self.email_input = QLineEdit()
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Email:", self.email_input)
        form.addRow("Hasło:", self.password_input)
        layout.addLayout(form)

        row = QHBoxLayout()
        btn_login = QPushButton("Zaloguj")
        btn_login.clicked.connect(self._on_login)
        btn_register = QPushButton("Zarejestruj")
        btn_register.clicked.connect(self._on_register)
        row.addWidget(btn_login)
        row.addWidget(btn_register)
        layout.addLayout(row)

    def _on_login(self):
        from app.utils.crypto import login_user
        email = self.email_input.text().strip()
        password = self.password_input.text()
        if not email or not password:
            QMessageBox.warning(self, "Błąd", "Uzupełnij email i hasło")
            return
        try:
            result = login_user(email, password)
        except Exception as e:
            QMessageBox.critical(self, "Błąd", str(e))
            return
        if isinstance(result, tuple):
            ok, user_id = result[0], result[1]
        else:
            ok, user_id = bool(result), result
        if ok:
            self.pracownik_id = user_id
            self.accept()
        else:
            QMessageBox.critical(self, "Błąd", "Nieprawidłowy email lub hasło")

    def _on_register(self):
        from app.utils.crypto import register_user
        dlg = QDialog(self)
        dlg.setWindowTitle("Rejestracja")
        dlg.setFixedSize(420, 320)
        dlg.setStyleSheet(self.styleSheet())

        form = QFormLayout()
        email = QLineEdit()
        imie = QLineEdit()
        nazwisko = QLineEdit()
        haslo = QLineEdit()
        haslo.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Email:", email)
        form.addRow("Imię:", imie)
        form.addRow("Nazwisko:", nazwisko)
        form.addRow("Hasło:", haslo)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)

        layout = QVBoxLayout(dlg)
        layout.addLayout(form)
        layout.addWidget(btns)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            try:
                ok = register_user(email.text(), haslo.text(),
                                   imie=imie.text(), nazwisko=nazwisko.text(),
                                   is_admin_flag=False)
            except Exception as e:
                QMessageBox.critical(self, "Błąd", str(e))
                return
            if isinstance(ok, tuple):
                ok = ok[0]
            if ok:
                QMessageBox.information(self, "OK", "Rejestracja udana.")
            else:
                QMessageBox.critical(self, "Błąd", "Rejestracja nie powiodła się.")


class OfficeManagerApp(QMainWindow):
    def __init__(self, pracownik_id: Optional[int] = None):
        super().__init__()
        if pracownik_id is None:
            self.pracownik_id = self._show_login_dialog()
            if not self.pracownik_id:
                QApplication.instance().quit()
                return
        else:
            self.pracownik_id = pracownik_id

        self.setWindowTitle("System Zarządzania Biurem Rachunkowego")
        self.resize(1400, 850)

        # elementy dla nagłówkowego timera
        self.lbl_live_timer = None
        self._header_timer = None

        self._build_ui()

    def _show_login_dialog(self):
        dlg = LoginWindow(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.pracownik_id
        return None

    def _build_ui(self):
        self.setStyleSheet("""
            QWidget { background-color: #eae0d5; font-family: Arial; }
            QLabel { color: #2c3e50; }
            QPushButton {
                background-color: #1c70b7;
                color: white;
                padding: 8px 14px;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #155a8a; }
        """)

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(240)
        self.sidebar.setStyleSheet("""
            QListWidget {
                background-color: #1c70b7;
                color: white;
                border: none;
                font-size: 16px;
            }
            QListWidget::item { padding: 18px; }
            QListWidget::item:selected { background-color: #155a8a; font-weight: bold; }
            QListWidget::item:hover { background-color: #1668a3; }
        """)

        self.sidebar.addItems([
            "Widok miesięczny",
            "Klienci",
            "Email",
            "Chat",
            "Faktury",
            "Raporty",
            "Zadania",
            "Ustawienia"
        ])
        self.sidebar.currentRowChanged.connect(self.zmien_zakladke)
        main_layout.addWidget(self.sidebar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)

        header = QWidget()
        header.setStyleSheet("background-color: #ffffff; border-bottom: 2px solid #d0c7bd;")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 12, 20, 12)

        emp = get_employee_info(self.pracownik_id)
        name = f"{emp['imie']} {emp['nazwisko']}" if emp else "Nieznany"
        lbl_user = QLabel(f"<b>Zalogowany:</b> {name}")
        lbl_user.setStyleSheet("font-size: 16px;")
        header_layout.addWidget(lbl_user)

        # --- Live timer widget (po prawej stronie nagłówka) ---
        # kontener i layout
        live_container = QWidget()
        live_layout = QHBoxLayout(live_container)
        live_layout.setContentsMargins(0, 0, 0, 0)
        live_layout.setSpacing(8)

        # tytuł
        lbl_live_title = QLabel("Aktywny stoper:")
        lbl_live_title.setStyleSheet("font-weight: bold;")
        live_layout.addWidget(lbl_live_title)

        # główna etykieta pokazująca: Nazwa firmy — czas — Czynność
        self.lbl_live_timer = QLabel("Brak aktywnego stoperu")
        self.lbl_live_timer.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lbl_live_timer.setStyleSheet("font-size: 12px;")
        live_layout.addWidget(self.lbl_live_timer)

        header_layout.addStretch()
        header_layout.addWidget(live_container)

        content_layout.addWidget(header)

        self.pages = QStackedWidget()
        content_layout.addWidget(self.pages)
        main_layout.addWidget(content)

        self.page_monthly = MonthlyViewPage(self.pracownik_id)
        self.page_companies = CompanyUI(self)
        self.page_email = EmailPage(self.pracownik_id)
        self.page_chat = ChatPage(self.pracownik_id)
        self.page_faktury = FakturyPage(self.pracownik_id)
        self.page_raporty = ReportsPage(self.pracownik_id)
        self.page_tasks = TasksPage(self.pracownik_id)
        self.page_settings = GlobalSettingsPage(self.pracownik_id, True, self)

        self.pages.addWidget(self.page_monthly)
        self.pages.addWidget(self.page_companies)
        self.pages.addWidget(self.page_email)
        self.pages.addWidget(self.page_chat)
        self.pages.addWidget(self.page_faktury)
        self.pages.addWidget(self.page_raporty)
        self.pages.addWidget(self.page_tasks)
        self.pages.addWidget(self.page_settings)

        self.sidebar.setCurrentRow(0)
        self.pages.setCurrentIndex(0)

        # Timer aktualizujący nagłówek co sekundę
        self._header_timer = QTimer(self)
        self._header_timer.setInterval(1000)
        self._header_timer.timeout.connect(self._update_header_timer)
        self._header_timer.start()

    def _update_header_timer(self):
        """
        Odczytuje z MonthlyViewPage.logic aktywne stopery (ksiegowosc/kadry)
        i aktualizuje tekst w nagłówku: "Nazwa firmy — HH:MM:SS — Czynność".
        """
        try:
            logic = getattr(self.page_monthly, "logic", None)
            if not logic:
                if self.lbl_live_timer:
                    self.lbl_live_timer.setText("Brak aktywnego stoperu")
                return

            labels = logic.get_active_timer_labels()
            self.lbl_live_timer.setText(" | ".join(labels) if labels else "Brak aktywnego stoperu")
        except Exception:
            # nie przerywamy UI; ewentualnie loguj
            if self.lbl_live_timer:
                self.lbl_live_timer.setText("Brak aktywnego stoperu")

    def zmien_zakladke(self, index):
        if 0 <= index < self.pages.count():
            self.pages.setCurrentIndex(index)

    def closeEvent(self, event):
        # zatrzymaj timer przy zamykaniu
        try:
            if self._header_timer and self._header_timer.isActive():
                self._header_timer.stop()
        except Exception:
            pass
        super().closeEvent(event)
