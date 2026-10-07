import os

from PyQt6.QtWidgets import QApplication, QFileDialog
from PyQt6.QtCore import QSettings
from app.utils.ui_imports import *
from app.config import DEFAULT_DB_NAME, get_database, set_database

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
from app.dashboard.dashboard_ui import DashboardPage

from PyQt6.QtCore import QTimer
from typing import Optional


class LoginWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Logowanie")
        self.setMinimumSize(520, 380)
        self.pracownik_id: Optional[int] = None
        self.database_path = self._last_database() or get_database()
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
        self.database_combo = QComboBox()
        self.database_combo.setEditable(True)
        self.database_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.database_combo.addItems(self._database_history())
        self.database_combo.setCurrentText(self.database_path)
        self.database_combo.lineEdit().setPlaceholderText("Wybierz plik SQLite...")
        btn_browse = QPushButton("...")
        btn_browse.setFixedWidth(42)
        btn_browse.clicked.connect(self._choose_database)
        database_row = QHBoxLayout()
        database_row.addWidget(self.database_combo)
        database_row.addWidget(btn_browse)
        form.addRow("Baza danych:", database_row)
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

    @staticmethod
    def _database_history() -> list[str]:
        settings = QSettings("Biuro", "OfficeManager")
        values = settings.value("databases", [], type=list)
        if isinstance(values, str):
            values = [values]
        paths = [str(value).strip() for value in values if str(value).strip()]
        last_database = str(settings.value("last_database", "") or "").strip()
        if last_database:
            paths = [last_database] + [path for path in paths if path != last_database]
        if DEFAULT_DB_NAME not in paths:
            paths.insert(0, DEFAULT_DB_NAME)
        return paths

    @staticmethod
    def _last_database() -> str:
        settings = QSettings("Biuro", "OfficeManager")
        return str(settings.value("last_database", "") or "").strip()

    def _choose_database(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Wybierz bazę SQLite",
            self.database_combo.currentText() or DEFAULT_DB_NAME,
            "Baza SQLite (*.db *.sqlite *.sqlite3);;Wszystkie pliki (*)",
        )
        if path:
            self.database_combo.setCurrentText(path)

    def _remember_database(self, path: str):
        settings = QSettings("Biuro", "OfficeManager")
        selected = os.path.abspath(os.path.expanduser(path))
        paths = [selected]
        for existing in self._database_history():
            if existing != selected:
                paths.append(existing)
        settings.setValue("databases", paths[:10])
        settings.setValue("last_database", selected)
        settings.sync()

    def _on_login(self):
        from app.utils.crypto import login_user
        database_path = self.database_combo.currentText().strip()
        email = self.email_input.text().strip()
        password = self.password_input.text()
        if not database_path or not email or not password:
            QMessageBox.warning(self, "Błąd", "Uzupełnij bazę danych, email i hasło")
            return
        try:
            set_database(database_path)
            from app.db.schema import init_db
            init_db()
            result = login_user(email, password)
        except Exception as e:
            QMessageBox.critical(self, "Błąd", str(e))
            return
        if isinstance(result, tuple):
            ok, user_id = result[0], result[1]
        else:
            ok, user_id = bool(result), result
        if ok:
            self.database_path = database_path
            self._remember_database(database_path)
            self.pracownik_id = user_id
            self.accept()
        else:
            QMessageBox.critical(self, "Błąd", "Nieprawidłowy email lub hasło")

    def _on_register(self):
        from app.utils.crypto import register_user
        database_path = self.database_combo.currentText().strip()
        if not database_path:
            QMessageBox.warning(self, "Błąd", "Wybierz bazę danych przed rejestracją")
            return
        try:
            set_database(database_path)
            from app.db.schema import init_db
            init_db()
        except Exception as e:
            QMessageBox.critical(self, "Błąd", str(e))
            return
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
                self.database_path = database_path
                self._remember_database(database_path)
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
            "Dashboard",
            "Widok miesięczny",
            "Klienci",
            "Email",
            "Chat",
            "Faktury",
            "Raporty",
            "Zadania",
            "Ustawienia"
        ])
        for row in range(self.sidebar.count()):
            item = self.sidebar.item(row)
            item.setData(Qt.ItemDataRole.UserRole, item.text())
        self.sidebar.currentRowChanged.connect(self.zmien_zakladke)
        self.sidebar_collapsed = False
        self.sidebar_panel = QWidget()
        self.sidebar_panel.setFixedWidth(240)
        sidebar_layout = QVBoxLayout(self.sidebar_panel)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        self.sidebar_toggle = QPushButton("☰  Menu")
        self.sidebar_toggle.setToolTip("Zwiń lub rozwiń menu")
        self.sidebar_toggle.clicked.connect(self._toggle_sidebar)
        sidebar_layout.addWidget(self.sidebar_toggle)
        sidebar_layout.addWidget(self.sidebar)
        main_layout.addWidget(self.sidebar_panel)

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
        lbl_live_title = QLabel(name)
        lbl_live_title.setStyleSheet("font-weight: bold;")
        live_layout.addWidget(lbl_live_title)

        self.lbl_live_task = QLabel("Zadanie")
        self.lbl_live_context = QLabel("Firma")
        self.lbl_live_timer = QLabel("00:00:00")
        for label in (self.lbl_live_task, self.lbl_live_context, self.lbl_live_timer):
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            label.setStyleSheet("font-size: 12px;")
            live_layout.addWidget(label)

        header_layout.addStretch()
        header_layout.addWidget(live_container)
        minimize_button = QPushButton("—")
        minimize_button.setFixedSize(34, 28)
        minimize_button.setToolTip("Minimalizuj okno")
        minimize_button.clicked.connect(self.showMinimized)
        header_layout.addWidget(minimize_button)

        content_layout.addWidget(header)

        self.pages = QStackedWidget()
        content_layout.addWidget(self.pages)
        main_layout.addWidget(content)

        self.page_monthly = MonthlyViewPage(self.pracownik_id)
        self.page_dashboard = DashboardPage(self.pracownik_id)
        self.page_companies = CompanyUI(self.pracownik_id, self)
        self.page_email = EmailPage(self.pracownik_id)
        self.page_chat = ChatPage(self.pracownik_id)
        self.page_faktury = FakturyPage(self.pracownik_id)
        self.page_raporty = ReportsPage(self.pracownik_id)
        self.page_tasks = TasksPage(self.pracownik_id)
        self.page_settings = GlobalSettingsPage(self.pracownik_id, True, self)

        self.pages.addWidget(self.page_dashboard)
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

    def _toggle_sidebar(self):
        self.sidebar_collapsed = not self.sidebar_collapsed
        if self.sidebar_collapsed:
            self.sidebar.setFixedWidth(58)
            self.sidebar_panel.setFixedWidth(58)
            self.sidebar_toggle.setText("☰")
            self.sidebar.setToolTip("Rozwiń menu")
            for row in range(self.sidebar.count()):
                item = self.sidebar.item(row)
                item.setToolTip(item.data(Qt.ItemDataRole.UserRole))
                item.setText("")
        else:
            self.sidebar.setFixedWidth(240)
            self.sidebar_panel.setFixedWidth(240)
            self.sidebar_toggle.setText("☰  Menu")
            self.sidebar.setToolTip("")
            for row in range(self.sidebar.count()):
                item = self.sidebar.item(row)
                item.setText(item.data(Qt.ItemDataRole.UserRole))
                item.setToolTip("")

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
            details = logic.get_active_timer_details()
            if details:
                first = details[0]
                self.lbl_live_task.setText(first["task"])
                self.lbl_live_context.setText(
                    f"{first['company']} - {first['department']}"
                )
                self.lbl_live_timer.setText(first["elapsed"])
            else:
                self.lbl_live_task.setText("Brak aktywnego zadania")
                self.lbl_live_context.setText("")
                self.lbl_live_timer.setText("00:00:00")
        except Exception:
            # nie przerywamy UI; ewentualnie loguj
            if self.lbl_live_timer:
                self.lbl_live_task.setText("Brak aktywnego zadania")
                self.lbl_live_context.setText("")
                self.lbl_live_timer.setText("00:00:00")

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
