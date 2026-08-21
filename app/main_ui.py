from app.utils.ui_imports import *

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QStackedWidget,
    QTableWidget, QTableWidgetItem, QPushButton, QHeaderView, QAbstractItemView,
    QDialog, QMessageBox
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from app.db.dao_companies import get_conn
from app.db.schema import init_db

from app.global.settings import is_admin

from app.monthly_view.monthly_view_ui import MonthlyViewPage
from app.companies.companies_ui import CompaniesPage
from app.email.email_ui import EmailPage
from app.chat.chat_ui import ChatPage
from app.reports.reports_ui import ReportsPage   # jeśli masz
from app.ui.settings_ui import SettingsPage      # jeśli masz
from app.ui.saldeo_ui import SaldeoPage          # jeśli masz

# --- UTILS ---
from app.utils.saldeo_exporter import export_saldeo_csv
from app.utils.importers import import_cennik_from_excel


class OfficeManagerApp(QMainWindow):
    def __init__(self, pracownik_id: Optional[int] = None):
        super().__init__()
        self.employees_table = None
        self.pracownik_id = pracownik_id
        self.setWindowTitle("System Zarządzania Biurem Rachunkowego v1.1")
        self.resize(1250, 750)

        # Thread pool for background tasks
        from PyQt6.QtCore import QThreadPool
        self.thread_pool = QThreadPool.globalInstance()

        # Stoper
        self.aktywna_firma_stoper: Optional[int] = None
        self.aktywna_firma_stoper_nazwa: str = ""
        self.czas_start: Optional[datetime.datetime] = None
        self.aktualny_miesiac: str = datetime.datetime.now().strftime("%Y-%m")
        self.status_autosave_timers = {}
        self.status_widgets = {}

        # UI build
        self._build_ui()

        # Global timer for updating stoper GUI
        self.global_timer = QTimer(self)
        self.global_timer.timeout.connect(self.aktualizuj_stoper_gui)
        self.global_timer.start(1000)

    # -------------------------
    # UI construction
    # -------------------------
    def _build_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Sidebar
        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(220)
        self.sidebar.setStyleSheet("""
            QListWidget { background-color: #2c3e50; color: #ecf0f1; font-size: 14px; border: none; }
            QListWidget::item { padding: 15px 10px; border-bottom: 1px solid #34495e; }
            QListWidget::item:selected { background-color: #3498db; color: white; font-weight: bold; }
            QListWidget::item:hover { background-color: #34495e; }
        """)
        # order: Monthly view (with tabs Księgowość/Kadry), Companies, Employees, Email, Chat, Export
        self.sidebar.addItems([
            "Widok Miesięczny",
            "Klienci",
            "Pracownicy",
            "Email",
            "Chat",
            "Eksport Saldeo",
            "Raporty",
            "Ustawienia"
        ])
        self.sidebar.currentRowChanged.connect(self.zmien_zakladke)
        main_layout.addWidget(self.sidebar)

        # Content
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # Header
        global_header = QWidget()
        global_header.setStyleSheet("background-color: #f5f7fa; border-bottom: 1px solid #dfe6e9;")
        global_header_layout = QHBoxLayout(global_header)
        global_header_layout.setContentsMargins(16, 10, 16, 10)

        emp_info = get_employee_info(self.pracownik_id) if self.pracownik_id else None
        if isinstance(emp_info, dict):
            employee_name = f"{emp_info.get('imie','')} {emp_info.get('nazwisko','')}".strip() or "Nieznany"
        else:
            try:
                employee_name = f"{emp_info[2]} {emp_info[3]}" if emp_info else "Nieznany"
            except Exception:
                employee_name = "Nieznany"

        self.lbl_global_user = QLabel(f"<b>Zalogowany:</b> {employee_name}")
        self.lbl_global_user.setStyleSheet("color: #2c3e50;")
        global_header_layout.addWidget(self.lbl_global_user)

        self.lbl_global_timer_status = QLabel("Timer: brak aktywnej firmy")
        self.lbl_global_timer_status.setStyleSheet(
            "color: #2c3e50; font-weight: bold; background-color: #ecf0f1; padding: 6px 10px; border-radius: 4px;"
        )
        global_header_layout.addWidget(self.lbl_global_timer_status, alignment=Qt.AlignmentFlag.AlignRight)
        content_layout.addWidget(global_header)

        # Pages stack
        self.pages = QStackedWidget()
        content_layout.addWidget(self.pages)
        main_layout.addWidget(content_widget)

        # Create pages
        self.create_monthly_view_page()
        self.create_companies_page()
        self.create_employees_page()
        self.create_email_page()
        self.create_chat_page()
        self.create_saldeo_page()
        self.create_raporty_page()
        self.create_settings_page()

        # default to first page
        self.pages.setCurrentIndex(0)
        self.sidebar.setCurrentRow(0)
        print("=== STACK DEBUG ===")
        print("Sidebar count:", self.sidebar.count())
        print("Pages count:", self.pages.count())

        for i in range(self.pages.count()):
            w = self.pages.widget(i)
            print(i, type(w).__name__)


    # -------------------------
    # Navigation handler (required by sidebar)
    # -------------------------
    def zmien_zakladke(self, index: int):
        if index < 0 or index >= self.pages.count():
            return
        self.pages.setCurrentIndex(index)
        try:
            if index == 0:
                try:
                    self.refresh_monthly_filters()
                except Exception:
                    pass
                try:
                    self.refresh_monthly_table()
                except Exception:
                    pass
            elif index == 1:
                try:
                    self.refresh_firms_table()
                except Exception:
                    pass
            elif index == 2:
                try:
                    self.refresh_employees_table()
                except Exception:
                    pass
            elif index == 3:
                if EmailPage and hasattr(self, "refresh_email_accounts"):
                    try:
                        self.refresh_email_accounts()
                    except Exception:
                        pass
            elif index == 4:
                try:
                    page = self.pages.widget(index)
                    if hasattr(page, "refresh_chat_conversations"):
                        page.refresh_chat_conversations()
                except Exception:
                    pass
            elif index == 5:
                try:
                    if SaldeoService and hasattr(self, "refresh_saldeo_page"):
                        self.refresh_saldeo_page()
                except Exception:
                    pass
        except Exception:
            pass


    # -------------------------
    # Cleanup / close
    # -------------------------
    def closeEvent(self, event):
        try:
            self.global_timer.stop()
        except Exception:
            pass
        super().closeEvent(event)