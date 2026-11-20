"""Strona poczty: stała powłoka plus odświeżana lista i podgląd.

Podział na moduły (kolejność ma znaczenie tylko dla dziedziczenia):

* `mail_shell` — pasek narzędzi, drzewo folderów, etykiety pracowników: budowane raz,
  odświeżanie podmienia tylko ich treść;
* `mail_area` — lista wiadomości i podgląd, jedyne panele zmieniane przy każdej
  synchronizacji i kliknięciu;
* `mail_sync_controller` — synchronizacja w tle;
* `mail_actions` — czynności na wiadomościach (nowa, odpowiedź, przekaż, usuwanie…).

Ten plik zostaje punktem wejścia dla `app.main_ui` i testów (`email_ui.EmailPage`),
więc publiczne API strony się nie zmienia.
"""
from __future__ import annotations

import email
import threading
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QWidget

from app.db.dao_app_settings import get_setting
from app.email.email_labels import resolve_label_numbers
from app.email.email_storage import get_email_accounts_for_employee
from app.email.mail_actions import MailActionsMixin
from app.email.mail_area import MailArea
from app.email.mail_constants import (  # noqa: F401 — re-eksport dla testów i starych importów
    AUTO_SYNC_INTERVAL_MS,
    DEFAULT_COLUMN_WIDTHS,
    MAX_NUMBER_KEYS,
    ROLE_ACCOUNT,
    ROLE_DATA,
    ROLE_FOLDER,
    ROLE_FOLDER_IDS,
    ROLE_FOLDER_LABEL,
    ROLE_MSGID,
    ROLE_REFS,
    ROLE_ROW_ID,
)
from app.email.mail_folder_tree import FolderTree  # noqa: F401 — re-eksport
from app.email.mail_shell import MailShellMixin
from app.email.mail_sync_controller import MailSyncMixin
from app.email.mail_view import MailBrowser, _MailWebPage  # noqa: F401 — re-eksport


class EmailPage(MailShellMixin, MailArea, MailSyncMixin, MailActionsMixin, QWidget):
    """Strona E-mail. Powłoka stała, odświeża się lista wiadomości i podgląd."""

    folder_visibility_changed = pyqtSignal()
    sync_finished = pyqtSignal(bool)
    sync_failed = pyqtSignal(str)
    headers_ready = pyqtSignal()
    sync_progress = pyqtSignal(object)
    async_result = pyqtSignal(object)


    def __init__(self, pracownik_id: int):
        super().__init__()
        self.pracownik_id = pracownik_id
        from app.remote.state import client
        remote_mode = client() is not None
        self._office_folder_prefix = '' if remote_mode else str(get_setting('email_folder_prefix') or '')
        self._sidebar_reads = self._make_runner('TaskRunner')
        self._list_jobs = self._make_runner('TaskRunner')
        self._message_jobs = self._make_runner('TaskRunner')
        self._command_jobs = self._make_runner('CommandRunner')
        self._contact_jobs = self._make_runner('TaskRunner')
        self._contact_editor_pending = False
        self._sync_jobs = self._make_runner('CommandRunner')
        self.accounts = self._email_accounts_for_employee()
        self.current_account: Optional[Dict[str, Any]] = None
        self.current_folder_local: Optional[str] = None
        self.current_uid: Optional[str] = None
        self.cached_message: Optional[email.message.Message] = None
        self.current_message_labels: List[Tuple[str, str]] = []
        self.unread_map: Dict[int, bool] = {}
        self.employee_filter: Optional[int] = None
        self.completed_filter = False
        self.employees: List[Dict[str, Any]] = []
        self.assign_map: Dict[int, List[int]] = {}
        self.completed_map: set[int] = set()
        self.label_numbers: Dict[int, int] = {}
        self.hidden_folders: Dict[str, set[str]] = self._load_hidden_folders()
        self.show_hidden_folders = False
        self._tree_signature = None
        self._chips_signature = None
        self._mail_rows = []
        self._mail_cursor = None
        self._mail_context = None
        self._pending_mail_load = None
        self._loading_message_key = None
        self._last_account_check = 0.0
        self._last_sync_error = ""
        self._resync_pending = False
        self._sync_running = False
        self._sync_cancel = threading.Event()
        self._load_token = 0
        if not remote_mode:
            self._reload_employees()
        # Wybór wiadomości w liście jest opóźniony: jedno kliknięcie, jedno otwarcie.
        self._select_timer = self._make_timer(120, self._open_selected_now, start=False)
        self._build_ui()
        self._refresh_chips()
        self.async_result.connect(self._on_async_result)
        self.sync_finished.connect(self._on_sync_finished)
        self.sync_failed.connect(self._on_sync_failed)
        self._headers_refresh_timer = self._make_timer(500, self._on_headers_ready)
        self.headers_ready.connect(self._schedule_headers_refresh)
        self.sync_progress.connect(self._on_sync_progress)

        if self.accounts:
            self.current_account = self.accounts[0]
            self.load_folders_from_db()
            self.start_background_sync()
        else:
            self.sync_status.setText('Brak przypisanego konta e-mail')
            self.sync_status.setToolTip('Administrator może przypisać konto w Ustawienia → Konta e-mail → Dostęp pracowników.')

        self._timer = self._make_timer(AUTO_SYNC_INTERVAL_MS, self.start_background_sync)
        self._assignment_timer = self._make_timer(15000, self._refresh_chips)

    def _email_accounts_for_employee(self):
        """Konta poczty pracownika, przez `app.email.email_ui`, gdy strona jest tam
        re-eksportowana: testy i skrypty podmieniają tę funkcję na starym module."""
        import sys
        module = sys.modules.get('app.email.email_ui')
        fetch = getattr(module, 'get_email_accounts_for_employee', None) \
            if module is not None else None
        return (fetch or get_email_accounts_for_employee)(self.pracownik_id)

    def _make_runner(self, name: str):
        from app.utils.background import CommandRunner, TaskRunner
        return (CommandRunner if name == 'CommandRunner' else TaskRunner)(self)

    def _make_timer(self, interval: int, slot, *, start: bool = True):
        from PyQt6.QtCore import QTimer
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(interval)
        timer.timeout.connect(slot)
        if start:
            timer.start()
        return timer

    def _reload_employees(self) -> None:
        from app.db.dao_employees import list_employees
        try:
            employees = [e for e in list_employees() if e.get('aktywny', 1)]
        except Exception:
            employees = []
        self.label_numbers = resolve_label_numbers(employees)
        employees.sort(key=lambda e: (self.label_numbers.get(e["id"], 99), e["id"]))
        self.employees = employees


__all__ = ['EmailPage', 'FolderTree', 'MailArea', 'MailBrowser', 'ROLE_ROW_ID']
