# app/main_ui.py
import datetime
from typing import Optional

from app.utils.ui_imports import *

# --- DAO ---
from app.db.dao_employees import get_employee_info

# --- PAGES ---
from app.monthly_view.monthly_view_ui import MonthlyViewPage
from app.companies.companies_ui import CompanyDialog
from app.email.email_ui import EmailPage
from app.chat.chat_ui import ChatPage
from app.reports.reports_ui import ReportsPage
from app.admin.global_settings_ui import GlobalSettingsDialog


class OfficeManagerApp(QMainWindow):
    def __init__(self, pracownik_id: Optional[int] = None):
        super().__init__()
        self.pracownik_id = pracownik_id

        self.setWindowTitle("System Zarządzania Biurem Rachunkowego v1.1")
        self.resize(1250, 750)

        # Stoper
        self.aktywna_firma_stoper: Optional[int] = None
        self.aktywna_firma_stoper_nazwa: str = ""
        self.czas_start: Optional[datetime.datetime] = None
        self.aktualny_miesiac: str = datetime.datetime.now().strftime("%Y-%m")
        self.status_autosave_timers = {}
        self.status_widgets = {}

        # Build UI
        self._build_ui()

        # Global timer
        self.global_timer = QTimer(self)
        self.global_timer.timeout.connect(self.aktualizuj_stoper_gui)
        self.global_timer.start(1000)

    # ============================================================
    # BUILD UI
    # ============================================================
    def _build_ui(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # -------------------------
        # SIDEBAR
        # -------------------------
        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(220)
        self.sidebar.setStyleSheet("""
            QListWidget { background-color: #2c3e50; color: #ecf0f1; font-size: 14px; border: none; }
            QListWidget::item { padding: 15px 10px; border-bottom: 1px solid #34495e; }
            QListWidget::item:selected { background-color: #3498db; color: white; font-weight: bold; }
            QListWidget::item:hover { background-color: #34495e; }
        """)

        self.sidebar.addItems([
            "Widok Miesięczny",
            "Klienci",
            "Email",
            "Chat",
            "Eksport Saldeo",
            "Raporty",
            "Ustawienia"
        ])
        self.sidebar.currentRowChanged.connect(self.zmien_zakladke)
        main_layout.addWidget(self.sidebar)

        # -------------------------
        # CONTENT
        # -------------------------
        content_widget = QWidget()
        content_layout = QVBoxLayout(content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # HEADER
        global_header = QWidget()
        global_header.setStyleSheet("background-color: #f5f7fa; border-bottom: 1px solid #dfe6e9;")
        global_header_layout = QHBoxLayout(global_header)
        global_header_layout.setContentsMargins(16, 10, 16, 10)

        emp_info = get_employee_info(self.pracownik_id)
        if emp_info:
            employee_name = f"{emp_info['imie']} {emp_info['nazwisko']}"
        else:
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

        # STACK
        self.pages = QStackedWidget()
        content_layout.addWidget(self.pages)
        main_layout.addWidget(content_widget)

        # -------------------------
        # CREATE PAGES
        # -------------------------
        self.create_monthly_view_page()
        self.create_companies_page()
        self.create_email_page()
        self.create_chat_page()
        # self.create_saldeo_page()
        self.create_raporty_page()
        self.create_settings_page()

        # Default
        self.pages.setCurrentIndex(0)
        self.sidebar.setCurrentRow(0)

    # ============================================================
    # PAGE CREATORS
    # ============================================================

    def create_monthly_view_page(self):
        page = MonthlyViewPage(self.pracownik_id)
        self.pages.addWidget(page)

        # Delegacja metod do sidebar refresh
        if hasattr(page, "refresh_monthly_filters"):
            self.refresh_monthly_filters = page.refresh_monthly_filters
        if hasattr(page, "refresh_monthly_table"):
            self.refresh_monthly_table = page.refresh_monthly_table

    def create_companies_page(self):
        page = CompanyDialog(self.pracownik_id)
        self.pages.addWidget(page)
        if hasattr(page, "refresh_firms_table"):
            self.refresh_firms_table = page.refresh_firms_table

    def create_email_page(self):
        page = EmailPage(self.pracownik_id)
        self.pages.addWidget(page)
        if hasattr(page, "refresh_email_accounts"):
            self.refresh_email_accounts = page.refresh_email_accounts

    def create_chat_page(self):
        page = ChatPage(self.pracownik_id)
        self.pages.addWidget(page)

    # def create_saldeo_page(self):
    #     from app.saldeo.saldeo_ui import SaldeoPage
    #     page = SaldeoPage(self.pracownik_id)
    #     self.pages.addWidget(page)

    def create_raporty_page(self):
        page = ReportsPage(self.pracownik_id)
        self.pages.addWidget(page)

    def create_settings_page(self):
        # Panel admina jako osobny dialog otwierany z zakładki
        page = QWidget()
        layout = QVBoxLayout(page)

        btn = QPushButton("Otwórz panel administratora")
        btn.clicked.connect(self.open_admin_panel)
        layout.addWidget(btn)

        self.pages.addWidget(page)

    def open_admin_panel(self):
        dlg = GlobalSettingsDialog(self.pracownik_id, True, self)
        dlg.exec()

    # ============================================================
    # NAVIGATION
    # ============================================================
    def zmien_zakladke(self, index: int):
        if index < 0 or index >= self.pages.count():
            return

        self.pages.setCurrentIndex(index)

        try:
            if index == 0:
                self.refresh_monthly_filters()
                self.refresh_monthly_table()
            elif index == 1:
                self.refresh_firms_table()
            elif index == 2:
                self.refresh_employees_table()
            elif index == 3:
                self.refresh_email_accounts()
        except Exception:
            pass

    # ============================================================
    # STOPER
    # ============================================================
    def aktualizuj_stoper_gui(self):
        pass

    # ============================================================
    # CLOSE
    # ============================================================
    def closeEvent(self, event):
        try:
            self.global_timer.stop()
        except Exception:
            pass
        super().closeEvent(event)
