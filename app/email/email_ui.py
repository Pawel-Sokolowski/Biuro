from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QSpinBox, QComboBox, QTableWidget,
    QHeaderView, QPushButton, QTextEdit, QTabWidget, QSplitter,
    QMessageBox, QInputDialog, QTableWidgetItem, QTreeWidget,
    QTreeWidgetItem, QMenu, QDialog
)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt, QThreadPool

import imaplib

from app.db.dao_companies import get_conn
from app.utils.crypto import decrypt_password

from app.email.email_actions import SyncRunnable
from app.email.email_storage import (
    refresh_email_filters,
    add_email_filter,
)
from app.email.email_sync import (
    sync_emails,
    load_email_message,
    list_attachments,
    download_attachment,
    move_email_to_folder
)


class EmailPage(QWidget):

    def __init__(self, pracownik_id: int, parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self.current_attachments = {}
        self.current_attachment_meta = None
        self.thread_pool = QThreadPool.globalInstance()
        self._build_ui()

    # ============================================================
    #  SYNC API DLA SyncRunnable
    # ============================================================

    def sync_all_emails(self) -> bool:
        # pełna synchronizacja dla pracownika
        return sync_emails(self.pracownik_id)

    # ============================================================
    #  BUDOWA UI
    # ============================================================

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        title = QLabel("Email")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        layout.addWidget(title)

        self.email_tabs = QTabWidget()
        self.email_tabs.addTab(self._create_reader_tab(), "Przegląd Emaili")
        self.email_tabs.addTab(self._create_filters_tab(), "Filtry")
        layout.addWidget(self.email_tabs)

    # ============================================================
    #  THUNDERBIRD‑STYLE READER
    # ============================================================

    def _create_reader_tab(self):
        tab = QWidget()
        layout = QHBoxLayout(tab)

        # LEWA STRONA — FOLDERY
        self.folder_tree = QTreeWidget()
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.itemClicked.connect(self._on_folder_selected)
        layout.addWidget(self.folder_tree, 1)

        # PRAWA STRONA — WIADOMOŚCI
        right = QWidget()
        right_layout = QVBoxLayout(right)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Konto:"))
        self.email_account_combo = QComboBox()
        self.email_account_combo.currentIndexChanged.connect(self._load_folders)
        toolbar.addWidget(self.email_account_combo)

        btn_refresh = QPushButton("🔄 Odśwież")
        btn_refresh.clicked.connect(self.refresh_email_list)
        toolbar.addWidget(btn_refresh)

        btn_sync = QPushButton("Synchronizuj")
        btn_sync.clicked.connect(self._trigger_email_sync)
        toolbar.addWidget(btn_sync)

        right_layout.addLayout(toolbar)

        self.table_email_list = QTableWidget()
        self.table_email_list.setColumnCount(6)
        self.table_email_list.setHorizontalHeaderLabels(
            ["UID", "Od", "Temat", "Data", "Przeczytana", "Pracownik"]
        )
        self.table_email_list.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_email_list.hideColumn(0)
        self.table_email_list.itemClicked.connect(self.on_email_selected)
        self.table_email_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table_email_list.customContextMenuRequested.connect(self._show_context_menu)
        right_layout.addWidget(self.table_email_list)

        right_layout.addWidget(QLabel("Treść wiadomości:"))
        self.email_content = QTextEdit()
        self.email_content.setReadOnly(True)
        right_layout.addWidget(self.email_content)

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

        right_layout.addLayout(actions)

        layout.addWidget(right, 3)

        self._refresh_email_account_combos()
        return tab

    # ============================================================
    #  FILTRY
    # ============================================================

    def _create_filters_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Aktywne filtry:"))
        self.table_email_filters = QTableWidget()
        self.table_email_filters.setColumnCount(5)
        self.table_email_filters.setHorizontalHeaderLabels(["ID", "Od", "Temat", "Folder", "Akcje"])
        self.table_email_filters.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_email_filters.hideColumn(0)
        left_layout.addWidget(self.table_email_filters)

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

    # ============================================================
    #  KONTA I FOLDERY
    # ============================================================

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

        self._load_folders()

    def _load_folders(self):
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

            self.folder_tree.clear()
            for f in folders:
                parts = f.decode().split(' "/" ')
                if len(parts) == 2:
                    folder_name = parts[1].strip('"')
                    item = QTreeWidgetItem([folder_name])
                    self.folder_tree.addTopLevelItem(item)

        except Exception:
            pass

    def _on_folder_selected(self, item):
        folder = item.text(0)
        self.refresh_email_list(folder_override=folder)

    # ============================================================
    #  LISTA WIADOMOŚCI
    # ============================================================

    def refresh_email_list(self, folder_override=None):
        konto_id = self.email_account_combo.currentData()
        folder_item = self.folder_tree.currentItem()
        folder = folder_override or (folder_item.text(0) if folder_item else "")

        if not konto_id or not folder:
            self.table_email_list.setRowCount(0)
            return

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT w.uid, w.od, w.temat, w.data, w.przeczytana,
                   p.imie || ' ' || p.nazwisko AS pracownik
            FROM wiadomosci w
            LEFT JOIN pracownicy p ON w.pracownik_id = p.id
            WHERE w.konto_id = ? AND w.folder = ?
            ORDER BY w.data DESC
        """, (konto_id, folder))
        rows = cur.fetchall()
        conn.close()

        self.table_email_list.setRowCount(0)
        for r, row in enumerate(rows):
            self.table_email_list.insertRow(r)
            self.table_email_list.setItem(r, 0, QTableWidgetItem(str(row["uid"])))
            self.table_email_list.setItem(r, 1, QTableWidgetItem(row["od"] or ""))
            self.table_email_list.setItem(r, 2, QTableWidgetItem(row["temat"] or ""))
            self.table_email_list.setItem(r, 3, QTableWidgetItem(row["data"] or ""))
            self.table_email_list.setItem(r, 4, QTableWidgetItem("Tak" if row["przeczytana"] else "Nie"))
            self.table_email_list.setItem(r, 5, QTableWidgetItem(row["pracownik"] or ""))

    # ============================================================
    #  WYBÓR WIADOMOŚCI
    # ============================================================

    def on_email_selected(self, item):
        row = item.row()
        uid = self.table_email_list.item(row, 0).text()
        konto_id = self.email_account_combo.currentData()

        folder_item = self.folder_tree.currentItem()
        if not folder_item:
            return
        folder = folder_item.text(0)

        msg = load_email_message(konto_id, folder, uid)
        if not msg:
            self.email_content.setPlainText("Nie udało się pobrać wiadomości")
            return

        # tu możesz wstawić realny parsing treści
        self.email_content.setPlainText("Treść wiadomości pobrana z IMAP")

        attachments = list_attachments(msg)
        self.current_attachments = {att["filename"]: att["index"] for att in attachments}
        self.current_attachment_meta = {
            "konto_id": konto_id,
            "folder": folder,
            "email_uid": uid
        }

    # ============================================================
    #  MENU KONTEKSTOWE (PPM)
    # ============================================================

    def _show_context_menu(self, pos):
        menu = QMenu(self)

        act_reply = menu.addAction("Odpowiedz")
        act_forward = menu.addAction("Prześlij dalej")
        act_spam = menu.addAction("Oznacz jako niechciane")
        act_delete = menu.addAction("Usuń")
        act_move = menu.addAction("Przenieś do folderu…")
        act_download = menu.addAction("Pobierz załączniki")
        act_source = menu.addAction("Pokaż źródło wiadomości")
        act_mark_read = menu.addAction("Oznacz jako przeczytane")
        act_mark_unread = menu.addAction("Oznacz jako nieprzeczytane")

        action = menu.exec(self.table_email_list.mapToGlobal(pos))
        if not action:
            return

        row = self.table_email_list.currentRow()
        if row < 0:
            return

        uid = self.table_email_list.item(row, 0).text()
        konto_id = self.email_account_combo.currentData()
        folder_item = self.folder_tree.currentItem()
        if not folder_item:
            return
        folder = folder_item.text(0)

        if action == act_reply:
            self._reply_email(uid, konto_id, folder)
        elif action == act_forward:
            self._forward_email(uid, konto_id, folder)
        elif action == act_spam:
            self._mark_as_spam(uid, konto_id, folder)
        elif action == act_delete:
            self.delete_selected_email()
        elif action == act_move:
            self._move_to_folder(uid, konto_id)
        elif action == act_download:
            self._prompt_attachment_download()
        elif action == act_source:
            self._show_source(uid, konto_id, folder)
        elif action == act_mark_read:
            self._mark_read(uid, konto_id, folder)
        elif action == act_mark_unread:
            self._mark_unread(uid, konto_id, folder)

    # ============================================================
    #  AKCJE NA WIADOMOŚCIACH
    # ============================================================

    def _reply_email(self, uid, konto_id, folder):
        QMessageBox.information(self, "Odpowiedz", "Obsługa odpowiedzi do zaimplementowania.")

    def _forward_email(self, uid, konto_id, folder):
        QMessageBox.information(self, "Prześlij dalej", "Obsługa przesyłania dalej do zaimplementowania.")

    def _mark_as_spam(self, uid, konto_id, folder):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE wiadomosci SET folder = 'Spam' WHERE uid = ? AND konto_id = ?", (uid, konto_id))
        conn.commit()
        conn.close()
        self.refresh_email_list(folder_override="Spam")

    def _move_to_folder(self, uid, konto_id):
        folders = []
        root = self.folder_tree.invisibleRootItem()
        for i in range(root.childCount()):
            folders.append(root.child(i).text(0))

        folder, ok = QInputDialog.getItem(self, "Przenieś", "Wybierz folder:", folders, 0, False)
        if not ok:
            return

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE wiadomosci SET folder = ? WHERE uid = ? AND konto_id = ?", (folder, uid, konto_id))
        conn.commit()
        conn.close()

        self.refresh_email_list(folder_override=folder)

    def _show_source(self, uid, konto_id, folder):
        msg = load_email_message(konto_id, folder, uid)
        if not msg:
            QMessageBox.warning(self, "Błąd", "Nie udało się pobrać źródła.")
            return

        raw = msg.as_string()
        dlg = QDialog(self)
        dlg.setWindowTitle("Źródło wiadomości")
        lay = QVBoxLayout(dlg)
        txt = QTextEdit()
        txt.setPlainText(raw)
        lay.addWidget(txt)
        dlg.resize(800, 600)
        dlg.exec()

    def _mark_read(self, uid, konto_id, folder):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE wiadomosci SET przeczytana = 1 WHERE uid = ? AND konto_id = ?", (uid, konto_id))
        conn.commit()
        conn.close()
        self.refresh_email_list(folder_override=folder)

    def _mark_unread(self, uid, konto_id, folder):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE wiadomosci SET przeczytana = 0 WHERE uid = ? AND konto_id = ?", (uid, konto_id))
        conn.commit()
        conn.close()
        self.refresh_email_list(folder_override=folder)

    def move_selected_email(self):
        row = self.table_email_list.currentRow()
        if row < 0:
            return

        uid = self.table_email_list.item(row, 0).text()
        konto_id = self.email_account_combo.currentData()
        folder_item = self.folder_tree.currentItem()
        if not folder_item:
            return
        folder = folder_item.text(0)

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
            self.refresh_email_list(folder_override=folder)

    def delete_selected_email(self):
        row = self.table_email_list.currentRow()
        if row < 0:
            return

        uid = self.table_email_list.item(row, 0).text()
        konto_id = self.email_account_combo.currentData()
        folder_item = self.folder_tree.currentItem()
        if not folder_item:
            return
        folder = folder_item.text(0)

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
            self.refresh_email_list(folder_override=folder)

    # ============================================================
    #  ZAŁĄCZNIKI
    # ============================================================

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

    # ============================================================
    #  SYNC W TLE
    # ============================================================

    def _trigger_email_sync(self):
        runnable = SyncRunnable(self)

        runnable.signals.finished.connect(
            lambda ok: (
                self._load_folders(),
                self.refresh_email_list()
            )
        )
        runnable.signals.error.connect(
            lambda msg: QMessageBox.critical(self, "Błąd synchronizacji", msg)
        )

        self.thread_pool.start(runnable)
