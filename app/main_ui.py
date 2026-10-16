import os

from PyQt6.QtWidgets import QApplication, QCheckBox, QFileDialog
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
from app.kalendarz.calendar_ui import CalendarPage
from app.dashboard.dashboard_ui import DashboardPage

from PyQt6.QtCore import QTimer, QSize
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class ShrinkableStack(QStackedWidget):
    """QStackedWidget bierze minimum z najszerszej strony; tu pozwalamy zwężać okno."""

    def minimumSizeHint(self):
        return QSize(0, 0)


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

            QLabel {
                color: #1c70b7;
                font-weight: bold;
            }

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

            QPushButton:hover {
                background-color: #155a8a;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)

        lbl_title = QLabel("Zaloguj się")
        lbl_title.setFont(
            QFont(
                "Arial",
                16,
                QFont.Weight.Bold
            )
        )

        layout.addWidget(lbl_title)

        form = QFormLayout()

        self.database_combo = QComboBox()
        self.database_combo.setEditable(True)
        self.database_combo.setInsertPolicy(
            QComboBox.InsertPolicy.NoInsert
        )

        self.database_combo.addItems(
            self._database_history()
        )

        self.database_combo.setCurrentText(
            self.database_path
        )

        self.database_combo.lineEdit().setPlaceholderText(
            "Wybierz plik SQLite..."
        )

        # TAB nie zatrzymuje się na polu bazy
        self.database_combo.setFocusPolicy(
            Qt.FocusPolicy.ClickFocus
        )

        btn_browse = QPushButton("...")
        btn_browse.setFixedWidth(42)
        btn_browse.clicked.connect(
            self._choose_database
        )

        database_row = QHBoxLayout()
        database_row.addWidget(
            self.database_combo
        )
        database_row.addWidget(
            btn_browse
        )

        form.addRow(
            "Baza danych:",
            database_row
        )

        self.email_input = QLineEdit()

        self.password_input = QLineEdit()
        self.password_input.setEchoMode(
            QLineEdit.EchoMode.Password
        )

        form.addRow(
            "E-mail:",
            self.email_input
        )

        form.addRow(
            "Hasło:",
            self.password_input
        )

        self.remember_check = QCheckBox("Zapamiętaj mnie na tym komputerze")
        form.addRow("", self.remember_check)

        layout.addLayout(form)

        row = QHBoxLayout()

        btn_login = QPushButton(
            "Zaloguj"
        )
        btn_login.clicked.connect(
            self._on_login
        )

        btn_register = QPushButton(
            "Zarejestruj"
        )
        btn_register.clicked.connect(
            self._on_register
        )

        row.addWidget(btn_login)
        row.addWidget(btn_register)

        layout.addLayout(row)

        btn_reset = QPushButton("Nie pamiętam hasła")
        btn_reset.clicked.connect(self._on_reset_password)
        layout.addWidget(btn_reset)

        # ====================================================
        # OBSŁUGA ENTER / TAB
        # ====================================================

        self.setTabOrder(
            self.email_input,
            self.password_input
        )

        self.setTabOrder(
            self.password_input,
            btn_login
        )

        self.email_input.returnPressed.connect(
            self.password_input.setFocus
        )

        self.password_input.returnPressed.connect(
            self._on_login
        )

        self._load_saved_credentials()
        if self.email_input.text() and self.password_input.text():
            self.password_input.setFocus()
        else:
            self.email_input.setFocus()

    def _load_saved_credentials(self):
        settings = QSettings("Biuro", "OfficeManager")
        if not settings.value("remember_me", False, type=bool):
            return
        self.remember_check.setChecked(True)
        self.email_input.setText(str(settings.value("saved_email", "") or ""))
        encrypted = str(settings.value("saved_password", "") or "")
        if encrypted:
            try:
                from app.utils.crypto import decrypt_password
                self.password_input.setText(decrypt_password(encrypted))
            except Exception:
                logger.warning("Nie udało się odczytać zapisanego hasła", exc_info=True)

    def _store_credentials(self, email: str, password: str):
        """Hasło trafia do QSettings wyłącznie w postaci zaszyfrowanej."""
        settings = QSettings("Biuro", "OfficeManager")
        if self.remember_check.isChecked():
            from app.utils.crypto import encrypt_password
            settings.setValue("remember_me", True)
            settings.setValue("saved_email", email)
            settings.setValue("saved_password", encrypt_password(password))
        else:
            for key in ("remember_me", "saved_email", "saved_password"):
                settings.remove(key)
        settings.sync()

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
            if not self._ensure_initial_admin():
                return
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
            try:
                self._store_credentials(email, password)
            except Exception:
                logger.warning("Nie udało się zapisać danych logowania", exc_info=True)
            self.pracownik_id = user_id
            self.accept()
        else:
            QMessageBox.critical(self, "Błąd", "Nieprawidłowy email lub hasło")

    def _ensure_initial_admin(self) -> bool:
        """Przy pustej bazie prosi o utworzenie konta administratora."""
        from app.security.bootstrap import needs_initial_admin, create_initial_admin, MIN_PASSWORD_LENGTH
        if not needs_initial_admin():
            return True
        dlg = QDialog(self)
        dlg.setWindowTitle("Pierwsze uruchomienie - konto administratora")
        dlg.setMinimumWidth(420)
        form = QFormLayout()
        email = QLineEdit()
        imie = QLineEdit()
        nazwisko = QLineEdit()
        haslo = QLineEdit()
        haslo.setEchoMode(QLineEdit.EchoMode.Password)
        powtorz = QLineEdit()
        powtorz.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("E-mail:", email)
        form.addRow("Imię:", imie)
        form.addRow("Nazwisko:", nazwisko)
        form.addRow(f"Hasło (min. {MIN_PASSWORD_LENGTH} znaków):", haslo)
        form.addRow("Powtórz hasło:", powtorz)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("Baza jest pusta. Utwórz konto administratora."))
        layout.addLayout(form)
        layout.addWidget(btns)
        while dlg.exec() == QDialog.DialogCode.Accepted:
            if haslo.text() != powtorz.text():
                QMessageBox.warning(self, "Błąd", "Hasła nie są takie same.")
                continue
            try:
                create_initial_admin(email.text(), haslo.text(),
                                     imie.text().strip() or "Główny",
                                     nazwisko.text().strip() or "Administrator")
            except (ValueError, PermissionError) as e:
                QMessageBox.warning(self, "Błąd", str(e))
                continue
            self.email_input.setText(email.text().strip())
            self.password_input.setText(haslo.text())
            return True
        return False

    def _on_reset_password(self):
        from app.utils.password_reset import reset_password
        database_path = self.database_combo.currentText().strip()
        email = self.email_input.text().strip()
        if not database_path or not email:
            QMessageBox.warning(self, "Błąd", "Uzupełnij bazę danych i email")
            return
        try:
            set_database(database_path)
            from app.db.schema import init_db
            init_db()
            ok, message = reset_password(email)
        except Exception as e:
            QMessageBox.critical(self, "Błąd", str(e))
            return
        if ok:
            QMessageBox.information(self, "Reset hasła", message)
        else:
            QMessageBox.warning(self, "Reset hasła", message)

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
        form.addRow("E-mail:", email)
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
        self.setMinimumSize(900, 600)

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
            QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QDateEdit {
                background-color: white;
                border: 1px solid #b8b1a8;
                border-radius: 4px;
                padding: 4px 6px;
            }
            QComboBox { padding: 4px 6px; min-height: 22px; }
            QTableView, QTreeView, QListView {
                background-color: white;
                alternate-background-color: #f6f2ed;
                border: 1px solid #d0c7bd;
                selection-background-color: #cfe3f5;
                selection-color: #1c1c1c;
            }
            QHeaderView::section {
                background-color: #f1e9e0;
                color: #2c3e50;
                font-weight: bold;
                padding: 6px 8px;
                border: none;
                border-bottom: 2px solid #d0c7bd;
            }
            QTabBar::tab {
                background: #f1e9e0;
                padding: 8px 18px;
                border: 1px solid #d0c7bd;
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
            }
            QTabBar::tab:selected { background: white; font-weight: bold; }
            QGroupBox { border: 1px solid #d0c7bd; border-radius: 6px; margin-top: 10px; padding-top: 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
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
            "Pulpit",
            "Widok miesięczny",
            "Klienci",
            "E-mail",
            "Czat",
            "Faktury",
            "Raporty",
            "Zadania",
            "Kalendarz",
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

        self.pages = ShrinkableStack()
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
        self.page_calendar = CalendarPage(self.pracownik_id)
        self.page_settings = GlobalSettingsPage(self.pracownik_id, True, self)

        self.pages.addWidget(self.page_dashboard)
        self.pages.addWidget(self.page_monthly)
        self.pages.addWidget(self.page_companies)
        self.pages.addWidget(self.page_email)
        self.pages.addWidget(self.page_chat)
        self.pages.addWidget(self.page_faktury)
        self.pages.addWidget(self.page_raporty)
        self.pages.addWidget(self.page_tasks)
        self.pages.addWidget(self.page_calendar)
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
            logger.warning("Zignorowany błąd", exc_info=True)
        super().closeEvent(event)
