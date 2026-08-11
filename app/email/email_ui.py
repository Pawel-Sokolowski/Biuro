# app/email/email_ui.py
import sqlite3
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QSpinBox, QComboBox, QTableWidget,
    QHeaderView, QPushButton, QTextEdit, QTabWidget, QSplitter,
    QMessageBox, QInputDialog
)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt

from app.db.dao_companies import get_conn
from app.utils.crypto import decrypt_password, encrypt_password
from app.email.email_actions import SyncRunnable
from app.email.email_storage import (
    refresh_email_accounts,
    refresh_email_filters,
    add_email_filter,
    delete_email_filter,
    add_email_account,
    delete_email_account,
)
from app.email.email_sync import move_email_to_folder, load_email_message, list_attachments, download_attachment


class EmailUIMixin:
    """Mixin z logiką UI dla zakładki Email."""

    current_attachments = {}
    current_attachment_meta = None

    def create_email_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)

        lbl_title = QLabel("Zarządzanie kontami Email")
        lbl_title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        layout.addWidget(lbl_title)

        self.email_tabs = QTabWidget()

        if not hasattr(self, "filter_konto_combo"):
            self.filter_konto_combo = QComboBox()

        reader_tab = self._create_email_reader_tab()
        self.email_tabs.addTab(reader_tab, "Przegląd Emaili")

        accounts_tab = self._create_email_accounts_tab()
        self.email_tabs.addTab(accounts_tab, "Konta Email")

        filters_tab = self._create_email_filters_tab()
        self.email_tabs.addTab(filters_tab, "Filtry")

        layout.addWidget(self.email_tabs)
        self.pages.addWidget(page)

        # uruchom automatyczny sync jeśli dostępny
        try:
            self._init_email_auto_sync(interval_minutes=5, start_immediately=True)
        except Exception:
            pass

    def _create_email_accounts_tab(self):
        tab_widget = QWidget()
        layout = QVBoxLayout(tab_widget)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.addWidget(QLabel("Moje konta email:"))

        self.table_email_accounts = QTableWidget()
        self.table_email_accounts.setColumnCount(5)
        self.table_email_accounts.setHorizontalHeaderLabels(
            ["ID", "Email", "Serwer IMAP", "Aktywne", "Akcje"]
        )
        self.table_email_accounts.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table_email_accounts.hideColumn(0)
        left_layout.addWidget(self.table_email_accounts)

        right_widget = QWidget()
        right_widget.setFixedWidth(380)
        right_layout = QVBoxLayout(right_widget)
        right_layout.addWidget(QLabel("Dodaj nowe konto"))

        form = QFormLayout()
        self.email_input = QLineEdit()
        self.imap_server = QLineEdit()
        self.imap_port = QSpinBox()
        self.imap_port.setMaximum(9999)
        self.imap_port.setValue(993)
        self.smtp_server = QLineEdit()
        self.smtp_port = QSpinBox()
        self.smtp_port.setMaximum(9999)
        self.smtp_port.setValue(587)
        self.email_password = QLineEdit()
        self.email_password.setEchoMode(QLineEdit.EchoMode.Password)

        form.addRow("Email:", self.email_input)
        form.addRow("Serwer IMAP:", self.imap_server)
        form.addRow("Port IMAP:", self.imap_port)
        form.addRow("Serwer SMTP:", self.smtp_server)
        form.addRow("Port SMTP:", self.smtp_port)
        form.addRow("Hasło:", self.email_password)

        right_layout.addLayout(form)

        btn_add_account = QPushButton("Dodaj konto")
        btn_add_account.setStyleSheet(
            "background-color: #3498db; color: white; font-weight: bold;"
        )
        btn_add_account.clicked.connect(
            lambda: add_email_account(self, encrypt_password)
        )
        right_layout.addWidget(btn_add_account)

        btn_sync = QPushButton("🔄 Synchronizuj emaile")
        btn_sync.setStyleSheet(
            "background-color: #27ae60; color: white; font-weight: bold;"
        )
        btn_sync.clicked.connect(lambda: self._trigger_email_sync())
        right_layout.addWidget(btn_sync)

        right_layout.addStretch()

        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        layout.addWidget(splitter)

        refresh_email_accounts(self)

        return tab_widget

    def _create_email_reader_tab(self):
        tab_widget = QWidget()
        layout = QVBoxLayout(tab_widget)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Folder:"))
        self.email_folder_combo = QComboBox()
        self.email_folder_combo.addItems(
            ["INBOX", "firma1", "firma2", "firma3", "Sent", "Drafts", "Trash"]
        )
        toolbar.addWidget(self.email_folder_combo)

        toolbar.addWidget(QLabel("Konto:"))
        self.email_account_combo = QComboBox()
        toolbar.addWidget(self.email_account_combo)

        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self.refresh_email_list)
        toolbar.addWidget(btn_refresh)

        layout.addLayout(toolbar)

        self.table_email_list = QTableWidget()
        self.table_email_list.setColumnCount(5)
        self.table_email_list.setHorizontalHeaderLabels(
            ["ID", "Od", "Temat", "Data", "Przeczytana"]
        )
        self.table_email_list.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table_email_list.hideColumn(0)
        self.table_email_list.itemClicked.connect(self.on_email_selected)
        layout.addWidget(self.table_email_list)

        layout.addWidget(QLabel("Treść wiadomości:"))
        self.email_content = QTextEdit()
        self.email_content.setReadOnly(True)
        layout.addWidget(self.email_content)

        action_layout = QHBoxLayout()
        btn_reply = QPushButton("Odpowiedź")
        btn_reply.clicked.connect(self.reply_to_email)
        action_layout.addWidget(btn_reply)

        btn_forward = QPushButton("Prześlij dalej")
        btn_forward.clicked.connect(self.forward_email)
        action_layout.addWidget(btn_forward)

        btn_move = QPushButton("Przenieś do folderu")
        btn_move.clicked.connect(self.move_selected_email)
        action_layout.addWidget(btn_move)

        btn_delete = QPushButton("Usuń")
        btn_delete.setStyleSheet("background-color: #e74c3c; color: white;")
        btn_delete.clicked.connect(self.delete_selected_email)
        action_layout.addWidget(btn_delete)

        btn_download_att = QPushButton("Pobierz załącznik")
        btn_download_att.clicked.connect(self._prompt_attachment_download)
        action_layout.addWidget(btn_download_att)

        layout.addLayout(action_layout)

        return tab_widget

    def _create_email_filters_tab(self):
        tab_widget = QWidget()
        layout = QVBoxLayout(tab_widget)

        layout.addWidget(QLabel("Automatyczne filtry dla emaili"))

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.addWidget(QLabel("Aktywne filtry:"))

        self.table_email_filters = QTableWidget()
        self.table_email_filters.setColumnCount(5)
        self.table_email_filters.setHorizontalHeaderLabels(
            ["ID", "Od", "Temat zawiera", "Folder docelowy", "Akcje"]
        )
        self.table_email_filters.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table_email_filters.hideColumn(0)
        left_layout.addWidget(self.table_email_filters)

        right_widget = QWidget()
        right_widget.setFixedWidth(350)
        right_layout = QVBoxLayout(right_widget)
        right_layout.addWidget(QLabel("Dodaj nowy filtr"))

        form = QFormLayout()

        if not hasattr(self, "filter_konto_combo") or self.filter_konto_combo is None:
            self.filter_konto_combo = QComboBox()

        self.filter_od = QLineEdit()
        self.filter_od.setPlaceholderText("np: boss@firma.pl")
        self.filter_temat = QLineEdit()
        self.filter_temat.setPlaceholderText("np: Faktura")
        self.filter_folder = QLineEdit()
        self.filter_folder.setPlaceholderText("np: Faktury")

        form.addRow("Konto:", self.filter_konto_combo)
        form.addRow("Od:", self.filter_od)
        form.addRow("Temat zawiera:", self.filter_temat)
        form.addRow("Folder docelowy:", self.filter_folder)

        right_layout.addLayout(form)

        info_label = QLabel(
            "Uwaga: Jeśli pola 'Od' i 'Temat' są puste, filtr stosuje się do wszystkich emaili\n"
            "Filtry są stosowane automatycznie przy synchronizacji"
        )
        info_label.setStyleSheet("color: #7f8c8d; font-size: 10px;")
        right_layout.addWidget(info_label)

        btn_add_filter = QPushButton("Dodaj filtr")
        btn_add_filter.setStyleSheet(
            "background-color: #3498db; color: white; font-weight: bold;"
        )
        btn_add_filter.clicked.connect(lambda: add_email_filter(self))
        right_layout.addWidget(btn_add_filter)

        right_layout.addStretch()

        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        layout.addWidget(splitter)

        self._refresh_email_account_combos()
        refresh_email_filters(self)

        return tab_widget

    def _refresh_email_account_combos(self):
        if not hasattr(self, "email_account_combo") or self.email_account_combo is None:
            self.email_account_combo = QComboBox()
        if not hasattr(self, "filter_konto_combo") or self.filter_konto_combo is None:
            self.filter_konto_combo = QComboBox()

        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, email FROM konta_email WHERE pracownik_id = ?",
            (self.pracownik_id,),
        )
        accounts = cursor.fetchall()

        self.email_account_combo.clear()
        self.filter_konto_combo.clear()
        for kid, email_addr in accounts:
            self.email_account_combo.addItem(email_addr, kid)
            self.filter_konto_combo.addItem(email_addr, kid)

        conn.close()

    def _decode_email_body(self, msg):
        body = ""
        html = None

        try:
            if msg.is_multipart():
                for part in msg.walk():
                    ctype = part.get_content_type()
                    if part.get_content_maintype() == 'multipart':
                        continue
                    payload = part.get_payload(decode=True)
                    if payload is None:
                        continue
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        decoded = payload.decode(charset, errors="replace")
                    except Exception:
                        decoded = payload.decode("utf-8", errors="replace")
                    if ctype == "text/plain" and not body:
                        body = decoded
                    elif ctype == "text/html":
                        html = decoded
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    charset = msg.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="replace")
        except Exception:
            try:
                body = str(msg)
            except Exception:
                body = ""

        return html if html else body

    def on_email_selected(self, item):
        row = item.row()
        msg_id_item = self.table_email_list.item(row, 0)
        if not msg_id_item:
            return
        msg_id = int(msg_id_item.text())

        conn = get_conn()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT od, temat, tresc, raw_msg FROM wiadomosci WHERE id = ?",
            (msg_id,),
        )
        result = cursor.fetchone()
        conn.close()

        raw = None
        if result:
            od, temat, tresc, raw = result
            if raw:
                try:
                    import email as email_mod
                    msg = email_mod.message_from_bytes(raw)
                    decoded = self._decode_email_body(msg)
                    if decoded and "<html" in decoded.lower():
                        self.email_content.setHtml(decoded)
                        # załączniki i meta będą ustawione poniżej
                    else:
                        self.email_content.setPlainText(f"Od: {od}\nTemat: {temat}\n\n{decoded or tresc or ''}")
                except Exception:
                    self.email_content.setPlainText(f"Od: {od}\nTemat: {temat}\n\n{tresc or ''}")
            else:
                self.email_content.setPlainText(f"Od: {od}\nTemat: {temat}\n\n{tresc or ''}")

        # --- ZAŁĄCZNIKI ---
        if raw:
            import email as email_mod
            msg = email_mod.message_from_bytes(raw)
            attachments = list_attachments(msg)

            if attachments:
                txt = "\n\nZałączniki:\n"
                for att in attachments:
                    txt += f"- {att['filename']} (kliknij aby pobrać)\n"
                self.email_content.append(txt)

                self.current_attachments = {att["filename"]: att["index"] for att in attachments}
                self.current_attachment_meta = {
                    "konto_id": self.email_account_combo.currentData(),
                    "folder": self.email_folder_combo.currentText(),
                    "email_uid": self.table_email_list.item(row, 0).text()
                }
            else:
                self.current_attachments = {}
                self.current_attachment_meta = None

    def download_selected_attachment(self, filename):
        if not self.current_attachment_meta:
            QMessageBox.warning(self, "Błąd", "Brak danych załącznika")
            return

        part_index = self.current_attachments.get(filename)
        if not part_index:
            QMessageBox.warning(self, "Błąd", "Nie znaleziono załącznika")
            return

        # najpierw spróbuj pobrać z serwera (download_attachment) — funkcja obsługuje IMAP
        raw = download_attachment(
            self.current_attachment_meta["konto_id"],
            self.current_attachment_meta["folder"],
            self.current_attachment_meta["email_uid"],
            part_index
        )

        if not raw:
            QMessageBox.critical(self, "Błąd", "Nie udało się pobrać załącznika")
            return

        save_path, _ = QInputDialog.getText(self, "Zapisz jako", "Podaj nazwę pliku:", text=filename)
        if not save_path:
            save_path = filename

        with open(save_path, "wb") as f:
            f.write(raw)

        QMessageBox.information(self, "OK", f"Załącznik zapisany jako {save_path}")

    def _prompt_attachment_download(self):
        if not self.current_attachments:
            QMessageBox.information(self, "Brak", "Brak załączników")
            return

        items = list(self.current_attachments.keys())
        filename, ok = QInputDialog.getItem(self, "Załączniki", "Wybierz:", items, 0, False)
        if ok:
            self.download_selected_attachment(filename)

    def move_selected_email(self):
        row = self.table_email_list.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Błąd", "Wybierz wiadomość do przeniesienia")
            return

        email_uid_item = self.table_email_list.item(row, 0)
        if not email_uid_item:
            QMessageBox.warning(self, "Błąd", "Brak identyfikatora wiadomości")
            return
        email_uid = email_uid_item.text()
        konto_id = self.email_account_combo.currentData()
        target_folder = self.email_folder_combo.currentText()

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT serwer_imap, port_imap, haslo_enc, email
            FROM konta_email WHERE id = ?
        """, (konto_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            QMessageBox.critical(self, "Błąd", "Nie można pobrać danych konta")
            return
        imap_srv, imap_port, haslo_enc, email_addr = row
        password = decrypt_password(haslo_enc)

        import imaplib
        try:
            imap = imaplib.IMAP4_SSL(imap_srv, imap_port, timeout=30)
            imap.login(email_addr, password)
            try:
                imap.create(target_folder)
            except Exception:
                pass
            ok = move_email_to_folder(imap, email_uid, target_folder)
            imap.logout()
            if ok:
                QMessageBox.information(self, "Sukces", "Wiadomość przeniesiona")
                self.refresh_email_list()
            else:
                QMessageBox.critical(self, "Błąd", "Nie udało się przenieść wiadomości")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Błąd połączenia IMAP: {str(e)}")

    def delete_selected_email(self):
        row = self.table_email_list.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Błąd", "Wybierz wiadomość do usunięcia")
            return

        email_uid_item = self.table_email_list.item(row, 0)
        if not email_uid_item:
            QMessageBox.warning(self, "Błąd", "Brak identyfikatora wiadomości")
            return
        email_uid = email_uid_item.text()
        konto_id = self.email_account_combo.currentData()

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT serwer_imap, port_imap, haslo_enc, email
            FROM konta_email WHERE id = ?
        """, (konto_id,))
        row = cur.fetchone()
        conn.close()
        if not row:
            QMessageBox.critical(self, "Błąd", "Nie można pobrać danych konta")
            return
        imap_srv, imap_port, haslo_enc, email_addr = row
        password = decrypt_password(haslo_enc)

        import imaplib
        try:
            imap = imaplib.IMAP4_SSL(imap_srv, imap_port, timeout=30)
            imap.login(email_addr, password)
            destination = "Trash"
            try:
                imap.create(destination)
            except Exception:
                pass
            ok = move_email_to_folder(imap, email_uid, destination)
            imap.logout()
            if ok:
                QMessageBox.information(self, "Sukces", "Wiadomość przeniesiona do kosza")
                self.refresh_email_list()
            else:
                QMessageBox.critical(self, "Błąd", "Nie udało się usunąć wiadomości")
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Błąd połączenia IMAP: {str(e)}")

    def _trigger_email_sync(self):
        # upewnij się, że thread_pool istnieje w głównym oknie
        if not hasattr(self, "thread_pool") or self.thread_pool is None:
            from PyQt6.QtCore import QThreadPool
            self.thread_pool = QThreadPool.globalInstance()
        runnable = SyncRunnable(self)
        self.thread_pool.start(runnable)
