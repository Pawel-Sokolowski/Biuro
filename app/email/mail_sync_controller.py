"""Synchronizacja poczty w tle: harmonogram, postęp i odświeżenie po zakończeniu."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from app.utils.background import LatestRead

import logging

import threading
import time
from datetime import datetime

from PyQt6.QtCore import QTimer

from app.email.email_helpers import decode_mailbox_utf7
from app.email.email_storage import get_email_accounts_for_employee
from app.email.email_sync import sync_automatic_accounts


class MailSyncMixin:
    def start_background_sync(self) -> None:
        if self._sync_cancel.is_set():
            return
        if self._sync_running:
            self._resync_pending = True
            return
        if time.monotonic() - self._last_account_check > 30:
            try:
                accounts = self._email_accounts_for_employee()
                current_id = self.current_account["id"] if self.current_account else None
                self.accounts = accounts
                self.current_account = next((a for a in accounts if a["id"] == current_id), accounts[0] if accounts else None)
                self._last_account_check = time.monotonic()
                if self.current_account is None and current_id is not None:
                    self.current_folder_local = None
                    self.current_uid = None
                    self.mail_list.clear()
                    self.folder_tree.clear()
                    self._clear_view()
                    self._refresh_chips()
                if self.current_account and self.current_account["id"] != current_id:
                    self.current_folder_local = "INBOX"
                    self.current_uid = None
                    self.load_folders_from_db()
            except Exception:
                logger.exception("Nie można odświeżyć dostępu do kont poczty")
        if not self.current_account:
            self.sync_status.setText("Brak przypisanych kont · dostęp sprawdzany co 2 min")
            return
        self._sync_running = True
        self._resync_pending = False
        self._last_sync_error = ""
        self._sync_started_at = time.monotonic()
        self.sync_status.setText("Synchronizacja w toku…")
        accounts = list(self.accounts)
        account_id = self.current_account["id"]
        folder = self.current_folder_local

        def work() -> None:
            try:
                errors = sync_automatic_accounts(accounts, account_id, folder,
                    on_headers=self.headers_ready.emit, cancel_event=self._sync_cancel,
                    on_progress=lambda done, total, name, headers: self.sync_progress.emit(("", done, total, name, headers)))
            except Exception as exc:
                logger.exception("Błąd synchronizacji poczty")
                errors = [str(exc)]
            try:
                if errors:
                    self.sync_failed.emit("\n".join(errors))
                self.sync_finished.emit(not errors)
            except RuntimeError:
                pass
        try:
            self._sync_jobs.submit(work)
        except Exception:
            self._sync_running = False
            raise
    def _on_sync_failed(self, message):
        self._last_sync_error = message
        self.sync_status.setText("Błąd synchronizacji · ponowna próba co 2 min")
        self.sync_status.setToolTip(message)
    def _on_sync_progress(self, data) -> None:
        label, done, total, folder, headers = data
        elapsed = int(time.monotonic() - getattr(self, "_sync_started_at", time.monotonic()))
        where = f" – {self._clean_folder_label(decode_mailbox_utf7(folder))}" if folder else ""
        self.sync_status.setToolTip(
            f"Foldery {done}/{total}, pobrano {headers} nagłówków{where} · czas {elapsed//60}:{elapsed%60:02d}"
        )
    def _schedule_headers_refresh(self):
        if not self._headers_refresh_timer.isActive():
            self._headers_refresh_timer.start()
    def _on_headers_ready(self) -> None:
        if not self.current_account:
            return
        self.load_folders_from_db()
        if self.current_folder_local:
            self._load_mail_async(keep_view=True)
        self._refresh_chips()
    def _on_sync_finished(self, ok: bool) -> None:
        self._headers_refresh_timer.stop()
        self._sync_running = False
        if ok:
            self.sync_status.setText(f"Ostatnia synchronizacja: {datetime.now():%H:%M:%S} · co 2 min")
            self.sync_status.setToolTip("")
        self._on_headers_ready()
        # Liczniki chipów idą już w _on_headers_ready(); drugie żądanie jest zbędne.
        if self._resync_pending and not self._sync_cancel.is_set():
            self.start_background_sync()
    def _run_async(self, work, done) -> None:
        runner = self._message_jobs if isinstance(work, LatestRead) else self._command_jobs
        runner.submit(work, lambda result: done(result, None), lambda exc: done(None, exc))
    def _on_async_result(self, payload) -> None:
        done, result, error = payload
        done(result, error)
