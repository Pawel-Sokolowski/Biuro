from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QSpinBox, QComboBox, QTableWidget,
    QHeaderView, QPushButton, QTextEdit, QTabWidget, QSplitter,
    QMessageBox, QInputDialog, QTableWidgetItem
)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt, QThreadPool

import imaplib

from app.db.dao_companies import get_conn
from app.utils.crypto import encrypt_password, decrypt_password

from app.email.email_actions import SyncRunnable
from app.email.email_storage import (
    refresh_email_accounts,
    refresh_email_filters,
    add_email_filter,
    delete_email_filter,
    add_email_account,
    delete_email_account,
)
from app.email.email_sync import (
    sync_emails,
    load_email_message,
    list_attachments,
    download_attachment,
    move_email_to_folder
)

class EmailPage(QWidget):

    def __init__(self, parent, pracownik_id):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self.current_attachments = {}
        self.current_attachment_meta = None
        self.thread_pool = QThreadPool.globalInstance()
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        title = QLabel("Zarządzanie kontami Email")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        layout.addWidget(title)

        self.email_tabs = QTabWidget()
        self.email_tabs.addTab(self._create_reader_tab(), "Przegląd Emaili")
        self.email_tabs.addTab(self._create_accounts_tab(), "Konta Email")
        self.email_tabs.addTab(self._create_filters_tab(), "Filtry")
        layout.addWidget(self.email_tabs)

    def _create_reader_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Folder:"))
        self.email_folder_combo = QComboBox()
        toolbar.addWidget(self.email_folder_combo)
        toolbar.addWidget(QLabel("Konto:"))
        self.email_account_combo = QComboBox()
        self.email_account_combo.currentIndexChanged.connect(self._load_imap_folders)
        toolbar.addWidget(self.email_account_combo)
        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self.refresh_email_list)
        toolbar.addWidget(btn_refresh)
        layout.addLayout(toolbar)

        self.table_email_list = QTableWidget()
        self.table_email_list.setColumnCount(5)
        self.table_email_list.setHorizontalHeaderLabels(["UID", "Od", "Temat", "Data", "Przeczytana"])
        self.table_email_list.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_email_list.hideColumn(0)
        self.table_email_list.itemClicked.connect(self.on_email_selected)
        layout.addWidget(self.table_email_list)

        layout.addWidget(QLabel("Treść wiadomości:"))
        self.email_content = QTextEdit()
        self.email_content.setReadOnly(True)
        layout.addWidget(self.email_content)
        actions = QHBoxLayout()
        btn_move = QPushButton("Przenieś")
        btn_move.clicked.connect(self.move_selected_email)
        actions.addWidget(btn_move)

        btn_delete = QPushButton("Usuń")
        btn_delete.clicked.connect(self.delete_selected_email)
        actions.addWidget(btn_delete)

        btn_download = QPushButton("Pobierz załącznik")
        btn_download.clicked.connect(self._prompt_attachment_download)
        actions.addWidget(btn_download)
        layout.addLayout(actions)
        return tab

    def _create_accounts_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        # LEWA STRONA — lista kont
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Moje konta email:"))
        self.table_email_accounts = QTableWidget()
        self.table_email_accounts.setColumnCount(5)
        self.table_email_accounts.setHorizontalHeaderLabels(["ID", "Email", "IMAP", "Aktywne", "Akcje"])
        self.table_email_accounts.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_email_accounts.hideColumn(0)
        left_layout.addWidget(self.table_email_accounts)
        # PRAWA STRONA — dodawanie konta
        right = QWidget()
        right.setFixedWidth(380)
        right_layout = QVBoxLayout(right)
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
        self.smtp_port.setValue(465)
        self.email_password = QLineEdit()
        self.email_password.setEchoMode(QLineEdit.EchoMode.Password)

        form.addRow("Email:", self.email_input)
        form.addRow("IMAP:", self.imap_server)
        form.addRow("Port IMAP:", self.imap_port)
        form.addRow("SMTP:", self.smtp_server)
        form.addRow("Port SMTP:", self.smtp_port)
        form.addRow("Hasło:", self.email_password)
        right_layout.addLayout(form)

        btn_add = QPushButton("Dodaj konto")
        btn_add.clicked.connect(lambda: add_email_account(self, encrypt_password))
        btn_add.clicked.connect(self._refresh_email_account_combos)
        btn_add.clicked.connect(self._trigger_email_sync)
        right_layout.addWidget(btn_add)

        btn_sync = QPushButton("🔄 Synchronizuj")
        btn_sync.clicked.connect(self._trigger_email_sync)
        right_layout.addWidget(btn_sync)
        right_layout.addStretch()
        splitter.addWidget(left)
        splitter.addWidget(right)
        layout.addWidget(splitter)
        refresh_email_accounts(self)
        return tab

    def _create_filters_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        # LEWA — lista filtrów
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Aktywne filtry:"))
        self.table_email_filters = QTableWidget()
        self.table_email_filters.setColumnCount(5)
        self.table_email_filters.setHorizontalHeaderLabels(["ID", "Od", "Temat", "Folder", "Akcje"])
        self.table_email_filters.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_email_filters.hideColumn(0)
        left_layout.addWidget(self.table_email_filters)
        # PRAWA — dodawanie filtra
        right = QWidget()
        right.setFixedWidth(350)
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(QLabel("Dodaj filtr"))

        form = QFormLayout()
        self.filter_konto_combo = QComboBox()
        self.filter_od = QLineEdit()
        self.filter_temat = QLineEdit()
        self.filter_folder = QLineEdit()

        form.addRow("Konto:", self.filter_konto_combo)
        form.addRow("Od:", self.filter_od)
        form.addRow("Temat:", self.filter_temat)
        form.addRow("Folder:", self.filter_folder)

        right_layout.addLayout(form)

        btn_add = QPushButton("Dodaj filtr")
        btn_add.clicked.connect(lambda: add_email_filter(self))
        btn_add.clicked.connect(self._trigger_email_sync)
        right_layout.addWidget(btn_add)

        right_layout.addStretch()

        splitter.addWidget(left)
        splitter.addWidget(right)
        layout.addWidget(splitter)

        self._refresh_email_account_combos()
        refresh_email_filters(self)
        return tab
    def _refresh_email_account_combos(self):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT id, email FROM konta_email WHERE pracownik_id = ?", (self.pracownik_id,))
        accounts = cur.fetchall()
        conn.close()

        self.email_account_combo.clear()
        self.filter_konto_combo.clear()

        for kid, email_addr in accounts:
            self.email_account_combo.addItem(email_addr, kid)
            self.filter_konto_combo.addItem(email_addr, kid)
        self._load_imap_folders()

    def _load_imap_folders(self):
        konto_id = self.email_account_combo.currentData()
        if not konto_id:
            return

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT serwer_imap, port_imap, haslo_enc, email FROM konta_email WHERE id = ?", (konto_id,))
        row = cur.fetchone()
        conn.close()

        if not row:
            return

        imap_srv, imap_port, haslo_enc, email_addr = row
        password = decrypt_password(haslo_enc)

        try:
            imap = imaplib.IMAP4_SSL(imap_srv, imap_port)
            imap.login(email_addr, password)
            status, folders = imap.list()
            imap.logout()

            if status != "OK":
                return

            self.email_folder_combo.clear()
            for f in folders:
                parts = f.decode().split(' "/" ')
                if len(parts) == 2:
                    self.email_folder_combo.addItem(parts[1].strip('"'))

        except Exception:
            pass

    def refresh_email_list(self):
        konto_id = self.email_account_combo.currentData()
        folder = self.email_folder_combo.currentText()

        if not konto_id or not folder:
            self.table_email_list.setRowCount(0)
            return

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT uid, od, temat, data, przeczytana
            FROM wiadomosci
            WHERE konto_id = ? AND folder = ?
            ORDER BY data DESC
        """, (konto_id, folder))
        rows = cur.fetchall()
        conn.close()

        self.table_email_list.setRowCount(0)
        for r, (uid, od, temat, data, read) in enumerate(rows):
            self.table_email_list.insertRow(r)
            self.table_email_list.setItem(r, 0, QTableWidgetItem(str(uid)))
            self.table_email_list.setItem(r, 1, QTableWidgetItem(od or ""))
            self.table_email_list.setItem(r, 2, QTableWidgetItem(temat or ""))
            self.table_email_list.setItem(r, 3, QTableWidgetItem(data or ""))
            self.table_email_list.setItem(r, 4, QTableWidgetItem("Tak" if read else "Nie"))

    def on_email_selected(self, item):
        row = item.row()
        uid = self.table_email_list.item(row, 0).text()
        konto_id = self.email_account_combo.currentData()
        folder = self.email_folder_combo.currentText()

        msg = load_email_message(konto_id, folder, uid)
        if not msg:
            self.email_content.setPlainText("Nie udało się pobrać wiadomości")
            return

        self.email_content.setPlainText("Treść wiadomości pobrana z IMAP")

        attachments = list_attachments(msg)
        self.current_attachments = {att["filename"]: att["index"] for att in attachments}
        self.current_attachment_meta = {
            "konto_id": konto_id,
            "folder": folder,
            "email_uid": uid
        }
    def move_selected_email(self):
        row = self.table_email_list.currentRow()
        if row < 0:
            return

        uid = self.table_email_list.item(row, 0).text()
        konto_id = self.email_account_combo.currentData()
        folder = self.email_folder_combo.currentText()

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT serwer_imap, port_imap, haslo_enc, email FROM konta_email WHERE id = ?", (konto_id,))
        row = cur.fetchone()
        conn.close()

        if not row:
            return

        imap_srv, imap_port, haslo_enc, email_addr = row
        password = decrypt_password(haslo_enc)

        imap = imaplib.IMAP4_SSL(imap_srv, imap_port)
        imap.login(email_addr, password)

        ok = move_email_to_folder(imap, uid, folder)
        imap.logout()

        if ok:
            self.refresh_email_list()
    def delete_selected_email(self):
        row = self.table_email_list.currentRow()
        if row < 0:
            return

        uid = self.table_email_list.item(row, 0).text()
        konto_id = self.email_account_combo.currentData()

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT serwer_imap, port_imap, haslo_enc, email FROM konta_email WHERE id = ?", (konto_id,))
        row = cur.fetchone()
        conn.close()

        if not row:
            return

        imap_srv, imap_port, haslo_enc, email_addr = row
        password = decrypt_password(haslo_enc)

        imap = imaplib.IMAP4_SSL(imap_srv, imap_port)
        imap.login(email_addr, password)

        ok = move_email_to_folder(imap, uid, "Trash")
        imap.logout()

        if ok:
            self.refresh_email_list()
    def _prompt_attachment_download(self):
        if not self.current_attachments:
            return

        items = list(self.current_attachments.keys())
        filename, ok = QInputDialog.getItem(self, "Załączniki", "Wybierz:", items, 0, False)
        if not ok:
            return

        part_index = self.current_attachments[filename]
        meta = self.current_attachment_meta

        raw = download_attachment(meta["konto_id"], meta["folder"], meta["email_uid"], part_index)
        if not raw:
            return

        save_path, _ = QInputDialog.getText(self, "Zapisz jako", "Nazwa pliku:", text=filename)
        if not save_path:
            save_path = filename

        with open(save_path, "wb") as f:
            f.write(raw)
    def _trigger_email_sync(self):
        runnable = SyncRunnable(self)

        runnable.signals.finished.connect(
            lambda ok: (
                self._load_imap_folders(),
                self.refresh_email_list()
            )
        )
        runnable.signals.error.connect(
            lambda msg: QMessageBox.critical(self, "Błąd synchronizacji", msg)
        )

        self.thread_pool.start(runnable)






