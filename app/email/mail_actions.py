"""Akcje na wiadomościach: nowa, odpowiedź, przekaż, archiwizacja, spam, usuwanie."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from app.email.mail_constants import ROLE_ACCOUNT, ROLE_DATA, ROLE_ROW_ID

from PyQt6.QtWidgets import QApplication
from PyQt6.QtWidgets import QDialog
from PyQt6.QtWidgets import QFileDialog
from PyQt6.QtWidgets import QMenu
from app.email.email_actions import load_message
from app.email.email_actions import send_email_smtp
from app.email.email_helpers import attachment_name
from app.email.email_helpers import decode_mailbox_utf7
from app.email.email_rules import apply_rules_to_folder
from app.email.email_rules import import_thunderbird_filters
from app.email.email_storage import get_email_accounts_for_employee
from app.email.email_sync import delete_email_remote
from email.utils import parseaddr
from typing import List

import json
import logging

import email
import os
import re
import shutil
import tempfile
from html import escape as html_escape
from typing import Any, Dict, Optional

from PyQt6.QtWidgets import QMessageBox

from app.email.compose_dialog import ComposeDialog
from app.email.email_helpers import (extract_html, extract_text, get_attachments,
                                     iter_message_attachments, safe_attachment_name,
                                     viewable_attachment_extension)
from app.email.email_storage import (find_special_folder, get_assignment_map,
                                     get_completed_map, resolve_assigned_message,
                                     resolve_notification_message, set_messages_completed,
                                     set_employee_assignment)


class MailActionsMixin:
    def on_new_message(self) -> None:
        if self.current_account:
            self._open_compose("Nowa wiadomość")
    def on_reply(self) -> None:
        original = self._current_original()
        if original is None:
            return
        to_addr = (
            parseaddr(str(original.get("Reply-To") or ""))[1]
            or parseaddr(str(original.get("From") or ""))[1]
        )
        subject = str(original.get("Subject") or "")
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        self._open_compose(
            "Odpowiedź", reply_to=original, copy_folder=self._reply_copy_folder(),
            to=to_addr, subject=subject,
            html=self._quote_html(original, "reply"),
        )
    def _reply_copy_folder(self) -> Optional[str]:
        """Odpowiedź zostaje w folderze oryginału (chyba że to kosz, spam albo szkice)."""
        folder = self.current_folder_local
        if not folder or self._role_of(folder) in ("trash", "junk", "drafts"):
            return None
        return folder
    def on_forward(self) -> None:
        original = self._current_original()
        if original is None:
            return
        subject = str(original.get("Subject") or "")
        if not subject.lower().startswith(("fwd:", "fw:")):
            subject = f"Fwd: {subject}"
        self._open_compose(
            "Przekaż wiadomość",
            copy_folder=self._reply_copy_folder(),
            subject=subject,
            html=self._quote_html(original, "forward"),
            attachments=self._save_attachments_to_temp(original),
        )
    @staticmethod
    def _quote_html(original: email.message.Message, mode: str) -> str:
        """Cytat oryginału w stylu Thunderbirda: „W dniu …, Nadawca pisze:” albo nagłówek przekazania."""
        sender = html_escape(str(original.get("From") or ""))
        date = html_escape(str(original.get("Date") or ""))
        content = extract_html(original) or (
            "<pre style='white-space: pre-wrap;'>" + html_escape(extract_text(original)) + "</pre>"
        )
        if mode == "forward":
            head = (
                "<p>---------- Przekazana wiadomość ----------<br>"
                f"Od: {sender}<br>Data: {date}<br>"
                f"Temat: {html_escape(str(original.get('Subject') or ''))}<br>"
                f"Do: {html_escape(str(original.get('To') or ''))}</p>"
            )
            return f"<p><br></p>{head}{content}"
        head = f"<p>W dniu {date}, {sender} pisze:</p>"
        return (
            f"<p><br></p>{head}"
            "<blockquote style='border-left: 2px solid #999; margin-left: 0; padding-left: 8px;'>"
            f"{content}</blockquote>"
        )
    @staticmethod
    def _save_attachments_to_temp(msg: email.message.Message) -> List[str]:
        paths: List[str] = []
        tmp = tempfile.mkdtemp(prefix="biuro_fwd_")
        for index, part in enumerate(iter_message_attachments(msg)):
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            name = attachment_name(part.get_filename(), payload)
            if name in ("", ".", ".."):
                name = f"zalacznik_{index}"
            # Separate directories preserve each MIME part's original filename
            # in outgoing mail while keeping equal names from overwriting.
            directory = os.path.join(tmp, str(index))
            os.mkdir(directory)
            path = os.path.join(directory, name)
            with open(path, "wb") as fh:
                fh.write(payload)
            paths.append(path)
        return paths
    def _open_compose(self, title: str, reply_to: Optional[email.message.Message] = None,
                      copy_folder: Optional[str] = None, **kwargs) -> bool:
        """Pokazuje okno wiadomości; przy błędzie wysyłki zostawia je otwarte z treścią."""
        from app.email.signature import choose_sender

        sender = choose_sender(self.accounts, self.current_account) or self.current_account
        label = self._account_label()
        if sender is not self.current_account:
            name = sender.get("display_name") or ""
            label = f"{name} <{sender.get('email', '')}>" if name else sender.get("email", "")
        dlg = ComposeDialog(
            self, from_addr=label, title=title, pracownik_id=self.pracownik_id,
            accounts=self.accounts, account_id=sender["id"], **kwargs,
        )
        def compose():
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return False
            values = dlg.values()
            account_id = values["account_id"] or sender["id"]
            accounts = [dict(a) for a in self.accounts]
            in_reply_to = str(reply_to.get("Message-ID") or "").strip() if reply_to else None
            references = str(reply_to.get("References") or "").strip() if reply_to else None

            def send():
                target = copy_folder
                if target is None:
                    from app.email.email_actions import choose_copy_folder
                    account = next((a for a in accounts if a["id"] == account_id), sender)
                    target = choose_copy_folder(account_id, account.get("email", ""),
                                                list(values["to"]) + list(values["cc"] or []), values["subject"])
                return send_email_smtp(
                    account_id, values["to"], values["subject"], values["body"], values["attachments"] or None,
                    cc=values["cc"] or None, bcc=values["bcc"] or None, html_body=values["html"],
                    in_reply_to=in_reply_to, references=references, save_copy=True, copy_folder=target)

            def finished(result, error):
                if error is None and result[0]:
                    QMessageBox.information(self, "Email", "Wiadomość została wysłana.")
                else:
                    info = str(error) if error is not None else result[1]
                    QMessageBox.warning(self, "Email", f"Nie udało się potwierdzić wysłania wiadomości:\n{info}")
                    compose()
            try:
                self._run_async(send, finished)
            except Exception as exc:
                finished(None, exc)
            return True
        return compose()
    def _current_original(self) -> Optional[email.message.Message]:
        if (not self.current_account or not self.current_uid or not self.current_folder_local
                or getattr(self, "_current_message_row_id", None) is None):
            return None
        original = load_message(
            self.current_account["id"], self.current_folder_local, self.current_uid,
            expected_row_id=getattr(self, "_current_message_row_id", None),
        )
        if original is None:
            QMessageBox.warning(self, "Email", "Nie udało się wczytać wiadomości.")
        return original
    def _account_label(self) -> str:
        acc = self.current_account or {}
        name = acc.get("display_name") or ""
        return f"{name} <{acc.get('email', '')}>" if name else acc.get("email", "")
    def on_archive(self) -> None:
        self._move_current("archive", "Archiwizuj", "Archiwum")
    def on_spam(self) -> None:
        self._move_current("junk", "Niechciane", "Spam")
    def on_delete(self) -> None:
        refs = self._targets()
        if not self.current_account or not refs:
            return
        konto_id = self.current_account["id"]
        in_trash = [r for r in refs if self._role_of(r[1]) == "trash"]
        if in_trash and len(in_trash) == len(refs):
            # Confirmation runs a nested event loop: refreshes can destroy the
            # items in refs. Capture plain identities before opening the dialog.
            targets = [(folder, str(uid), self._target_row_id(item, folder, uid)) for item, folder, uid in refs]
            if any(row_id is None for _, _, row_id in targets):
                return
            noun = "wiadomość" if len(targets) == 1 else f"wiadomości ({len(targets)})"
            if QMessageBox.question(self, "Usuń", f"Usunąć {noun} bezpowrotnie?") \
                    != QMessageBox.StandardButton.Yes:
                return

            def work():
                for folder, uid, row_id in targets:
                    if not delete_email_remote(konto_id, folder, uid, expected_row_id=row_id):
                        raise RuntimeError("Serwer odrzucił usunięcie wiadomości.")

            def done(res, err) -> None:
                if err is not None:
                    QMessageBox.warning(self, "Usuń", f"Nie udało się usunąć wiadomości:\n{err}")
                self._after_change()

            self._run_async(work, done)
            return

        self._move_current("trash", "Usuń", "Kosz")
    def open_assignment_notification(self, target):
        return self._open_notification(target, resolve_assigned_message)
    def open_message_notification(self, target):
        return self._open_notification(target, resolve_notification_message)
    def _open_notification(self, target, resolver):
        try:
            accounts = self._email_accounts_for_employee()
            account = next((account for account in accounts if account["id"] == target.get("account_id")), None)
            message = resolver(self.pracownik_id, target) if account else None
        except Exception:
            logger.exception("Nie można odnaleźć przypisanej wiadomości")
            message = None
        if not message:
            QMessageBox.information(self, "Powiadomienie", "Wiadomość została usunięta, przeniesiona bez identyfikatora lub nie jest już Tobie dostępna.")
            return False
        self._select_timer.stop()
        self._search_timer.stop()
        self.accounts = accounts
        self.current_account = account
        self.current_folder_local = message["server_name"]
        self.current_uid = None
        self.employee_filter = None
        self.completed_filter = False
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        self.load_folders_from_db()
        def select_folder(parent):
            for index in range(parent.childCount()):
                item = parent.child(index)
                if (item.data(0, ROLE_ACCOUNT) == account['id']
                        and item.data(0, ROLE_DATA) == self.current_folder_local):
                    blocked = self.folder_tree.blockSignals(True)
                    self.folder_tree.setCurrentItem(item)
                    self.folder_tree.scrollToItem(item)
                    self.folder_tree.blockSignals(blocked)
                    return True
                if select_folder(item):
                    return True
            return False
        select_folder(self.folder_tree.invisibleRootItem())
        self.load_mail_list_from_db(self.current_folder_local, ensure_row=message)
        found = None
        for item in self.mail_list.all_items():
            if item.data(0, ROLE_ROW_ID) == message["id"]:
                found = item
                break
        if found is None:
            QMessageBox.information(self, "Powiadomienie", "Wiadomość nie jest dostępna na bieżącej liście.")
            return False
        parent = found.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        self.mail_list.blockSignals(True)
        self.mail_list.clearSelection()
        self.mail_list.setCurrentItem(found)
        found.setSelected(True)
        self.mail_list.blockSignals(False)
        self.mail_list.scrollToItem(found)
        self.on_mail_clicked(found)
        return True
    def on_manual_sync(self) -> None:
        self._last_account_check = 0.0
        self.start_background_sync()
    def _open_bulk_mail(self):
        from app.email.bulk_mail_dialog import BulkMailDialog
        try:
            accounts = self._email_accounts_for_employee()
            if not accounts:
                QMessageBox.information(self, 'Mail masowy', 'Brak dostępnego konta nadawczego.')
                return
            BulkMailDialog(self.pracownik_id, accounts,
                           (self.current_account or {}).get('id'), self).exec()
        except Exception:
            logger.exception('Nie można otworzyć maila masowego')
            QMessageBox.warning(self, 'Mail masowy', 'Nie udało się wczytać kont lub firm. Sprawdź połączenie z bazą i spróbuj ponownie.')
    def _open_address_book(self):
        from app.email.address_book_ui import AddressBookDialog
        AddressBookDialog(self.pracownik_id, self).exec()
    def _on_address_clicked(self, name, address):
        from PyQt6.QtGui import QCursor
        from app.email.address_book import contact_mailbox
        menu = QMenu(self)
        menu.addAction(address).setEnabled(False)
        menu.addSeparator()
        menu.addAction('Dodaj do książki adresowej…').triggered.connect(lambda: self._add_global_contact(name, address))
        mailbox = contact_mailbox({'display_name': name, 'email': address})
        menu.addAction('Wyślij wiadomość do…').triggered.connect(lambda: self._open_compose('Nowa wiadomość', to=mailbox))
        menu.addAction('Kopiuj adres e-mail').triggered.connect(lambda: QApplication.clipboard().setText(address))
        menu.addAction('Kopiuj nazwę i adres e-mail').triggered.connect(lambda: QApplication.clipboard().setText(mailbox))
        menu.aboutToHide.connect(menu.deleteLater)
        self._address_menu = menu
        menu.popup(QCursor.pos())
    def _add_global_contact(self, name, address):
        if self._contact_editor_pending:
            return
        from app.db.dao_address_book import find_contact
        from app.email.address_book_ui import ContactDialog
        self._contact_editor_pending = True
        def loaded(contact):
            self._contact_editor_pending = False
            ContactDialog(self.pracownik_id, contact=contact,
                          initial={'email': address, 'display_name': name}, parent=self).exec()
        def failed(exc):
            self._contact_editor_pending = False
            QMessageBox.warning(self, 'Kontakt', f'Nie udało się wczytać książki adresowej: {exc}')
        self._contact_jobs.submit(lambda: find_contact(self.pracownik_id, address), loaded, failed)
    def refresh_async(self):
        self.refresh_all()
    def refresh_all(self) -> None:
        from app.remote.state import client
        if client() is not None:
            self._refresh_sidebar_async()
            if self.current_folder_local:
                self._load_mail_async(keep_view=True)
            return
        self._reload_employees()
        self.load_folders_from_db()
        if self.current_folder_local:
            self._load_mail_async(keep_view=True)
    def on_import_filters(self) -> None:
        if not self.current_account:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Importuj filtry z Thunderbirda", "", "Filtry (*.json);;Wszystkie pliki (*)"
        )
        if not path:
            return
        try:
            with open(path, encoding="utf-8-sig") as fh:
                data = json.load(fh)
            added, duplicates, skipped = import_thunderbird_filters(self.current_account["id"], data)
        except Exception as exc:
            QMessageBox.warning(self, "Filtry", f"Nie udało się zaimportować filtrów:\n{exc}")
            return
        text = f"Dodano reguł: {added}, pominięto duplikaty: {duplicates}."
        if skipped:
            text += f"\n\nNieobsługiwane filtry ({len(skipped)}):\n" + "\n".join(skipped[:15])
        QMessageBox.information(self, "Filtry", text)
    def _filter_target_folder(self) -> str:
        return self.current_folder_local or "INBOX"
    def _update_apply_filters_action(self) -> None:
        label = self._clean_folder_label(decode_mailbox_utf7(self._filter_target_folder()))
        self.apply_filters_action.setText(f"Zastosuj filtry do folderu: {label}")
    def on_apply_filters(self) -> None:
        if not self.current_account:
            return
        konto_id = self.current_account["id"]
        folder = self._filter_target_folder()

        def done(res, err) -> None:
            if err is not None:
                QMessageBox.warning(self, "Filtry", f"Nie udało się zastosować filtrów:\n{err}")
            else:
                QMessageBox.information(self, "Filtry", f"Przeniesiono wiadomości: {res}")
            self._after_change()

        self._run_async(lambda: apply_rules_to_folder(konto_id, folder), done)
    def on_more(self) -> None:
        QMessageBox.information(self, "Więcej", "Dodatkowe opcje w przygotowaniu.")
    def closeEvent(self, event) -> None:
        timer = getattr(self, "_timer", None)
        if timer:
            timer.stop()
        super().closeEvent(event)
    def stop_background_sync(self):
        self._sync_cancel.set()
        self._timer.stop()
        self._assignment_timer.stop()
        self._headers_refresh_timer.stop()
        self._search_timer.stop()
        self._cancel_mail_load()
        self._message_jobs.invalidate()
        self._contact_jobs.invalidate()
        self._contact_editor_pending = False
