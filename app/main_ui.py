import os

from PyQt6.QtWidgets import QApplication, QCheckBox, QFileDialog, QStyledItemDelegate, QSystemTrayIcon
from PyQt6.QtCore import QSettings, QRectF
from PyQt6.QtGui import QColor, QPainter, QPen

from app.email.email_storage import get_visible_unread_counts, mail_notification_snapshot
from app.utils.ui_imports import *
from app.config import get_database

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
from app.notifications import NotificationCenter
from app.kalendarz.calendar_ui import CalendarPage
from app.dashboard.dashboard_ui import DashboardPage

from PyQt6.QtCore import QTimer, QSize
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class BadgeDelegate(QStyledItemDelegate):
    """Rysuje czerwone kółko z liczbą po prawej stronie pozycji menu."""
    BADGE_ROLE = Qt.ItemDataRole.UserRole + 1

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        count = index.data(self.BADGE_ROLE) or 0
        if not count:
            return
        text = "99+" if count > 99 else str(count)
        d = 24
        w = d if len(text) < 3 else d + 10
        rect = QRectF(option.rect.right() - w - 14,
                      option.rect.center().y() - d / 2, w, d)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#e53935"))
        painter.drawRoundedRect(rect, d / 2, d / 2)
        painter.setPen(QColor("white"))
        f = painter.font()
        f.setBold(True)
        f.setPointSize(10)
        painter.setFont(f)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


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
        self.database_path = get_database()
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
        self.database_combo.addItem(self.database_path)
        self.database_combo.setEnabled(False)
        self.database_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        database_row = QHBoxLayout()
        database_row.addWidget(self.database_combo)

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

        row.addWidget(btn_login)

        layout.addLayout(row)

        btn_reset = QPushButton("Nie pamiętam hasła")
        btn_reset.clicked.connect(self._on_reset_password)
        btn_redeem = QPushButton("Mam kod resetowania")
        btn_redeem.clicked.connect(self._on_redeem_password_reset)
        layout.addWidget(btn_reset)
        layout.addWidget(btn_redeem)

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

    def _on_login(self):
        from app.utils.crypto import login_user
        database_path = self.database_combo.currentText().strip()
        email = self.email_input.text().strip()
        password = self.password_input.text()
        if not database_path or not email or not password:
            QMessageBox.warning(self, "Błąd", "Uzupełnij bazę danych, email i hasło")
            return
        try:
            self._require_current_schema()
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
            try:
                self._store_credentials(email, password)
            except Exception:
                logger.warning("Nie udało się zapisać danych logowania", exc_info=True)
            self.pracownik_id = user_id
            self.accept()
        else:
            QMessageBox.critical(self, "Błąd", "Nieprawidłowy email lub hasło")

    def _require_current_schema(self):
        from app.db.schema import schema_is_current
        if not schema_is_current():
            raise RuntimeError(
                "Baza danych wymaga aktualizacji.\n"
                "Uruchom aktualizację bazy (skrót „Biuro - aktualizacja bazy” albo Biuro.exe --migrate; "
                "w wersji deweloperskiej: python migrate_db.py) - raz po aktualizacji programu."
            )

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
            self._require_current_schema()
            ok, message = reset_password(email)
        except Exception as e:
            QMessageBox.critical(self, "Błąd", str(e))
            return
        if ok:
            QMessageBox.information(self, "Reset hasła", message)
        else:
            QMessageBox.warning(self, "Reset hasła", message)

    def _on_redeem_password_reset(self):
        from app.utils.password_reset import redeem_password_reset
        token, ok = QInputDialog.getText(self, "Reset hasła", "Kod z wiadomości e-mail:")
        if not ok:
            return
        password, ok = QInputDialog.getText(self, "Nowe hasło", "Nowe hasło (minimum 10 znaków):", QLineEdit.EchoMode.Password)
        if not ok:
            return
        confirm, ok = QInputDialog.getText(self, "Nowe hasło", "Powtórz nowe hasło:", QLineEdit.EchoMode.Password)
        if not ok:
            return
        if password != confirm:
            QMessageBox.warning(self, "Reset hasła", "Hasła muszą być takie same.")
            return
        try:
            self._require_current_schema()
            redeem_password_reset(self.email_input.text(), token, password)
        except Exception as exc:
            QMessageBox.warning(self, "Reset hasła", str(exc))
            return
        QMessageBox.information(self, "Reset hasła", "Hasło zmienione. Możesz się zalogować.")

    def _on_register(self):
        QMessageBox.information(self, "Konta pracowników", "Nowe konta pracowników tworzy administrator w ustawieniach użytkowników.")


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
        from app.preferences.appearance import apply_appearance
        apply_appearance(self.pracownik_id)

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(240)
        self.sidebar.setObjectName("mainSidebar")

        self.sidebar.setItemDelegate(BadgeDelegate(self.sidebar))
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
            "Ustawienia",
            "Sprawdzanie"
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
        sidebar_layout.setAlignment(self.sidebar_toggle, Qt.AlignmentFlag.AlignTop)
        main_layout.addWidget(self.sidebar_panel)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)

        header = QWidget()
        header.setObjectName("appHeader")
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
        self.notification_center = NotificationCenter(self.pracownik_id, self._open_assignment_notification, header)
        header_layout.addWidget(self.notification_center)
        self.btn_general_timer = QPushButton("▶ Start stopera")
        self.btn_general_timer.setToolTip("Uruchom stoper dla zadań ogólnych (bez firmy)")
        self.btn_general_timer.clicked.connect(self._toggle_general_timer)
        header_layout.addWidget(self.btn_general_timer)

        content_layout.addWidget(header)

        self.pages = ShrinkableStack()
        content_layout.addWidget(self.pages)
        main_layout.addWidget(content)

        self.page_monthly = MonthlyViewPage(self.pracownik_id)
        self.page_dashboard = DashboardPage(self.pracownik_id)
        # These pages own background monitoring and must start before tab visits.
        self.page_email = EmailPage(self.pracownik_id)
        self.page_email.folder_visibility_changed.connect(self.refresh_email_badge_async)
        self.page_chat = ChatPage(self.pracownik_id)
        self.page_chat.unread_changed.connect(self._set_chat_badge)
        self.page_chat._refresh_unread_marks()
        self.page_calendar = CalendarPage(self.pracownik_id)
        self._loaded_pages = {}
        from app.accounting.accounting_ui import AccountingPage
        self._lazy_pages = {
            "page_accounting": (10, lambda: AccountingPage(self.pracownik_id)),
            "page_companies": (2, lambda: CompanyUI(self.pracownik_id, self)),
            "page_faktury": (5, lambda: FakturyPage(self.pracownik_id)),
            "page_raporty": (6, lambda: ReportsPage(self.pracownik_id)),
            "page_tasks": (7, lambda: TasksPage(self.pracownik_id)),
            "page_settings": (9, lambda: GlobalSettingsPage(self.pracownik_id, True, self)),
        }
        eager = {0: self.page_dashboard, 1: self.page_monthly, 3: self.page_email,
                 4: self.page_chat, 8: self.page_calendar}
        for row in range(11):
            self.pages.addWidget(eager[row] if row in eager else QWidget())

        self.sidebar.setCurrentRow(0)
        self.pages.setCurrentIndex(0)
        self.pages.currentChanged.connect(self._refresh_current_page)
        self._apply_tab_permissions()

        # Timer aktualizujący nagłówek co sekundę
        self._header_timer = QTimer(self)
        self._header_timer.setInterval(1000)
        self._header_timer.timeout.connect(self._update_header_timer)
        self._header_timer.start()

        self._email_badge_timer = QTimer(self)
        self._email_badge_timer.setInterval(10000)
        self._email_badge_timer.timeout.connect(self.refresh_email_badge_async)
        self._email_badge_timer.start()
        from app.mail_toasts import MailToaster
        self._mail_toaster = MailToaster(self, self._show_email_tab)
        from app.utils.background import TaskRunner
        self._badge_reads = TaskRunner(self)
        self._badge_busy = False
        self.refresh_email_badge_async()
        from app.chat.chat_toasts import ChatToaster
        self._chat_toaster = ChatToaster(self, self.pracownik_id, self._show_chat_tab)
        self._start_saldeo_push_watcher()
        self._start_email_reminders()

    def _ensure_page(self, name):
        if name in self._loaded_pages:
            return self._loaded_pages[name]
        row, factory = self._lazy_pages[name]
        page = factory()
        placeholder = self.pages.widget(row)
        current = self.pages.currentIndex()
        blocked = self.pages.blockSignals(True)
        self.pages.removeWidget(placeholder)
        self.pages.insertWidget(row, page)
        self.pages.setCurrentIndex(current)
        self.pages.blockSignals(blocked)
        placeholder.deleteLater()
        self._loaded_pages[name] = page
        return page

    @property
    def page_companies(self):
        return self._ensure_page("page_companies")

    @property
    def page_faktury(self):
        return self._ensure_page("page_faktury")

    @property
    def page_raporty(self):
        return self._ensure_page("page_raporty")

    @property
    def page_tasks(self):
        return self._ensure_page("page_tasks")

    @property
    def page_settings(self):
        return self._ensure_page("page_settings")

    def _start_email_reminders(self):
        from app.services.email_reminders import run_reminders
        from app.utils.background import TaskRunner
        self._reminder_runner = TaskRunner(self)
        self._reminder_busy = False
        self._reminder_timer = QTimer(self)
        self._reminder_timer.setInterval(60000)

        def finished(*_):
            self._reminder_busy = False

        def failed(exc):
            logging.getLogger(__name__).error("Automatyczne przypomnienia: %s", exc)
            finished()

        def tick():
            if not self._reminder_busy:
                self._reminder_busy = True
                self._reminder_runner.submit(run_reminders, finished, failed)

        self._reminder_timer.timeout.connect(tick)
        self._reminder_timer.start()
        QTimer.singleShot(5000, tick)

    def _start_saldeo_push_watcher(self):
        import threading
        from app.utils.saldeo_push import SaldeoPushWatcher
        self._saldeo_push = SaldeoPushWatcher()
        self._saldeo_push_busy = False
        self._saldeo_push_results = []
        self._saldeo_push_timer = QTimer(self)
        self._saldeo_push_timer.setInterval(5000)

        def tick():
            if self._saldeo_push_busy or not self._saldeo_push.available:
                if self._saldeo_push_results:
                    results, self._saldeo_push_results = self._saldeo_push_results, []
                    self._show_saldeo_push_results(results)
                return
            self._saldeo_push_busy = True

            def work():
                try:
                    self._saldeo_push_results.extend(self._saldeo_push.poll())
                finally:
                    self._saldeo_push_busy = False

            threading.Thread(target=work, daemon=True).start()
            if self._saldeo_push_results:
                results, self._saldeo_push_results = self._saldeo_push_results, []
                self._show_saldeo_push_results(results)

        self._saldeo_push_timer.timeout.connect(tick)
        self._saldeo_push_timer.start()

    def _show_saldeo_push_results(self, results):
        done = [(name, month) for name, month, outcome in results if outcome == "ok"]
        for name, month in done:
            tray = getattr(self._mail_toaster, "_tray", None)
            if tray is not None:
                self._mail_toaster.show_notice("Saldeo: komplet dokumentów", f"{name} — {month[5:]}/{month[:4]}")
        if done and self.pages.currentWidget() is self.page_monthly:
            self.page_monthly.refresh_background()

    def _show_chat_tab(self, target):
        from app.security.authorization import allowed_tabs
        if not target or "Czat" not in allowed_tabs(self.pracownik_id):
            return False
        row = next((i for i in range(self.sidebar.count())
                    if self.sidebar.item(i).data(Qt.ItemDataRole.UserRole) == "Czat"
                    and not self.sidebar.item(i).isHidden()), -1)
        if row < 0:
            return False
        self.showNormal() if self.isMinimized() else self.show()
        self.raise_()
        self.activateWindow()
        self.sidebar.setCurrentRow(row)
        self.pages.setCurrentIndex(row)
        return self.page_chat.open_message_notification(target)

    def _show_email_tab(self, target=None):
        self.showNormal() if self.isMinimized() else self.show()
        self.raise_()
        self.activateWindow()
        for row in range(self.sidebar.count()):
            if self.sidebar.item(row).data(Qt.ItemDataRole.UserRole) == "E-mail" and not self.sidebar.item(row).isHidden():
                self.sidebar.setCurrentRow(row)
                self.pages.setCurrentIndex(row)
                if target is not None:
                    self.page_email.open_message_notification(target)
                break

    def _apply_tab_permissions(self):
        from app.security.authorization import allowed_tabs

        try:
            allowed = set(allowed_tabs(self.pracownik_id))
            allowed.add("Ustawienia")
        except Exception:
            logger.warning("Nie udało się odczytać uprawnień do zakładek", exc_info=True)
            return
        first_visible = -1
        for row in range(self.sidebar.count()):
            item = self.sidebar.item(row)
            visible = item.data(Qt.ItemDataRole.UserRole) in allowed
            item.setHidden(not visible)
            if visible and first_visible < 0:
                first_visible = row
        if first_visible >= 0:
            self.sidebar.setCurrentRow(first_visible)
            self.pages.setCurrentIndex(first_visible)

    def _open_assignment_notification(self, target):
        from app.security.authorization import allowed_tabs
        kind = target.get("kind")
        tab_name = {"email": "E-mail", "task": "Zadania"}.get(kind)
        if not tab_name or tab_name not in allowed_tabs(self.pracownik_id):
            QMessageBox.warning(self, "Powiadomienie", "Nie masz dostępu do tego modułu.")
            return False
        row = next((row for row in range(self.sidebar.count())
                    if self.sidebar.item(row).data(Qt.ItemDataRole.UserRole) == tab_name), -1)
        if row < 0:
            return False
        self.sidebar.setCurrentRow(row)
        self.pages.setCurrentIndex(row)
        if kind == "email":
            return self.page_email.open_assignment_notification(target)
        return self.page_tasks.show_assignment_notification(target["task_id"])

    def _set_chat_badge(self, count: int):
        self._set_badge("Czat", count)

    def _set_badge(self, tab_name: str, count: int):
        for row in range(self.sidebar.count()):
            item = self.sidebar.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == tab_name:
                item.setData(BadgeDelegate.BADGE_ROLE, count)
                self.sidebar.viewport().update()
                break

    def _update_email_badge(self):
        try:
            total = 0

            for account in self.page_email.accounts:
                unread = get_visible_unread_counts(
                    account["id"],
                    self.page_email.hidden_folders.get(str(account["id"]), set()),
                )
                total += sum(unread.values())

            self._set_badge(
                "E-mail",
                total,
            )
            self._mail_toaster.check(self.page_email.accounts, self.page_email.hidden_folders)

        except Exception:
            logger.debug(
                "Nie udało się odczytać liczby nieprzeczytanych maili",
                exc_info=True,
            )

    def refresh_email_badge_async(self):
        if self._badge_busy:
            return
        from copy import deepcopy
        from app.mail_toasts import MailToaster
        accounts = deepcopy(self.page_email.accounts)
        hidden = deepcopy(self.page_email.hidden_folders)
        last_ids = dict(self._mail_toaster._last_ids)
        self._badge_busy = True

        def read():
            snapshot = mail_notification_snapshot(accounts, hidden, last_ids)
            return snapshot.total, (snapshot.last_ids, snapshot.messages)

        def loaded(result):
            self._badge_busy = False
            if accounts != self.page_email.accounts or hidden != self.page_email.hidden_folders:
                self.refresh_email_badge_async()
                return
            self._set_badge('E-mail', result[0])
            self._mail_toaster.apply_check(*result[1])

        def failed(exc):
            self._badge_busy = False
            logger.warning('email_badge_read failed: %s', exc)

        self._badge_reads.submit(read, loaded, failed)

    def _toggle_sidebar(self):
        self.sidebar_collapsed = not self.sidebar_collapsed
        if self.sidebar_collapsed:
            self.sidebar.setVisible(False)
            self.sidebar_panel.setFixedWidth(48)
            self.sidebar_toggle.setText("☰")
            self.sidebar_toggle.setToolTip("Rozwiń menu")
        else:
            self.sidebar.setVisible(True)
            self.sidebar_panel.setFixedWidth(240)
            self.sidebar_toggle.setText("☰  Menu")
            self.sidebar_toggle.setToolTip("Zwiń lub rozwiń menu")

    def _toggle_general_timer(self):
        logic = getattr(self.page_monthly, "logic", None)
        if not logic:
            return
        try:
            if logic.is_general_timer_running():
                logic.stop_general_timer()
            else:
                from app.monthly_view.general_timer_dialog import GeneralTimerDialog
                dialog = GeneralTimerDialog(self.pracownik_id, self)
                if dialog.exec() != QDialog.DialogCode.Accepted:
                    return
                logic.start_general_timer(dialog.activity())
                reload_rows = getattr(self.page_monthly, "_reload_rows", None)
                if callable(reload_rows):
                    reload_rows()
        except Exception as exc:
            logger.warning("Stoper ogólny: błąd", exc_info=True)
            QMessageBox.warning(self, "Stoper", f"Nie udało się zmienić stanu stopera: {exc}")
        self._update_header_timer()

    def _update_header_timer(self):
        logic = getattr(self.page_monthly, "logic", None)
        if hasattr(self, "btn_general_timer") and logic:
            self.btn_general_timer.setText(
                "■ Stop stopera" if logic.is_general_timer_running() else "▶ Start stopera")
        self._update_header_timer_labels()

    def _update_header_timer_labels(self):
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

    def _refresh_current_page(self, index: int):
        """Odświeża dane widoku po przełączeniu, żeby zmiany z innych widoków były widoczne."""
        page = self.pages.widget(index)
        for name, (row, _) in self._lazy_pages.items():
            if row == index:
                page = self._ensure_page(name)
                break
        default_department = getattr(page, 'apply_department_default', None)
        if callable(default_department):
            try:
                default_department()
            except Exception:
                logger.warning('Nie udało się ustawić domyślnego działu', exc_info=True)
        for name in ("refresh_async", "_reload_rows", "_refresh", "refresh", "_refresh_all", "refresh_all"):
            action = getattr(page, name, None)
            if callable(action):
                try:
                    action()
                except Exception:
                    logger.warning("Nie udało się odświeżyć widoku", exc_info=True)
                return

    def zmien_zakladke(self, index):
        if 0 <= index < self.pages.count():
            self.pages.setCurrentIndex(index)

    def restore_window_state(self):
        settings = QSettings("Biuro", "OfficeManager")
        geometry = settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        if settings.value("window/maximized", True, type=bool):
            self.showMaximized()
        else:
            self.show()

    def closeEvent(self, event):
        if hasattr(self, "_reminder_timer"):
            self._reminder_timer.stop()
        from app.preferences.mail_sync import MailSyncSettings
        for sync_page in self.findChildren(MailSyncSettings):
            sync_page.stop()
        if hasattr(self, "page_email"):
            self.page_email.stop_background_sync()
        if hasattr(self, "notification_center"):
            self.notification_center.timer.stop()
        if hasattr(self, "_mail_toaster"):
            self._mail_toaster.hide()
        if hasattr(self, "_chat_toaster"):
            self._chat_toaster.hide()
        if hasattr(self, "page_chat"):
            self.page_chat._poll_timer.stop()
        settings = QSettings("Biuro", "OfficeManager")
        settings.setValue("window/maximized", self.isMaximized())
        if not self.isMaximized():
            settings.setValue("window/geometry", self.saveGeometry())
        # zatrzymaj timer przy zamykaniu
        try:
            if self._header_timer and self._header_timer.isActive():
                self._header_timer.stop()
        except Exception:
            logger.warning("Zignorowany błąd", exc_info=True)
        try:
            logic = getattr(self.page_monthly, "logic", None)
            if logic is not None:
                logic.stop_all_timers()
        except Exception:
            logger.warning("Nie zapisano stoperów", exc_info=True)
        super().closeEvent(event)
