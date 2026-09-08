import datetime
from typing import Optional
import logging
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtGui import QFont
from app.utils.ui_imports import *

from app.db.dao_employees import get_employee_info

from app.monthly_view.monthly_view_ui import MonthlyViewPage
from app.companies.company_ui import CompaniesPage
from app.email.email_ui import EmailPage
from app.chat.chat_ui import ChatPage
from app.reports.reports_ui import ReportsPage
from app.admin.global_settings_ui import GlobalSettingsPage
from app.invoices.faktury_ui import FakturyPage

logger = logging.getLogger(__name__)

# --- Improved Constants ---
APP_TITLE = "System Zarządzania Biurem Rachunkowego"
DEFAULT_WINDOW_WIDTH = 1400
DEFAULT_WINDOW_HEIGHT = 850
SIDEBAR_WIDTH = 240

COLOR_PRIMARY = "#1c70b7"
COLOR_PRIMARY_DARK = "#155a8a"
COLOR_PRIMARY_HOVER = "#1668a3"
COLOR_BACKGROUND = "#eae0d5"
COLOR_TEXT = "#2c3e50"
COLOR_BORDER = "#d0c7bd"
COLOR_WHITE = "white"


class LoginWindow(QDialog):
    """User login dialog window."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Logowanie")
        self.setFixedSize(420, 320)
        self.pracownik_id: Optional[int] = None
        self.email_input = None
        self.password_input = None
        self._build_ui()

    def _build_ui(self):
        """Build login form UI."""
        self.setStyleSheet(f"""
            QDialog {{ background-color: {COLOR_BACKGROUND}; }}
            QLabel {{ color: {COLOR_PRIMARY}; font-weight: bold; }}
            QLineEdit {{
                padding: 8px;
                border-radius: 6px;
                border: 1px solid #b8b1a8;
                background-color: {COLOR_WHITE};
            }}
            QPushButton {{
                background-color: {COLOR_PRIMARY};
                color: {COLOR_WHITE};
                padding: 8px;
                border-radius: 6px;
                font-weight: bold;
                border: none;
            }}
            QPushButton:hover {{ background-color: {COLOR_PRIMARY_DARK}; }}
            QPushButton:pressed {{ background-color: {COLOR_PRIMARY_DARK}; }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        lbl_title = QLabel("Zaloguj się")
        lbl_title.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        layout.addWidget(lbl_title)

        form = QFormLayout()
        form.setSpacing(10)
        
        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("example@example.com")
        
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Wpisz hasło")
        self.password_input.setEchoMode(QLineEdit.EchoMode.Password)
        
        form.addRow("Email:", self.email_input)
        form.addRow("Hasło:", self.password_input)
        layout.addLayout(form)

        layout.addStretch()

        row = QHBoxLayout()
        btn_login = QPushButton("Zaloguj")
        btn_login.clicked.connect(self._on_login)
        btn_register = QPushButton("Zarejestruj")
        btn_register.clicked.connect(self._on_register)
        row.addWidget(btn_login)
        row.addWidget(btn_register)
        layout.addLayout(row)

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
                self.accept()
            else:
                QMessageBox.critical(self, "Błąd", "Nieprawidłowy email lub hasło")
        except Exception as e:
            logger.error(f"Login error: {e}")
            QMessageBox.critical(self, "Błąd", f"Błąd logowania: {str(e)}")

    def _on_register(self):
        """Handle register button click."""
        from app.utils.crypto import register_user
        
        dlg = QDialog(self)
        dlg.setWindowTitle("Rejestracja")
        dlg.setFixedSize(420, 380)
        dlg.setStyleSheet(self.styleSheet())

        form = QFormLayout()
        form.setSpacing(10)
        
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
            # Validate inputs
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
        self.page_monthly = None
        self.page_companies = None
        self.page_email = None
        self.page_chat = None
        self.page_faktury = None
        self.page_raporty = None
        self.page_settings = None
        
        if pracownik_id is None:
            self.pracownik_id = self._show_login_dialog()
            if not self.pracownik_id:
                logger.warning("User cancelled login dialog")
                QApplication.instance().quit()
                return

        self.setWindowTitle(APP_TITLE)
        self.resize(DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT)
        self._build_ui()

    def _show_login_dialog(self) -> Optional[int]:
        """Show and handle login dialog."""
        dlg = LoginWindow(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            return dlg.pracownik_id
        return None

    def _build_ui(self):
        """Build main application UI."""
        self.setStyleSheet(f"""
            QWidget {{ background-color: {COLOR_BACKGROUND}; font-family: Arial; }}
            QLabel {{ color: {COLOR_TEXT}; }}
            QPushButton {{
                background-color: {COLOR_PRIMARY};
                color: {COLOR_WHITE};
                padding: 8px 14px;
                border-radius: 6px;
                font-weight: bold;
                border: none;
            }}
            QPushButton:hover {{ background-color: {COLOR_PRIMARY_DARK}; }}
            QPushButton:pressed {{ background-color: {COLOR_PRIMARY_DARK}; }}
        """)

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # --- Sidebar ---
        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(SIDEBAR_WIDTH)
        self.sidebar.setStyleSheet(f"""
            QListWidget {{
                background-color: {COLOR_PRIMARY};
                color: {COLOR_WHITE};
                border: none;
                font-size: 14px;
                outline: none;
            }}
            QListWidget::item {{ 
                padding: 15px 12px;
                margin: 0px;
            }}
            QListWidget::item:selected {{ 
                background-color: {COLOR_PRIMARY_DARK}; 
                font-weight: bold;
                border-left: 3px solid {COLOR_WHITE};
                margin: 0px;
            }}
            QListWidget::item:hover {{ 
                background-color: {COLOR_PRIMARY_HOVER}; 
            }}
        """)

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
        main_layout.addWidget(self.sidebar)

        # --- Content Area ---
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # --- Header ---
        header = QWidget()
        header.setStyleSheet(f"background-color: {COLOR_WHITE}; border-bottom: 2px solid {COLOR_BORDER};")
        header.setFixedHeight(60)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 12, 20, 12)
        header_layout.setSpacing(0)

        try:
            emp = get_employee_info(self.pracownik_id)
            if emp:
                name = f"{emp.get('imie', '')} {emp.get('nazwisko', '')}".strip()
            else:
                name = "Nieznany"
        except Exception as e:
            logger.error(f"Failed to get employee info: {e}")
            name = "Błąd"
        
        lbl_user = QLabel(f"<b>Zalogowany:</b> {name}")
        lbl_user.setStyleSheet(f"font-size: 14px; color: {COLOR_TEXT};")
        header_layout.addWidget(lbl_user)
        header_layout.addStretch()

        content_layout.addWidget(header)

        # --- Pages ---
        self.pages = QStackedWidget()
        content_layout.addWidget(self.pages)
        main_layout.addWidget(content)

        # Initialize all pages
        try:
            self.page_monthly = MonthlyViewPage(self.pracownik_id)
            self.page_companies = CompaniesPage(self.pracownik_id)
            self.page_email = EmailPage(self.pracownik_id)
            self.page_chat = ChatPage(self.pracownik_id)
            self.page_faktury = FakturyPage(self.pracownik_id)
            self.page_raporty = ReportsPage(self.pracownik_id)
            self.page_settings = GlobalSettingsPage(self.pracownik_id, True, self)

            self.pages.addWidget(self.page_monthly)
            self.pages.addWidget(self.page_companies)
            self.pages.addWidget(self.page_email)
            self.pages.addWidget(self.page_chat)
            self.pages.addWidget(self.page_faktury)
            self.pages.addWidget(self.page_raporty)
            self.pages.addWidget(self.page_settings)

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
