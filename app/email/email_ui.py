# app/email/email_ui.py
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QLabel, QLineEdit, QComboBox, QTableWidget,
    QHeaderView, QPushButton, QTextEdit, QTabWidget,
    QSplitter, QMessageBox, QInputDialog, QTableWidgetItem,
    QTreeWidget, QTreeWidgetItem, QMenu, QDialog
)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt, QThreadPool, QUrl
from PyQt6.QtWebEngineWidgets import QWebEngineView
from app.db.dao_conn import get_conn
from app.email.email_actions import email_actions
from PyQt6.QtCore import QRunnable, QObject, pyqtSignal

class SyncWorkerSignals(QObject):
    finished = pyqtSignal(bool)
    error = pyqtSignal(str)

class SyncRunnable(QRunnable):
    def __init__(self, parent, pracownik_id):
        super().__init__()
        self.parent = parent
        self.pracownik_id = pracownik_id
        self.signals = SyncWorkerSignals()
        self.setAutoDelete(True)

    def run(self):
        try:
            ok = email_actions.sync_all_emails(self.pracownik_id)
            self.signals.finished.emit(ok)
        except Exception as e:
            self.signals.error.emit(str(e))
            self.signals.finished.emit(False)



class EmailPage(QWidget):
    def __init__(self, pracownik_id: int, parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id

        self.filter_konto_combo = None
        self.filter_od = None
        self.filter_temat = None
        self.filter_folder = None

        self.current_attachments = []
        self.current_attachment_meta = None
        self.thread_pool = QThreadPool.globalInstance()
        self._build_ui()

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

    def _create_reader_tab(self):
        tab = QWidget()
        layout = QHBoxLayout(tab)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(8)

        panel_folders = QWidget()
        folders_layout = QVBoxLayout(panel_folders)
        folders_layout.setContentsMargins(0, 0, 0, 0)

        self.folder_tree = QTreeWidget()
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.itemClicked.connect(self._on_folder_selected)
        self.folder_tree.setAcceptDrops(True)
        self.folder_tree.setDragDropMode(QTreeWidget.DragDropMode.DropOnly)
        self.folder_tree.dragEnterEvent = self._folder_drag_enter
        self.folder_tree.dropEvent = self._folder_drop_event

        folders_layout.addWidget(self.folder_tree)
        splitter.addWidget(panel_folders)

        panel_list = QWidget()
        list_layout = QVBoxLayout(panel_list)
        list_layout.setContentsMargins(0, 0, 0, 0)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Konto:"))
        self.email_account_combo = QComboBox()
        self.email_account_combo.currentIndexChanged.connect(self._load_folders)
        toolbar.addWidget(self.email_account_combo)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Szukaj wiadomości…")
        self.search_box.textChanged.connect(self._search_messages)
        toolbar.addWidget(self.search_box)

        btn_refresh = QPushButton("🔄 Odśwież")
        btn_refresh.clicked.connect(self.refresh_email_list)
        toolbar.addWidget(btn_refresh)

        btn_sync = QPushButton("Synchronizuj")
        btn_sync.clicked.connect(self._trigger_email_sync)
        toolbar.addWidget(btn_sync)

        list_layout.addLayout(toolbar)

        self.table_email_list = QTableWidget()
        self.table_email_list.setColumnCount(6)
        self.table_email_list.setHorizontalHeaderLabels(
            ["UID", "Od", "Temat", "Data", "Przeczytana", "Pracownik"]
        )
        self.table_email_list.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_email_list.hideColumn(0)
        self.table_email_list.itemClicked.connect(self.on_email_selected)

        self.table_email_list.setDragEnabled(True)
        self.table_email_list.setAcceptDrops(False)
        self.table_email_list.setDragDropMode(QTableWidget.DragDropMode.DragOnly)

        self.table_email_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table_email_list.customContextMenuRequested.connect(self._show_context_menu)

        list_layout.addWidget(self.table_email_list)
        splitter.addWidget(panel_list)

        panel_content = QWidget()
        content_layout = QVBoxLayout(panel_content)
        content_layout.setContentsMargins(0, 0, 0, 0)

        content_layout.addWidget(QLabel("Treść wiadomości:"))

        self.email_content = QTextEdit()
        self.email_content.setReadOnly(True)
        content_layout.addWidget(self.email_content)

        self.html_viewer = QWebEngineView()
        self.html_viewer.hide()
        content_layout.addWidget(self.html_viewer)

        actions = QHBoxLayout()
        btn_move = QPushButton("Przenieś")
        btn_move.clicked.connect(self.move_selected_email)
        actions.addWidget(btn_move)

        btn_delete = QPushButton("Usuń")
        btn_delete.clicked.connect(self.delete_selected_email)
        actions.addWidget(btn_delete)

        btn_download_all = QPushButton("Pobierz wszystkie załączniki")
        btn_download_all.clicked.connect(self._download_all_attachments)
        actions.addWidget(btn_download_all)

        actions.addStretch()
        content_layout.addLayout(actions)
        splitter.addWidget(panel_content)

        splitter.setSizes([300, 600, 700])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 3)

        layout.addWidget(splitter)

        self._refresh_email_account_combos()
        return tab

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
        btn_add.clicked.connect(self._add_filter_from_ui)
        btn_add.clicked.connect(self._trigger_email_sync)
        right_layout.addWidget(btn_add)

        right_layout.addStretch()

        splitter.addWidget(left)
        splitter.addWidget(right)
        layout.addWidget(splitter)

        self._refresh_email_account_combos()
        self._refresh_email_filters()
        return tab

    def _refresh_email_account_combos(self):
        accounts = email_actions.load_accounts(self.pracownik_id)

        self.email_account_combo.clear()
        if self.filter_konto_combo is not None:
            self.filter_konto_combo.clear()

        for kid, email_addr in accounts:
            self.email_account_combo.addItem(email_addr, kid)
            if self.filter_konto_combo is not None:
                self.filter_konto_combo.addItem(email_addr, kid)

        self._load_folders()

    def _load_folders(self):
        accounts_folders = email_actions.load_all_accounts_folders(self.pracownik_id)
        self.folder_tree.clear()

        for kid, email_addr, folders in accounts_folders:
            root_item = QTreeWidgetItem([email_addr])
            root_item.setForeground(0, Qt.GlobalColor.blue)
            root_item.setFont(0, QFont("Arial", 11, QFont.Weight.Bold))

            for f in folders:
                child = QTreeWidgetItem([f])
                child.setData(0, Qt.ItemDataRole.UserRole, kid)
                root_item.addChild(child)

            self.folder_tree.addTopLevelItem(root_item)

            separator = QTreeWidgetItem([" "])
            separator.setDisabled(True)
            self.folder_tree.addTopLevelItem(separator)

    def _on_folder_selected(self, item):
        folder = item.text(0)
        konto_id = item.data(0, Qt.ItemDataRole.UserRole)
        if konto_id is None:
            konto_id = self.email_account_combo.currentData()
        self.refresh_email_list(folder_override=folder, konto_override=konto_id)

    def refresh_email_list(self, folder_override=None, konto_override=None):
        konto_id = konto_override or self.email_account_combo.currentData()
        folder_item = self.folder_tree.currentItem()
        folder = folder_override or (folder_item.text(0) if folder_item else "")

        if not konto_id or not folder:
            self.table_email_list.setRowCount(0)
            return

        rows = email_actions.refresh_email_list(konto_id, folder)
        self._populate_table(rows)

    def _search_messages(self):
        query = self.search_box.text().strip().lower()
        if not query:
            self.refresh_email_list()
            return

        folder_item = self.folder_tree.currentItem()
        if not folder_item:
            return

        folder = folder_item.text(0)
        konto_id = folder_item.data(0, Qt.ItemDataRole.UserRole)
        if konto_id is None:
            konto_id = self.email_account_combo.currentData()

        rows = email_actions.search_messages(konto_id, folder, query)
        self._populate_table(rows)

    def _populate_table(self, rows):
        self.table_email_list.setRowCount(0)
        for r, row in enumerate(rows):
            self.table_email_list.insertRow(r)
            self.table_email_list.setItem(r, 0, QTableWidgetItem(str(row["uid"])))
            self.table_email_list.setItem(r, 1, QTableWidgetItem(row["od"] or ""))
            self.table_email_list.setItem(r, 2, QTableWidgetItem(row["temat"] or ""))
            self.table_email_list.setItem(r, 3, QTableWidgetItem(row["data"] or ""))
            self.table_email_list.setItem(r, 4, QTableWidgetItem("Tak" if row["przeczytana"] else "Nie"))
            self.table_email_list.setItem(r, 5, QTableWidgetItem(row["pracownik"] or ""))

    def on_email_selected(self, item):
        row = item.row()
        uid = self.table_email_list.item(row, 0).text()

        folder_item = self.folder_tree.currentItem()
        if not folder_item:
            return
        folder = folder_item.text(0)
        konto_id = folder_item.data(0, Qt.ItemDataRole.UserRole)
        if konto_id is None:
            konto_id = self.email_account_combo.currentData()

        msg = email_actions.load_message(konto_id, folder, uid)
        if not msg:
            self.email_content.setPlainText("Nie udało się pobrać wiadomości")
            self.html_viewer.hide()
            self.email_content.show()
            return

        html = email_actions.extract_html(msg)
        if html:
            self.email_content.hide()
            self.html_viewer.show()
            self.html_viewer.setHtml(html)
        else:
            self.html_viewer.hide()
            self.email_content.show()
            self.email_content.setPlainText("Treść wiadomości pobrana z IMAP")

        attachments = email_actions.get_attachments(msg)
        self.current_attachments = attachments
        self.current_attachment_meta = {
            "konto_id": konto_id,
            "folder": folder,
            "email_uid": uid
        }

    def _show_context_menu(self, pos):
        menu = QMenu(self)

        act_open_att = menu.addAction("Otwórz załącznik")
        act_download_all = menu.addAction("Pobierz wszystkie załączniki")
        act_source = menu.addAction("Pokaż źródło wiadomości")
        act_mark_read = menu.addAction("Oznacz jako przeczytane")
        act_mark_unread = menu.addAction("Oznacz jako nieprzeczytane")
        act_spam = menu.addAction("Oznacz jako niechciane")
        act_delete = menu.addAction("Usuń")
        act_move = menu.addAction("Przenieś do folderu…")

        action = menu.exec(self.table_email_list.mapToGlobal(pos))
        if not action:
            return

        row = self.table_email_list.currentRow()
        if row < 0:
            return

        uid = self.table_email_list.item(row, 0).text()
        folder_item = self.folder_tree.currentItem()
        if not folder_item:
            return
        folder = folder_item.text(0)
        konto_id = folder_item.data(0, Qt.ItemDataRole.UserRole)
        if konto_id is None:
            konto_id = self.email_account_combo.currentData()

        if action == act_open_att:
            self._open_attachment()
        elif action == act_download_all:
            self._download_all_attachments()
        elif action == act_spam:
            email_actions.mark_spam(konto_id, uid)
            self.refresh_email_list(folder_override="Spam", konto_override=konto_id)
        elif action == act_delete:
            self.delete_selected_email()
        elif action == act_move:
            self.move_selected_email()
        elif action == act_source:
            self._show_source()
        elif action == act_mark_read:
            email_actions.mark_read(konto_id, uid)
            self.refresh_email_list(folder_override=folder, konto_override=konto_id)
        elif action == act_mark_unread:
            email_actions.mark_unread(konto_id, uid)
            self.refresh_email_list(folder_override=folder, konto_override=konto_id)

    def _open_attachment(self):
        if not self.current_attachments:
            return

        items = [att["filename"] for att in self.current_attachments]
        filename, ok = QInputDialog.getItem(self, "Załączniki", "Wybierz:", items, 0, False)
        if not ok:
            return

        att = next(a for a in self.current_attachments if a["filename"] == filename)

        pdf_path = email_actions.open_attachment(
            self.current_attachment_meta["konto_id"],
            self.current_attachment_meta["folder"],
            self.current_attachment_meta["email_uid"],
            att["index"]
        )

        if not pdf_path:
            QMessageBox.warning(self, "Błąd", "Nie udało się otworzyć załącznika.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(filename)
        dlg.resize(900, 700)

        layout = QVBoxLayout(dlg)
        viewer = QWebEngineView()
        viewer.load(QUrl.fromLocalFile(pdf_path))
        layout.addWidget(viewer)

        dlg.exec()

    def _download_all_attachments(self):
        if not self.current_attachments:
            return

        email_actions.download_all_attachments(
            self.current_attachment_meta["konto_id"],
            self.current_attachment_meta["folder"],
            self.current_attachment_meta["email_uid"],
            self.current_attachments
        )

    def move_selected_email(self):
        row = self.table_email_list.currentRow()
        if row < 0:
            return

        uid = self.table_email_list.item(row, 0).text()
        folder_item = self.folder_tree.currentItem()
        if not folder_item:
            return
        current_folder = folder_item.text(0)
        konto_id = folder_item.data(0, Qt.ItemDataRole.UserRole)
        if konto_id is None:
            konto_id = self.email_account_combo.currentData()

        folders = []
        root = self.folder_tree.invisibleRootItem()
        for i in range(root.childCount()):
            item = root.child(i)
            if item.childCount() == 0:
                continue
            for j in range(item.childCount()):
                folders.append(item.child(j).text(0))

        folder, ok = QInputDialog.getItem(self, "Przenieś", "Wybierz folder:", folders, 0, False)
        if not ok:
            return

        ok, sender = email_actions.move_email_and_get_sender(konto_id, uid, folder)
        if not ok:
            QMessageBox.warning(self, "Błąd", "Nie udało się przenieść wiadomości.")
            return

        if sender:
            self._ask_add_filter(konto_id, sender, folder)

        self.refresh_email_list(folder_override=folder, konto_override=konto_id)

    def delete_selected_email(self):
        row = self.table_email_list.currentRow()
        if row < 0:
            return

        uid = self.table_email_list.item(row, 0).text()
        folder_item = self.folder_tree.currentItem()
        if not folder_item:
            return
        konto_id = folder_item.data(0, Qt.ItemDataRole.UserRole)
        if konto_id is None:
            konto_id = self.email_account_combo.currentData()

        email_actions.delete_email(konto_id, uid)
        self.refresh_email_list()

    def _show_source(self):
        meta = self.current_attachment_meta
        if not meta:
            return

        raw = email_actions.load_message(
            meta["konto_id"],
            meta["folder"],
            meta["email_uid"]
        )
        if not raw:
            QMessageBox.warning(self, "Błąd", "Nie udało się pobrać źródła.")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("Źródło wiadomości")
        lay = QVBoxLayout(dlg)
        txt = QTextEdit()
        txt.setPlainText(raw.as_string())
        lay.addWidget(txt)
        dlg.resize(800, 600)
        dlg.exec()

    def _folder_drag_enter(self, event):
        event.accept()

    def _folder_drop_event(self, event):
        item = self.folder_tree.itemAt(event.position().toPoint())
        if not item:
            return

        target_folder = item.text(0)
        row = self.table_email_list.currentRow()
        if row < 0:
            return

        uid = self.table_email_list.item(row, 0).text()
        konto_id = item.data(0, Qt.ItemDataRole.UserRole)
        if konto_id is None:
            konto_id = self.email_account_combo.currentData()

        ok, sender = email_actions.move_email_and_get_sender(konto_id, uid, target_folder)
        if not ok:
            QMessageBox.warning(self, "Błąd", "Nie udało się przenieść wiadomości.")
            return

        if sender:
            self._ask_add_filter(konto_id, sender, target_folder)

        self.refresh_email_list(folder_override=target_folder, konto_override=konto_id)

    def _ask_add_filter(self, konto_id, sender, folder):
        reply = QMessageBox.question(
            self,
            "Dodaj filtr?",
            f"Czy chcesz dodać filtr, aby wiadomości od:\n\n{sender}\n\nzawsze trafiały do folderu:\n{folder}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            email_actions.add_filter(konto_id, sender, folder)
            self._refresh_email_filters()

    def _add_filter_from_ui(self):
        konto_id = self.filter_konto_combo.currentData()
        sender = self.filter_od.text().strip() or None
        folder = self.filter_folder.text().strip()

        if not konto_id or not folder:
            QMessageBox.warning(self, "Błąd", "Wybierz konto i folder docelowy")
            return

        email_actions.add_filter(konto_id, sender or "", folder)
        QMessageBox.information(self, "Sukces", "Filtr został dodany")

        self.filter_od.clear()
        self.filter_temat.clear()
        self.filter_folder.clear()
        self._refresh_email_filters()

    def _refresh_email_filters(self):
        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT rf.id, rf.od, rf.temat_zawiera, rf.folder_docelowy
                FROM reguly_filtrow rf
                JOIN konta_email ke ON rf.konto_id = ke.id
                JOIN pracownik_konto_email pke ON pke.konto_id = ke.id
                WHERE pke.pracownik_id = ?
            """, (self.pracownik_id,))
            rows = cur.fetchall()
        finally:
            conn.close()

        self.table_email_filters.setRowCount(0)
        for r, row in enumerate(rows):
            fid, od, temat, folder = row
            self.table_email_filters.insertRow(r)
            self.table_email_filters.setItem(r, 0, QTableWidgetItem(str(fid)))
            self.table_email_filters.setItem(r, 1, QTableWidgetItem(od or "Dowolny"))
            self.table_email_filters.setItem(r, 2, QTableWidgetItem(temat or "Dowolny"))
            self.table_email_filters.setItem(r, 3, QTableWidgetItem(folder or "N/A"))

            btn_delete = QPushButton("Usuń")
            btn_delete.clicked.connect(lambda checked, f=fid: self._delete_filter(f))
            self.table_email_filters.setCellWidget(r, 4, btn_delete)

    def _delete_filter(self, filter_id):
        reply = QMessageBox.question(
            self,
            "Potwierdzenie",
            "Usunąć ten filtr?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        conn = get_conn()
        cur = conn.cursor()
        try:
            cur.execute("DELETE FROM reguly_filtrow WHERE id = ?", (filter_id,))
            conn.commit()
        finally:
            conn.close()

        self._refresh_email_filters()

    def _trigger_email_sync(self):
        runnable = SyncRunnable(self)
        runnable.signals.finished.connect(lambda ok: self.refresh_email_list())
        runnable.signals.error.connect(lambda msg: QMessageBox.critical(self, "Błąd synchronizacji", msg))
        self.thread_pool.start(runnable)
