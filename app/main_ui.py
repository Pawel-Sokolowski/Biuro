"""Main application entry point with login and navigation."""

import logging
from typing import Optional
from PyQt6.QtWidgets import QApplication

from app.utils.ui_imports import *
from app.utils.app_header import AppHeader
from app.db.dao_employees import get_employee_info

from app.monthly_view.monthly_view_ui import MonthlyViewPage
from app.companies.company_ui import CompaniesPage
from app.email.email_ui import EmailPage
from app.chat.chat_ui import ChatPage
from app.reports.reports_ui import ReportsPage
from app.admin.global_settings_ui import GlobalSettingsPage
from app.invoices.faktury_ui import FakturyPage

logger = logging.getLogger(__name__)

# Application constants
APP_TITLE = "System Zarządzania Biurem Rachunkowego"


class LoginWindow(QDialog):
    """User login and registration dialog."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Logowanie")
        self.setFixedSize(420, 380)
        self.pracownik_id: Optional[int] = None
        self.email_input = None
        self.password_input = None
        self._build_ui()

    def _build_ui(self):
        """Build login form UI."""
        self.setStyleSheet(STYLE_DIALOG)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(MARGIN_LARGE, MARGIN_LARGE, MARGIN_LARGE, MARGIN_LARGE)
        layout.setSpacing(SPACING_NORMAL)

        # Title
        lbl_title = QLabel("Zaloguj się")
        font = QFont("Arial", *FONT_TITLE[1:])
        lbl_title.setFont(font)
        layout.addWidget(lbl_title)

        # Form
        form = QFormLayout()
        form.setSpacing(SPACING_NORMAL)
        
        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("example@example.com")
        
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Wpisz hasło")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        
        form.addRow("Email:", self.email_input)
        form.addRow("Hasło:", self.password_input)
        layout.addLayout(form)

        layout.addStretch()

        # Buttons
        button_row = QHBoxLayout()
        button_row.setSpacing(SPACING_NORMAL)
        
        btn_login = create_primary_button("Zaloguj")
        btn_login.clicked.connect(self._on_login)
        
        btn_register = create_secondary_button("Zarejestruj")
        btn_register.clicked.connect(self._on_register)
        
        button_row.addWidget(btn_login)
        button_row.addWidget(btn_register)
        layout.addLayout(button_row)

    def _on_login(self):
        """Handle login button click."""
        from app.utils.crypto import login_user
        
        email = self.email_input.text().strip()
        password = self.password_input.text()
        
        if not email or not password:
            QMessageBox.warning(self, "Błąd", "Uzupełnij email i hasło")
            return
        
        try:
            result = login_user(email, password)
            if isinstance(result, tuple) and len(result) >= 2:
                ok, user_id = result[0], result[1]
            else:
                QMessageBox.critical(self, "Błąd", "Nieoczekiwana odpowiedź serwera")
                logger.error(f"Unexpected login response format: {result}")
                return
            
            if ok and user_id:
                self.pracownik_id = user_id
                logger.info(f"User {user_id} logged in successfully")
                self.accept()
            else:
                msg = result[2] if isinstance(result, tuple) and len(result) > 2 else "Nieprawidłowy email lub hasło"
                QMessageBox.critical(self, "Błąd", msg)
        except Exception as e:
            logger.error(f"Login error: {e}")
            QMessageBox.critical(self, "Błąd", f"Błąd logowania: {str(e)}")

    def _on_register(self):
        """Handle register button click."""
        from app.utils.crypto import register_user
        
        dlg = QDialog(self)
        dlg.setWindowTitle("Rejestracja")
        dlg.setFixedSize(420, 380)
        dlg.setStyleSheet(STYLE_DIALOG)

        form = QFormLayout()
        form.setSpacing(SPACING_NORMAL)
        
        email = QLineEdit()
        email.setPlaceholderText("example@example.com")
        
        imie = QLineEdit()
        imie.setPlaceholderText("Jan")
        
        nazwisko = QLineEdit()
        nazwisko.setPlaceholderText("Kowalski")
        
        haslo = QLineEdit()
        haslo.setPlaceholderText("Minimalna długość: 8 znaków")
        haslo.setEchoMode(QLineEdit.EchoMode.Password)
        
        form.addRow("Email:", email)
        form.addRow("Imię:", imie)
        form.addRow("Nazwisko:", nazwisko)
        form.addRow("Hasło:", haslo)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)

        layout = QVBoxLayout(dlg)
        layout.addLayout(form)
        layout.addWidget(btns)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            email_text = email.text().strip()
            password_text = haslo.text()
            
            if not email_text or not password_text:
                QMessageBox.warning(dlg, "Błąd", "Email i hasło są wymagane")
                return
            
            if len(password_text) < 8:
                QMessageBox.warning(dlg, "Błąd", "Hasło musi mieć przynajmniej 8 znaków")
                return
            
            try:
                result = register_user(
                    email_text,
                    password_text,
                    imie=imie.text().strip(),
                    nazwisko=nazwisko.text().strip(),
                    is_admin_flag=False
                )
                
                if isinstance(result, tuple) and len(result) >= 1:
                    ok = result[0]
                else:
                    ok = bool(result)
                
                if ok:
                    logger.info(f"User registered: {email_text}")
                    QMessageBox.information(self, "OK", "Rejestracja udana. Możesz się teraz zalogować.")
                else:
                    msg = result[2] if isinstance(result, tuple) and len(result) > 2 else "Rejestracja nie powiodła się."
                    QMessageBox.critical(self, "Błąd", msg)
            except Exception as e:
                logger.error(f"Registration error: {e}")
                QMessageBox.critical(self, "Błąd", f"Błąd rejestracji: {str(e)}")


class OfficeManagerApp(QMainWindow):
    """Main application window."""
    
    def __init__(self, pracownik_id: Optional[int] = None):
        super().__init__()
        self.pracownik_id = pracownik_id
        self.sidebar = None
        self.pages = None
        self.app_header = None
        
        if pracownik_id is None:
            self.pracownik_id = self._show_login_dialog()
            if not self.pracownik_id:
                logger.warning("User cancelled login dialog")
                QApplication.instance().quit()
                return

        self.setWindowTitle(APP_TITLE)
        self.resize(APP_WIDTH, APP_HEIGHT)
        self._build_ui()

    def _show_login_dialog(self) -> Optional[int]:
        """Show and handle login dialog."""
        dlg = LoginWindow(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.pracownik_id
        return None

    def _build_ui(self):
        """Build main application UI."""
        self.setStyleSheet(STYLE_MAIN_WINDOW)

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # --- Header with user info and time tracker ---
        self.app_header = AppHeader(self.pracownik_id)
        main_layout.addWidget(self.app_header)

        # --- Content area with sidebar ---
        content_layout = QHBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # --- Sidebar ---
        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(SIDEBAR_WIDTH)
        self.sidebar.setStyleSheet(STYLE_SIDEBAR)

        sidebar_items = [
            "📅 Widok miesięczny",
            "🏢 Klienci",
            "✉️  Email",
            "💬 Chat",
            "📄 Faktury",
            "📊 Raporty",
            "⚙️  Ustawienia"
        ]
        self.sidebar.addItems(sidebar_items)
        self.sidebar.currentRowChanged.connect(self.zmien_zakladke)
        content_layout.addWidget(self.sidebar)

        # --- Pages ---
        self.pages = QStackedWidget()
        content_layout.addWidget(self.pages, 1)

        # Add content to main layout
        content_widget = QWidget()
        content_widget.setLayout(content_layout)
        main_layout.addWidget(content_widget, 1)

        # Initialize all pages
        try:
            page_monthly = MonthlyViewPage(self.pracownik_id)
            page_companies = CompaniesPage(self.pracownik_id)
            page_email = EmailPage(self.pracownik_id)
            page_chat = ChatPage(self.pracownik_id)
            page_faktury = FakturyPage(self.pracownik_id)
            page_raporty = ReportsPage(self.pracownik_id)
            page_settings = GlobalSettingsPage(self.pracownik_id, True, self)

            self.pages.addWidget(page_monthly)
            self.pages.addWidget(page_companies)
            self.pages.addWidget(page_email)
            self.pages.addWidget(page_chat)
            self.pages.addWidget(page_faktury)
            self.pages.addWidget(page_raporty)
            self.pages.addWidget(page_settings)

            self.sidebar.setCurrentRow(0)
            self.pages.setCurrentIndex(0)
        except Exception as e:
            logger.error(f"Failed to initialize pages: {e}")
            QMessageBox.critical(self, "Błąd", f"Nie udało się załadować aplikacji: {str(e)}")

    def zmien_zakladke(self, index: int):
        """Change the current page.
        
        Args:
            index: Page index to switch to
        """
        if 0 <= index < self.pages.count():
            self.pages.setCurrentIndex(index)
        else:
            logger.warning(f"Invalid page index: {index}")

    def closeEvent(self, event):
        """Handle window close event."""
        logger.info(f"User {self.pracownik_id} closing application")
        super().closeEvent(event)
