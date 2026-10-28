# app/admin/settings/email_settings.py

import logging
import threading

from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt, pyqtSignal

from app.email.email_sync import sync_and_prefetch
from app.db.dao_app_settings import get_setting, set_setting

logger = logging.getLogger(__name__)

from app.admin.global_settings_logic import GlobalSettingsLogic
from app.email.email_connection import (
    DEFAULT_PORTS, SECURITY_CHOICES, check_imap, check_smtp, normalize_security,
)


def _run_test(parent, account: dict) -> None:
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        ok_i, msg_i = check_imap(account)
        ok_s, msg_s = check_smtp(account)
    finally:
        QApplication.restoreOverrideCursor()

    text = (
        f"Odbieranie (IMAP): {'OK' if ok_i else 'BŁĄD'}\n{msg_i}\n\n"
        f"Wysyłanie (SMTP): {'OK' if ok_s else 'BŁĄD'}\n{msg_s}"
    )
    if ok_i and ok_s:
        QMessageBox.information(parent, "Test połączenia", text)
    else:
        QMessageBox.warning(parent, "Test połączenia", text)


class AccountDialog(QDialog):
    """Dodawanie i edycja konta e-mail (hasło przy edycji opcjonalne)."""

    def __init__(self, parent=None, acc=None):
        super().__init__(parent)
        self.editing = acc is not None
        self.setWindowTitle("Edytuj konto e-mail" if acc else "Dodaj konto e-mail")
        self.setMinimumWidth(460)
        form = QFormLayout(self)

        self.e_email = QLineEdit(acc["email"] if acc else "")
        self.e_haslo = QLineEdit()
        self.e_haslo.setEchoMode(QLineEdit.EchoMode.Password)
        if acc:
            self.e_haslo.setPlaceholderText("Zostaw puste, aby nie zmieniać")

        self.e_imap = QLineEdit(acc["serwer_imap"] if acc else "")
        self.e_imap_port = QLineEdit(str(acc["port_imap"]) if acc else "993")
        self.cb_imap = QComboBox()
        self.cb_imap.addItems(SECURITY_CHOICES)
        self.cb_imap.setCurrentText(normalize_security(acc["ssl_imap"] if acc else "SSL/TLS"))

        self.e_smtp = QLineEdit(acc["serwer_smtp"] if acc else "")
        self.e_smtp_port = QLineEdit(str(acc["port_smtp"]) if acc else "465")
        self.cb_smtp = QComboBox()
        self.cb_smtp.addItems(SECURITY_CHOICES)
        self.cb_smtp.setCurrentText(normalize_security(acc["ssl_smtp"] if acc else "SSL/TLS"))

        self.cb_imap.currentTextChanged.connect(
            lambda t: self.e_imap_port.setText(str(DEFAULT_PORTS["imap"][t]))
        )
        self.cb_smtp.currentTextChanged.connect(
            lambda t: self.e_smtp_port.setText(str(DEFAULT_PORTS["smtp"][t]))
        )
        self.e_email.editingFinished.connect(self._guess_servers)

        form.addRow("Adres e-mail:", self.e_email)
        form.addRow("Hasło:", self.e_haslo)
        form.addRow(QLabel("<b>Poczta przychodząca (IMAP)</b>"))
        form.addRow("Serwer:", self.e_imap)
        form.addRow("Port:", self.e_imap_port)
        form.addRow("Zabezpieczenia połączenia:", self.cb_imap)
        form.addRow(QLabel("<b>Poczta wychodząca (SMTP)</b>"))
        form.addRow("Serwer:", self.e_smtp)
        form.addRow("Port:", self.e_smtp_port)
        form.addRow("Zabezpieczenia połączenia:", self.cb_smtp)

        hint = QLabel(
            "Gmail, Outlook i część innych dostawców wymagają hasła aplikacji, "
            "a nie zwykłego hasła do konta."
        )
        hint.setWordWrap(True)
        form.addRow(hint)

        btn_test = QPushButton("Testuj połączenie")
        btn_test.clicked.connect(self._test)
        form.addRow(btn_test)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self._acc = acc

    def _guess_servers(self):
        if self.editing or self.e_imap.text() or self.e_smtp.text():
            return
        email = self.e_email.text().strip()
        if "@" not in email:
            return
        domain = email.split("@", 1)[1].lower()
        self.e_imap.setText(f"imap.{domain}")
        self.e_smtp.setText(f"smtp.{domain}")

    def values(self) -> dict:
        return {
            "email": self.e_email.text().strip(),
            "haslo": self.e_haslo.text(),
            "serwer_imap": self.e_imap.text().strip(),
            "port_imap": int(self.e_imap_port.text() or 0),
            "ssl_imap": self.cb_imap.currentText(),
            "serwer_smtp": self.e_smtp.text().strip(),
            "port_smtp": int(self.e_smtp_port.text() or 0),
            "ssl_smtp": self.cb_smtp.currentText(),
        }

    def _test(self):
        try:
            data = self.values()
        except ValueError:
            QMessageBox.warning(self, "Błąd", "Port musi być liczbą.")
            return
        if not data["haslo"] and self._acc:
            data["haslo_enc"] = self._acc["haslo_enc"]
        _run_test(self, data)


class EmployeeAccessDialog(QDialog):
    def __init__(self, parent, employees, selected_ids):
        super().__init__(parent)
        self.setWindowTitle("Dostęp pracowników do konta")
        self.setMinimumWidth(340)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Pracownicy, którzy widzą to konto w module Poczta:"))

        self.list = QListWidget()
        for e in employees:
            name = f"{e['imie'] or ''} {e['nazwisko'] or ''}".strip() or e["email"]
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, e["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if e["id"] in selected_ids else Qt.CheckState.Unchecked
            )
            self.list.addItem(item)
        layout.addWidget(self.list)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_ids(self):
        return [
            self.list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.list.count())
            if self.list.item(i).checkState() == Qt.CheckState.Checked
        ]


class EmailSettings(QWidget):
    """Konta e-mail: konfiguracja i ręczne pełne pobieranie skrzynki."""

    full_sync_progress = pyqtSignal(object)
    full_sync_finished = pyqtSignal(object)
    full_sync_filters = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.logic = GlobalSettingsLogic()
        self._full_sync_running = False
        self.full_sync_progress.connect(self._on_full_sync_progress)
        self.full_sync_finished.connect(self._on_full_sync_finished)
        self.full_sync_filters.connect(self._on_full_sync_filters)
        self._last_filter_stats = None

        layout = QVBoxLayout(self)

        title = QLabel("Konta e-mail")
        title.setFont(QFont("Arial", 12, QFont.Weight.Bold))
        layout.addWidget(title)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["ID", "E-mail", "Serwer IMAP", "Serwer SMTP", "Aktywne", "Dostęp pracowników"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        btn_row = QHBoxLayout()
        for text, slot in (
            ("Odśwież", self._refresh),
            ("Dodaj konto", self._add),
            ("Edytuj wybrane", self._edit_selected),
            ("Testuj połączenie", self._test_selected),
            ("Dostęp pracowników", lambda: self._edit_access()),
            ("Aktywuj / Dezaktywuj", self._toggle_active),
            ("Ustaw jako główne do wysyłania", self._set_default_sender),
            ("Domyślna stopka…", self._edit_signature_template),
            ("Etykiety pracowników…", self._edit_labels),
            ("Usuń", self._delete),
        ):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            btn_row.addWidget(btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.sync_two_years_only = QCheckBox("Pobieraj tylko ostatnie 2 lata (730 dni)")
        self.sync_two_years_only.setChecked(True)
        layout.addWidget(self.sync_two_years_only)

        self.auto_folder_filters = QCheckBox(
            "Automatycznie twórz filtry z nadawców i odbiorców w istniejących folderach"
        )
        self.auto_folder_filters.setChecked(True)
        layout.addWidget(self.auto_folder_filters)

        domains_row = QHBoxLayout()
        domains_row.addWidget(QLabel("Domeny biura do pominięcia:"))
        self.internal_mail_domains = QLineEdit(get_setting("email_internal_domains") or "")
        self.internal_mail_domains.setPlaceholderText("np. biuro.pl, moja-domena.pl")
        self.internal_mail_domains.setToolTip(
            "Oddziel domeny przecinkiem. Domeny własnych kont pocztowych "
            "są pomijane automatycznie (poza publicznymi dostawcami poczty)."
        )
        domains_row.addWidget(self.internal_mail_domains)
        layout.addLayout(domains_row)

        self.full_sync_button = QPushButton("Synchronizuj wszystkie foldery")
        self.full_sync_button.clicked.connect(self._sync_full_mailbox)
        layout.addWidget(self.full_sync_button)
        self.full_sync_status = QLabel(
            "Synchronizację ręczną można ograniczyć do dwóch lat i wykorzystać "
            "do utworzenia filtrów bez analizowania domen wewnętrznych."
        )
        self.full_sync_status.setWordWrap(True)
        layout.addWidget(self.full_sync_status)

        self._refresh()

    def _edit_labels(self) -> None:
        from app.email.email_labels import (
            EMPLOYEE_LABEL_NUMBERS,
            label_color,
            resolve_label_numbers,
            save_label_map,
        )

        employees = [e for e in self.logic.list_employees() if e.get("aktywny", 1)]
        current = resolve_label_numbers(employees)
        dlg = QDialog(self)
        dlg.setWindowTitle("Etykiety pracowników (klawisze 1–2, 4–7; 3 = Zrobione)")
        form = QFormLayout(dlg)
        combos = {}
        for emp in employees:
            combo = QComboBox()
            combo.addItem("— brak —", 0)
            for n in EMPLOYEE_LABEL_NUMBERS:
                combo.addItem(f"{n}", n)
                combo.setItemData(combo.count() - 1, QColor(label_color(n)), Qt.ItemDataRole.ForegroundRole)
            combo.setCurrentIndex(max(0, combo.findData(current.get(emp["id"], 0))))
            combos[emp["id"]] = combo
            name = f"{emp.get('imie') or ''} {emp.get('nazwisko') or ''}".strip() or emp.get("email", "")
            form.addRow(name, combo)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        mapping = {eid: c.currentData() for eid, c in combos.items() if c.currentData()}
        try:
            save_label_map(mapping)
        except ValueError as exc:
            QMessageBox.warning(self, "Etykiety", str(exc))
            return
        QMessageBox.information(self, "Etykiety", "Zapisano. Odśwież zakładkę poczty, aby zobaczyć zmiany.")

    def _employee_names(self):
        names = {}
        for e in self.logic.list_employees():
            names[e["id"]] = (
                f"{e['imie'] or ''} {e['nazwisko'] or ''}".strip() or e["email"]
            )
        return names

    def _refresh(self):
        rows = self.logic.get_email_accounts()
        names = self._employee_names()
        self.table.setRowCount(0)

        for r in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            access = ", ".join(
                names.get(i, str(i)) for i in self.logic.get_account_employee_ids(r["id"])
            )
            values = [
                str(r["id"]), r["email"] or "", r["serwer_imap"] or "",
                r["serwer_smtp"] or "", "Tak" if r["aktywne"] else "Nie",
                access or "— nikt —",
            ]
            for col, val in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(val))

    def _sync_full_mailbox(self) -> None:
        if self._full_sync_running:
            return
        account_id = self._selected_id()
        if account_id is None:
            return
        two_years_only = self.sync_two_years_only.isChecked()
        learn_filters = self.auto_folder_filters.isChecked()
        window = "ostatnich 730 dni" if two_years_only else "całej historii"
        extra = (
            "Po synchronizacji powstaną filtry na podstawie już posortowanych "
            "wiadomości (z wyjątkiem domen wewnętrznych)."
            if learn_filters else "Filtry nie będą tworzone."
        )
        if QMessageBox.question(
            self,
            "Synchronizacja poczty",
            f"Pobrać wszystkie foldery i wiadomości z {window} wybranego konta IMAP?\n\n"
            f"{extra}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return

        try:
            set_setting("email_internal_domains", self.internal_mail_domains.text().strip())
        except Exception as exc:
            QMessageBox.warning(self, "Ustawienia domen", f"Nie udało się zapisać domen: {exc}")
            return

        self._last_filter_stats = None
        self._full_sync_running = True
        self.full_sync_button.setEnabled(False)
        self.full_sync_status.setText(f"Rozpoczynam pełną synchronizację konta {account_id}...")

        def work():
            error = None
            try:
                sync_and_prefetch(
                    account_id, ["INBOX"],
                    all_folders=two_years_only,
                    full_mailbox=not two_years_only,
                    learn_filters=learn_filters,
                    on_progress=lambda done, total, folder, headers: self.full_sync_progress.emit(
                        (done, total, folder, headers)
                    ),
                    on_filters=lambda stats: self.full_sync_filters.emit(stats),
                )
            except Exception as exc:
                logger.exception("Błąd pełnej synchronizacji konta %s", account_id)
                error = str(exc)
            try:
                self.full_sync_finished.emit((account_id, error))
            except RuntimeError:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _on_full_sync_progress(self, progress) -> None:
        done, total, folder, headers = progress
        self.full_sync_status.setText(
            f"Pełna synchronizacja: folder {done}/{total}, "
            f"zapisano {headers} nagłówków. {folder or ''}"
        )

    def _on_full_sync_filters(self, stats) -> None:
        self._last_filter_stats = stats

    def _on_full_sync_finished(self, result) -> None:
        account_id, error = result
        self._full_sync_running = False
        self.full_sync_button.setEnabled(True)
        if error:
            self.full_sync_status.setText(f"Synchronizacja konta {account_id}: błąd — {error}")
            QMessageBox.warning(self, "Synchronizacja", f"Nie udało się ukończyć synchronizacji:\n{error}")
        else:
            message = "Zakończono pobieranie wiadomości do PostgreSQL."
            if self._last_filter_stats is not None:
                stats = self._last_filter_stats
                message += (
                    f"\nRozpoznano adresów: {stats['addresses']}"
                    f"\nNowych filtrów: {stats['added']}"
                    f"\nPrzeniesiono wiadomości z Odebranych: {stats['moved']}"
                )
            self.full_sync_status.setText(f"Synchronizacja konta {account_id} zakończona.")
            QMessageBox.information(self, "Synchronizacja", message)

    def _selected_id(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "E-mail", "Wybierz konto.")
            return None
        return int(self.table.item(row, 0).text())

    def _add(self):
        dlg = AccountDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            v = dlg.values()
            if not v["email"] or not v["haslo"]:
                QMessageBox.warning(self, "Błąd", "Podaj adres e-mail i hasło.")
                return
            acc_id = self.logic.add_email_account(
                email=v["email"], imap_server=v["serwer_imap"],
                smtp_server=v["serwer_smtp"], password=v["haslo"],
                imap_port=v["port_imap"], smtp_port=v["port_smtp"],
                imap_ssl=v["ssl_imap"], smtp_ssl=v["ssl_smtp"],
            )
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się dodać: {e}")
            return

        self._refresh()
        self._edit_access(acc_id, after_add=True)

    def _edit_selected(self):
        acc_id = self._selected_id()
        if acc_id is None:
            return
        acc = self.logic.get_email_account(acc_id)
        if not acc:
            QMessageBox.critical(self, "Błąd", "Nie znaleziono konta.")
            return

        dlg = AccountDialog(self, acc)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            v = dlg.values()
            self.logic.update_email_account(
                acc_id=acc_id, email=v["email"],
                imap=v["serwer_imap"], imap_port=v["port_imap"],
                smtp=v["serwer_smtp"], smtp_port=v["port_smtp"],
                ssl_imap=v["ssl_imap"], ssl_smtp=v["ssl_smtp"],
            )
            if v["haslo"]:
                self.logic.update_email_password(acc_id, v["haslo"])
            self._refresh()
            QMessageBox.information(self, "OK", "Zapisano zmiany.")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się zapisać: {e}")

    def _test_selected(self):
        acc_id = self._selected_id()
        if acc_id is None:
            return
        acc = self.logic.get_email_account(acc_id)
        if acc:
            _run_test(self, dict(acc))

    def _edit_access(self, acc_id=None, after_add=False):
        if acc_id is None or isinstance(acc_id, bool):
            acc_id = self._selected_id()
        if acc_id is None:
            return
        dlg = EmployeeAccessDialog(
            self, self.logic.list_employees(), self.logic.get_account_employee_ids(acc_id)
        )
        if after_add:
            dlg.setWindowTitle("Kto ma mieć dostęp do nowego konta?")
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.logic.set_account_employees(acc_id, dlg.selected_ids())
            self._refresh()

    def _set_default_sender(self):
        from app.email.signature import default_sender_id, set_default_sender_id

        acc_id = self._selected_id()
        if acc_id is None:
            return
        set_default_sender_id(acc_id)
        QMessageBox.information(
            self, "E-mail",
            "To konto będzie domyślnym nadawcą nowych wiadomości "
            "(dla pracowników, którzy mają do niego dostęp).",
        )

    def _edit_signature_template(self):
        from app.email.signature import DEFAULT_TEMPLATE, get_template, save_template

        labels = {
            "stopka_szablon_pozdrowienie": "Pozdrowienie:",
            "stopka_szablon_firma": "Nazwa firmy:",
            "stopka_szablon_adres": "Adres (kilka linii):",
            "stopka_szablon_telefon": "Telefon:",
            "stopka_szablon_email": "E-mail biura:",
            "stopka_szablon_zastrzezenie": "Zastrzeżenie prawne:",
        }
        current = get_template()
        dlg = QDialog(self)
        dlg.setWindowTitle("Domyślna stopka wiadomości")
        dlg.setMinimumWidth(520)
        form = QFormLayout(dlg)
        fields = {}
        for key in DEFAULT_TEMPLATE:
            if key in ("stopka_szablon_adres", "stopka_szablon_zastrzezenie"):
                field = QTextEdit(current[key])
                field.setFixedHeight(60 if key.endswith("adres") else 130)
            else:
                field = QLineEdit(current[key])
            fields[key] = field
            form.addRow(labels[key], field)
        form.addRow(QLabel(
            "Szablon służy do wygenerowania stopki pracownika (imię i nazwisko "
            "wstawiane są automatycznie)."
        ))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            save_template({
                k: (f.toPlainText() if isinstance(f, QTextEdit) else f.text()).strip()
                for k, f in fields.items()
            })
            QMessageBox.information(self, "OK", "Zapisano szablon stopki.")

    def _toggle_active(self):
        acc_id = self._selected_id()
        if acc_id is None:
            return
        try:
            self.logic.toggle_email_account_active(acc_id)
            self._refresh()
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się zmienić statusu: {e}")

    def _delete(self):
        acc_id = self._selected_id()
        if acc_id is None:
            return
        if QMessageBox.question(
            self, "Usuń konto", "Usunąć konto wraz z przypisaniami?"
        ) == QMessageBox.StandardButton.Yes:
            self.logic.delete_email_account(acc_id)
            self._refresh()
