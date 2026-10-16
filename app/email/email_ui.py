# app/email/email_page.py
from __future__ import annotations

import os
import re
import email
import tempfile
import threading
import unicodedata
from datetime import datetime
from collections import Counter
from email.utils import parseaddr
from html import escape as html_escape
from typing import List, Dict, Any, Optional, Tuple

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QTreeWidget, QTreeWidgetItem, QMessageBox, QSplitter, QPushButton,
    QLabel, QFrame, QSizePolicy, QDialog, QDialogButtonBox, QTextEdit,
    QToolButton, QStyle, QLineEdit, QApplication, QTextBrowser, QMenu,
    QHeaderView, QAbstractItemView, QFileDialog, QProgressBar
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QImage

from app.db.dao_employees import list_employees
from app.email.compose_dialog import ComposeDialog
from app.email.email_connection import check_imap
from app.email.email_storage import (
    find_special_folder,
    get_account_info,
    get_email_accounts_for_employee,
    get_folder_id,
    get_folders_for_account,
    get_unread_counts,
    refresh_email_list,
    search_messages,
)
from app.email.email_sync import (
    list_folders_from_imap_and_store,
    mark_read_remote,
    delete_email_remote,
    mark_unread_remote,
    move_email_and_get_sender,
    needs_initial_sync,
    sync_and_prefetch,
)

from app.email.email_actions import (
    send_email_smtp,
    load_message,
    open_attachment,
)
from app.email.email_helpers import (
    SPECIAL_FOLDER_LABELS,
    SPECIAL_FOLDER_ORDER,
    decode_mailbox_utf7,
    extract_html,
    extract_text,
    folder_role,
    get_attachments,
    inline_resources,
    iter_message_attachments,
)
import logging

logger = logging.getLogger(__name__)

AUTO_SYNC_INTERVAL_MS = 120_000
ROLE_DATA = Qt.ItemDataRole.UserRole
ROLE_ACCOUNT = Qt.ItemDataRole.UserRole + 2
_SUBJECT_PREFIX = re.compile(r"^\s*(re|odp|fw|fwd|pd|wg|aw|przekaż)\s*:\s*", re.IGNORECASE)


def thread_key(subject: str) -> str:
    """Temat bez prefiksów Re:/Odp:/Fw: - wiadomości o tym samym temacie tworzą wątek."""
    text = subject or ""
    while True:
        stripped = _SUBJECT_PREFIX.sub("", text)
        if stripped == text:
            break
        text = stripped
    return " ".join(text.split()).casefold()


def _fold(text: str) -> str:
    text = text.replace("ł", "l").replace("Ł", "L")
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


def clean_folder_label(label: str) -> str:
    """Usuwa numer porządkowy ("3 Pracownicy") i prefiks "EM-KA " z nazwy folderu."""
    label = re.sub(r"^\d+\s+", "", label.strip())
    label = re.sub(r"^EM-KA\s+", "", label, flags=re.IGNORECASE)
    return label.strip()


def employee_label(label: str, employees: List[Dict[str, Any]]) -> str:
    """Zamienia np. "Sokolowski Paweł" na "Paweł Sokołowski" (niezależnie od kolejności i ogonków)."""
    key = sorted(_fold(label).split())
    for emp in employees:
        full = f"{emp.get('imie') or ''} {emp.get('nazwisko') or ''}".strip()
        if full and sorted(_fold(full).split()) == key:
            return full
    return label


def format_list_date(value: str) -> str:
    """'2026-10-15 08:00' -> '15.10.2026, 08:00'."""
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M").strftime("%d.%m.%Y, %H:%M")
    except (TypeError, ValueError):
        return value or ""


def build_folder_tree(folders: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Buduje drzewo folderów wg prawdziwego separatora serwera (INBOX jako główny folder).

    Zwraca listę korzeni; węzeł: {path, label, folder (dict|None), role, children}.
    """
    nodes: Dict[str, Dict[str, Any]] = {}
    roots: List[Dict[str, Any]] = []

    def ensure(path: str, delimiter: Optional[str], folder: Optional[Dict[str, Any]]):
        node = nodes.get(path)
        if node is None:
            leaf = path.split(delimiter)[-1] if delimiter else path
            node = {
                "path": path,
                "label": decode_mailbox_utf7(leaf),
                "folder": None,
                "role": None,
                "children": [],
            }
            nodes[path] = node
            if delimiter and delimiter in path:
                parent = ensure(path.rsplit(delimiter, 1)[0], delimiter, None)
                parent["children"].append(node)
            else:
                roots.append(node)
        if folder is not None:
            node["folder"] = folder
            node["role"] = folder_role(
                folder["server_name"], folder.get("attributes"), folder.get("delimiter")
            )
        return node

    for folder in sorted(folders, key=lambda f: f["server_name"]):
        ensure(folder["server_name"], folder.get("delimiter"), folder)

    def sort_key(node: Dict[str, Any]):
        role = node["role"]
        if role in SPECIAL_FOLDER_ORDER:
            return (0, SPECIAL_FOLDER_ORDER.index(role), "")
        label = node["label"].casefold()
        # foldery numerowane ("1 Firmy", "3 Pracownicy") po kolei, jak na serwerze
        return (1, 0, [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"(\d+)", label)])

    def sort_tree(items: List[Dict[str, Any]]) -> None:
        items.sort(key=sort_key)
        for n in items:
            sort_tree(n["children"])

    sort_tree(roots)
    # INBOX zawsze pierwszy
    roots.sort(key=lambda n: 0 if n["role"] == "inbox" else 1)
    return roots


class MailBrowser(QTextBrowser):
    """Podgląd wiadomości z obsługą obrazków wbudowanych (cid:)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.resources: Dict[str, Any] = {}

    def loadResource(self, rtype, url):
        if url.scheme() == "cid":
            entry = self.resources.get(url.path() or url.toString()[4:])
            if entry:
                image = QImage.fromData(entry[0])
                if not image.isNull():
                    return image
            return None
        return super().loadResource(rtype, url)


class EmailPage(QWidget):
    sync_finished = pyqtSignal(bool)
    headers_ready = pyqtSignal()
    sync_progress = pyqtSignal(object)

    def __init__(self, pracownik_id: int):
        super().__init__()
        self.pracownik_id = pracownik_id
        self.accounts = get_email_accounts_for_employee(pracownik_id)
        self.current_account: Optional[Dict[str, Any]] = None
        self.current_folder_local: Optional[str] = None
        self.current_uid: Optional[str] = None
        self.cached_message: Optional[email.message.Message] = None
        self.unread_map: Dict[str, bool] = {}
        self._sync_running = False
        self._resync_pending = False
        self._synced_accounts: set = set()
        self._tree_signature = None

        self._build_ui()
        self.sync_finished.connect(self._on_sync_finished)
        self.headers_ready.connect(self._on_headers_ready)
        self.sync_progress.connect(self._on_sync_progress)

        if self.accounts:
            self.current_account = self.accounts[0]
            self.load_folders_from_db()
            self.start_background_sync()
            self._timer = QTimer(self)
            self._timer.timeout.connect(self.start_background_sync)
            self._timer.start(AUTO_SYNC_INTERVAL_MS)
        else:
            QMessageBox.warning(
                self,
                "Brak kont",
                "Nie masz przypisanego żadnego konta e-mail.\n"
                "Administrator może je przypisać w Ustawienia → Konta e-mail → "
                "Dostęp pracowników.",
            )

    # ------------------------------------------------------------------
    # Automatyczna synchronizacja w tle
    # ------------------------------------------------------------------
    def start_background_sync(self) -> None:
        if not self.current_account:
            return
        if self._sync_running:
            self._resync_pending = True
            return
        self._sync_running = True
        self._resync_pending = False
        jobs = []
        for account in self.accounts:
            folders = ["INBOX"]
            if account["id"] == self.current_account["id"] and self.current_folder_local \
                    and self.current_folder_local not in folders:
                folders.insert(0, self.current_folder_local)
            first = account["id"] not in self._synced_accounts
            self._synced_accounts.add(account["id"])
            initial = first and needs_initial_sync(account["id"])
            jobs.append((account["id"], folders, first, initial))
        jobs.sort(key=lambda j: j[0] != self.current_account["id"])
        labels = {a["id"]: a.get("email", "") for a in self.accounts}

        def work() -> None:
            ok = True
            for konto_id, folders, first, initial in jobs:
                try:
                    on_progress = None
                    if initial:
                        label = labels.get(konto_id, "")
                        on_progress = lambda d, t, f, n, label=label: self.sync_progress.emit(
                            (label, d, t, f, n)
                        )
                    sync_and_prefetch(
                        konto_id, folders, first_run=first,
                        on_headers=lambda: self.headers_ready.emit(),
                        on_progress=on_progress, all_folders=initial,
                    )
                except Exception:
                    logger.warning("Synchronizacja konta %s nie powiodła się", konto_id, exc_info=True)
                    ok = False
            try:
                self.sync_finished.emit(ok)
            except RuntimeError:
                pass  # widżet został już zamknięty

        threading.Thread(target=work, daemon=True).start()

    def _on_sync_progress(self, data) -> None:
        label, done, total, folder, headers = data
        self.sync_progress_frame.show()
        self.sync_progress_bar.setMaximum(max(total, 1))
        self.sync_progress_bar.setValue(done)
        where = f" – {clean_folder_label(decode_mailbox_utf7(folder))}" if folder else ""
        self.sync_progress_label.setText(
            f"Pierwsze pobieranie nagłówków {label}: foldery {done}/{total}, "
            f"pobrano {headers} nagłówków{where}"
        )

    def _on_headers_ready(self) -> None:
        if not self.current_account:
            return
        self.load_folders_from_db()
        if self.current_folder_local and self.current_uid is None:
            self.load_mail_list_from_db(self.current_folder_local, keep_view=True)

    def _on_sync_finished(self, ok: bool) -> None:
        self._sync_running = False
        self.sync_progress_frame.hide()
        if ok:
            self._on_headers_ready()
        if self._resync_pending:
            self.start_background_sync()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)

        # Toolbar 1
        toolbar = QHBoxLayout()
        btn_sync = QPushButton("Synchronizacja wiadomości")
        btn_sync.clicked.connect(self.on_manual_sync)
        toolbar.addWidget(btn_sync)

        btn_refresh = QPushButton("Odśwież widok")
        btn_refresh.clicked.connect(self.refresh_all)
        toolbar.addWidget(btn_refresh)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Szukaj w temacie, nadawcy lub odbiorcy...")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(
            lambda: self.load_mail_list_from_db(self.current_folder_local)
            if self.current_folder_local else None
        )
        toolbar.addWidget(self.search_input, 2)

        toolbar.addStretch()
        main_layout.addLayout(toolbar)

        # Message actions, arranged like a mail client toolbar.
        mail_toolbar = QHBoxLayout()

        self.btn_new = self._create_mail_action(
            "Nowa wiadomość", QStyle.StandardPixmap.SP_FileIcon, self.on_new_message
        )
        self.btn_reply = self._create_mail_action(
            "Odpowiedz", QStyle.StandardPixmap.SP_ArrowBack, self.on_reply
        )
        self.btn_forward = self._create_mail_action(
            "Przekaż", QStyle.StandardPixmap.SP_ArrowForward, self.on_forward
        )
        self.btn_archive = self._create_mail_action(
            "Archiwizuj", QStyle.StandardPixmap.SP_DriveHDIcon, self.on_archive
        )
        self.btn_spam = self._create_mail_action(
            "Niechciane", QStyle.StandardPixmap.SP_MessageBoxWarning, self.on_spam
        )
        self.btn_delete = self._create_mail_action(
            "Usuń", QStyle.StandardPixmap.SP_TrashIcon, self.on_delete
        )
        self.btn_more = self._create_mail_action(
            "Więcej", QStyle.StandardPixmap.SP_TitleBarUnshadeButton, self.on_more
        )

        for button in (
            self.btn_new,
            self.btn_reply,
            self.btn_forward,
            self.btn_archive,
            self.btn_spam,
            self.btn_delete,
            self.btn_more,
        ):
            mail_toolbar.addWidget(button)

        # Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # Folder tree
        self.folder_tree = QTreeWidget()
        self.folder_tree.setHeaderLabel("Foldery")
        self.folder_tree.setIndentation(16)
        self.folder_tree.setMinimumWidth(240)
        self.folder_tree.header().setStretchLastSection(False)
        self.folder_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.currentItemChanged.connect(self._on_folder_current_changed)
        splitter.addWidget(self.folder_tree)

        # Middle panel
        right_container = QFrame()
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_container.setLayout(right_layout)

        self.mail_list = QTreeWidget()
        self.mail_list.setColumnCount(3)
        self.mail_list.setHeaderLabels(["Temat", "Korespondent", "Data"])
        self.mail_list.setRootIsDecorated(True)
        self.mail_list.setUniformRowHeights(True)
        self.mail_list.setAlternatingRowColors(True)
        self.mail_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.mail_list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.mail_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.mail_list.setStyleSheet("QTreeWidget::item { padding: 4px 6px; }")
        hdr = self.mail_list.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        self.mail_list.setColumnWidth(1, 220)
        self.mail_list.setColumnWidth(2, 140)
        self.mail_list.currentItemChanged.connect(self._on_mail_current_changed)
        self.mail_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.mail_list.customContextMenuRequested.connect(self._on_mail_context_menu)
        right_layout.addWidget(self.mail_list)

        splitter.addWidget(right_container)

        # Message content pane
        message_container = QFrame()
        message_layout = QVBoxLayout(message_container)
        message_layout.setContentsMargins(0, 0, 0, 0)
        message_layout.addLayout(mail_toolbar)

        self.mail_view = MailBrowser()
        self.mail_view.setOpenExternalLinks(True)
        self.mail_view.setPlaceholderText("Wybierz wiadomość z listy.")
        message_layout.addWidget(self.mail_view, 1)

        self.att_list = QListWidget()
        self.att_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.att_list.itemDoubleClicked.connect(self.on_attachment_item_activated)
        self.att_list.hide()
        message_layout.addWidget(self.att_list)

        self.att_bar = QFrame()
        self.att_bar.setFrameShape(QFrame.Shape.StyledPanel)
        bar = QHBoxLayout(self.att_bar)
        bar.setContentsMargins(6, 3, 6, 3)
        self.att_toggle = QToolButton()
        self.att_toggle.setText(">")
        self.att_toggle.setAutoRaise(True)
        self.att_toggle.clicked.connect(self._toggle_att_list)
        bar.addWidget(self.att_toggle)
        self.att_summary = QLabel("")
        bar.addWidget(self.att_summary, 1)
        self.att_save = QPushButton("Zapisz")
        self.att_save.setToolTip("Zapisz załączniki (zaznaczone lub wszystkie)")
        self.att_save.clicked.connect(self.on_save_attachments_clicked)
        bar.addWidget(self.att_save)
        self.att_bar.hide()
        message_layout.addWidget(self.att_bar)
        splitter.addWidget(message_container)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 3)
        splitter.setSizes([280, 480, 760])

        self.sync_progress_frame = QFrame()
        self.sync_progress_frame.setFrameShape(QFrame.Shape.StyledPanel)
        progress_row = QHBoxLayout(self.sync_progress_frame)
        progress_row.setContentsMargins(6, 3, 6, 3)
        self.sync_progress_label = QLabel("")
        progress_row.addWidget(self.sync_progress_label, 1)
        self.sync_progress_bar = QProgressBar()
        self.sync_progress_bar.setMinimumWidth(240)
        progress_row.addWidget(self.sync_progress_bar)
        self.sync_progress_frame.hide()
        main_layout.addWidget(self.sync_progress_frame)

    def _create_mail_action(self, text: str, icon_name, callback) -> QToolButton:
        button = QToolButton(self)
        button.setText(text)
        button.setIcon(self.style().standardIcon(icon_name))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setAutoRaise(True)
        button.setToolTip(text)
        button.clicked.connect(callback)
        return button
    def load_folders_from_db(self) -> None:
        if not self.accounts:
            self.folder_tree.clear()
            return

        data = []
        for account in self.accounts:
            konto_id = account["id"]
            try:
                folders = get_folders_for_account(konto_id)
                unread = get_unread_counts(konto_id)
            except Exception:
                folders, unread = [], {}
            if not any(f["server_name"].upper() == "INBOX" for f in folders):
                folders = folders + [{"server_name": "INBOX", "display_name": "INBOX",
                                      "delimiter": None, "attributes": "", "id": None}]
            data.append((account, folders, unread))

        current_id = self.current_account["id"] if self.current_account else None
        signature = (
            current_id,
            self.current_folder_local,
            tuple(
                (acc["id"],
                 tuple((f.get("id"), f["server_name"], f.get("attributes")) for f in folders),
                 tuple(sorted(unread.items())))
                for acc, folders, unread in data
            ),
        )
        if signature == self._tree_signature:
            return
        self._tree_signature = signature

        try:
            employees = list_employees()
        except Exception:
            employees = []

        self.folder_tree.blockSignals(True)
        self.folder_tree.clear()
        selected_item: Optional[QTreeWidgetItem] = None

        def add(account_id: int, unread: Dict[Any, int], node: Dict[str, Any],
                parent: QTreeWidgetItem, flatten: bool = False,
                under_staff: bool = False) -> QTreeWidgetItem:
            nonlocal selected_item
            role = node["role"]
            label = SPECIAL_FOLDER_LABELS.get(role) if role else None
            if label is None:
                label = clean_folder_label(node["label"])
                if under_staff:
                    label = employee_label(label, employees)
            folder = node["folder"]
            count = unread.get(folder["id"], 0) if folder and folder.get("id") else 0
            item = QTreeWidgetItem([f"{label} ({count})" if count else label])
            selectable = folder is not None and "\\noselect" not in (folder.get("attributes") or "").lower()
            if selectable:
                item.setData(0, ROLE_DATA, node["path"])
                item.setData(0, ROLE_ACCOUNT, account_id)
                item.setToolTip(0, decode_mailbox_utf7(node["path"]))
            else:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            if count:
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)
            parent.addChild(item)
            if selectable and account_id == current_id and node["path"] == self.current_folder_local:
                selected_item = item
            if not flatten:
                staff = clean_folder_label(node["label"]).casefold() == "pracownicy"
                for child in node["children"]:
                    add(account_id, unread, child, item, under_staff=staff)
            return item

        for account, folders, unread in data:
            account_item = QTreeWidgetItem([account.get("email", "")])
            account_item.setFlags(account_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            font = account_item.font(0)
            font.setBold(True)
            account_item.setFont(0, font)
            self.folder_tree.addTopLevelItem(account_item)
            account_item.setExpanded(True)
            for root in build_folder_tree(folders):
                if root["role"] == "inbox":
                    # "Odebrane" i podfoldery INBOX na jednym poziomie pod kontem
                    add(account["id"], unread, root, account_item, flatten=True)
                    for child in root["children"]:
                        add(account["id"], unread, child, account_item)
                else:
                    add(account["id"], unread, root, account_item)

        if selected_item is not None:
            self.folder_tree.setCurrentItem(selected_item)
            parent = selected_item.parent()
            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()
        self.folder_tree.blockSignals(False)

    def _on_folder_current_changed(self, current, previous) -> None:
        if current is not None:
            self.on_folder_clicked(current, 0)

    def on_folder_clicked(self, item: QTreeWidgetItem, column: int = 0) -> None:
        server_name = item.data(0, ROLE_DATA)
        if not server_name:
            return
        account_id = item.data(0, ROLE_ACCOUNT)
        for account in self.accounts:
            if account["id"] == account_id:
                self.current_account = account
                break
        self.current_folder_local = server_name
        self.current_uid = None
        self.load_mail_list_from_db(server_name)
        self.start_background_sync()

    def _style_mail_item(self, item: QTreeWidgetItem, unread: bool) -> None:
        for col in range(item.columnCount()):
            font = item.font(col)
            font.setBold(bool(unread))
            item.setFont(col, font)

    def load_mail_list_from_db(self, local_folder: str, keep_view: bool = False) -> None:
        selected_uid = self.current_uid if keep_view else None
        self.mail_list.blockSignals(True)
        self.mail_list.clear()
        if not keep_view:
            self._clear_view()
            self.cached_message = None
        self.unread_map.clear()

        if not self.current_account:
            self.mail_list.blockSignals(False)
            return

        konto_id = self.current_account["id"]

        try:
            rows = refresh_email_list(konto_id, local_folder)
        except Exception:
            rows = []

        query = self.search_input.text().strip().casefold()
        if query:
            rows = [
                row for row in rows
                if query in " ".join(
                    str(row.get(field) or "")
                    for field in ("temat", "nadawca", "od", "do")
                ).casefold()
            ]

        restore: Optional[QTreeWidgetItem] = None
        threads: Dict[Any, List[QTreeWidgetItem]] = {}
        for r in rows:
            uid = r.get("uid")
            is_unread = not bool(r.get("przeczytana"))
            self.unread_map[uid] = is_unread
            item = QTreeWidgetItem([
                r.get("temat") or "(brak tematu)",
                r.get("nadawca") or "",
                format_list_date(r.get("data") or ""),
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, uid)
            item.setData(0, Qt.ItemDataRole.UserRole + 1, local_folder)
            self._style_mail_item(item, is_unread)
            key = thread_key(r.get("temat") or "") or ("uid", uid)
            threads.setdefault(key, []).append(item)
            if selected_uid is not None and uid == selected_uid:
                restore = item

        for members in threads.values():
            head = members[0]
            for child in members[1:]:
                head.addChild(child)
            if len(members) > 1:
                head.setText(0, f"{head.text(0)}  [{len(members)}]")
                if any(self.unread_map.get(m.data(0, Qt.ItemDataRole.UserRole)) for m in members):
                    self._style_mail_item(head, True)
            self.mail_list.addTopLevelItem(head)
            if restore is not None and restore.parent() is head:
                head.setExpanded(True)
        if restore is not None:
            self.mail_list.setCurrentItem(restore)
        self.mail_list.blockSignals(False)

    def _on_mail_current_changed(self, current, previous) -> None:
        if current is not None:
            self.on_mail_clicked(current)

    def _on_mail_context_menu(self, pos) -> None:
        item = self.mail_list.itemAt(pos)
        if item is None:
            return
        uid = item.data(0, Qt.ItemDataRole.UserRole)
        folder = item.data(0, Qt.ItemDataRole.UserRole + 1)
        unread = self.unread_map.get(uid, False)
        menu = QMenu(self)
        act = menu.addAction("Oznacz jako przeczytane" if unread else "Oznacz jako nieprzeczytane")
        if menu.exec(self.mail_list.viewport().mapToGlobal(pos)) is act:
            self.on_toggle_read(uid, item, folder)

    def on_toggle_read(self, uid: str, item: QTreeWidgetItem, local_folder: str) -> None:
        konto_id = self.current_account["id"]
        is_unread = self.unread_map.get(uid, False)

        try:
            if is_unread:
                ok = mark_read_remote(konto_id, local_folder, uid)
            else:
                ok = mark_unread_remote(konto_id, local_folder, uid)
        except Exception:
            ok = False

        if ok:
            self.unread_map[uid] = not is_unread
            self._style_mail_item(item, not is_unread)
            self.load_folders_from_db()
            self.load_mail_list_from_db(local_folder, keep_view=True)

    def on_mail_clicked(self, item: QTreeWidgetItem, column: int = 0) -> None:
        uid = item.data(0, Qt.ItemDataRole.UserRole)
        local_folder = item.data(0, Qt.ItemDataRole.UserRole + 1)

        if not uid or not local_folder or not self.current_account:
            return

        self.current_uid = uid
        self.current_folder_local = local_folder
        self.cached_message = None
        konto_id = self.current_account["id"]

        # treść jest pobierana automatycznie i zapisywana w cache (ostatnie 300 wiadomości)
        self._clear_view()
        self.mail_view.setPlainText("Pobieranie wiadomości…")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            msg = load_message(konto_id, local_folder, uid)
        except Exception:
            logger.warning("Nie można pobrać wiadomości", exc_info=True)
            msg = None
        finally:
            QApplication.restoreOverrideCursor()

        if not msg:
            self.mail_view.setPlainText(
                "Nie udało się pobrać treści wiadomości z serwera.\n"
                "Sprawdź połączenie (Ustawienia → Konta e-mail → Testuj) i kliknij wiadomość ponownie."
            )
            return

        self._render_message(msg)

        if self.unread_map.get(uid, False):
            try:
                mark_read_remote(konto_id, local_folder, uid)
            except Exception:
                logger.warning("Zignorowany błąd", exc_info=True)
            self.unread_map[uid] = False
            self._style_mail_item(item, False)
            self.load_folders_from_db()

    def _render_message(self, msg: email.message.Message) -> None:
        self.cached_message = msg
        header = (
            f"<b>{html_escape(str(msg.get('Subject') or '(brak tematu)'))}</b><br>"
            f"Od: {html_escape(str(msg.get('From') or ''))}<br>"
            f"Do: {html_escape(str(msg.get('To') or ''))}<br>"
            f"Data: {html_escape(str(msg.get('Date') or ''))}<hr>"
        )
        html = extract_html(msg)
        self.mail_view.resources = inline_resources(msg)

        if html:
            self.mail_view.setHtml(header + html)
        else:
            self.mail_view.setHtml(
                header + "<pre style='white-space: pre-wrap;'>"
                + html_escape(extract_text(msg)) + "</pre>"
            )

        html_lower = (html or "").lower()
        shown = [
            att for att in get_attachments(msg)
            if not (att.get("content_id") and f"cid:{att['content_id'].lower()}" in html_lower)
        ]
        self.show_attachments(shown)

    def _clear_view(self) -> None:
        self.mail_view.clear()
        self.mail_view.resources = {}
        self.att_bar.hide()
        self.att_list.hide()
        self.att_list.clear()

    def show_attachments(self, attachments: List[Dict[str, Any]]) -> None:
        self.att_list.clear()
        if not attachments:
            self.att_bar.hide()
            self.att_list.hide()
            return

        for att in attachments:
            size = att.get("size")
            label = f"{att['filename']}  ({self._format_size(size)})" if size else att["filename"]
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, att["index"])
            item.setData(Qt.ItemDataRole.UserRole + 1, att["filename"])
            self.att_list.addItem(item)

        count = len(attachments)
        if count == 1:
            att = attachments[0]
            summary = f"1 załącznik: {att['filename']}  {self._format_size(att.get('size'))}"
        else:
            total = sum(a.get("size") or 0 for a in attachments)
            summary = f"{count} załączników  {self._format_size(total)}"
        self.att_summary.setText(summary)
        self.att_list.setFixedHeight(min(26 * count + 8, 130))
        self.att_list.setVisible(count > 1)
        self.att_toggle.setText("v" if count > 1 else ">")
        self.att_toggle.setVisible(count > 1)
        self.att_bar.show()

    @staticmethod
    def _format_size(size: Optional[int]) -> str:
        if not size:
            return ""
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.0f} KB"
        return f"{size / (1024 * 1024):.1f} MB"

    def _toggle_att_list(self) -> None:
        visible = not self.att_list.isVisible()
        self.att_list.setVisible(visible)
        self.att_toggle.setText("v" if visible else ">")

    def _attachment_parts(self):
        return iter_message_attachments(self.cached_message) if self.cached_message else []

    def _write_attachment(self, part, path: str) -> bool:
        payload = part.get_payload(decode=True)
        if payload is None:
            return False
        try:
            with open(path, "wb") as fh:
                fh.write(payload)
            return True
        except OSError:
            return False

    def save_attachments(self, indices: List[int]) -> None:
        parts = self._attachment_parts()
        chosen = [(i, parts[i]) for i in indices if 0 <= i < len(parts)]
        if not chosen:
            return

        def name_of(part) -> str:
            raw = part.get_filename() or "zalacznik"
            return os.path.basename(str(raw)) or "zalacznik"

        saved = 0
        if len(chosen) == 1:
            part = chosen[0][1]
            path, _ = QFileDialog.getSaveFileName(
                self, "Zapisz załącznik", os.path.join(self._last_save_dir(), name_of(part))
            )
            if not path:
                return
            self._remember_save_dir(os.path.dirname(path))
            saved = 1 if self._write_attachment(part, path) else 0
        else:
            directory = QFileDialog.getExistingDirectory(
                self, "Wybierz folder zapisu załączników", self._last_save_dir()
            )
            if not directory:
                return
            self._remember_save_dir(directory)
            for _, part in chosen:
                base, ext = os.path.splitext(name_of(part))
                path = os.path.join(directory, base + ext)
                n = 1
                while os.path.exists(path):
                    path = os.path.join(directory, f"{base} ({n}){ext}")
                    n += 1
                if self._write_attachment(part, path):
                    saved += 1

        if saved:
            QMessageBox.information(self, "Załączniki", f"Zapisano załączników: {saved}.")
        else:
            QMessageBox.warning(self, "Załączniki", "Nie udało się zapisać załączników.")

    def _last_save_dir(self) -> str:
        return getattr(self, "_save_dir", None) or os.path.expanduser("~")

    def _remember_save_dir(self, path: str) -> None:
        self._save_dir = path

    def on_save_attachments_clicked(self) -> None:
        selected = [it.data(Qt.ItemDataRole.UserRole) for it in self.att_list.selectedItems()]
        if not selected:
            selected = [
                self.att_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.att_list.count())
            ]
        self.save_attachments(selected)

    def on_attachment_item_activated(self, item: QListWidgetItem) -> None:
        self.save_attachments([item.data(Qt.ItemDataRole.UserRole)])

    def on_manual_sync(self) -> None:
        if not self.current_account:
            return

        konto_id = self.current_account["id"]

        ok, msg = check_imap(get_account_info(konto_id) or {})
        if not ok:
            QMessageBox.warning(self, "Synchronizacja", f"Nie można połączyć się z serwerem:\n{msg}")
            return

        self._synced_accounts.clear()
        self.start_background_sync()

    def refresh_all(self) -> None:
        self.load_folders_from_db()
        if self.current_folder_local:
            self.load_mail_list_from_db(self.current_folder_local)

    # ------------------------------------------------------------------
    # Pisanie wiadomości
    # ------------------------------------------------------------------
    def _account_label(self) -> str:
        acc = self.current_account or {}
        name = acc.get("display_name") or ""
        return f"{name} <{acc.get('email', '')}>" if name else acc.get("email", "")

    def _open_compose(self, title: str, **kwargs) -> bool:
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
        while dlg.exec() == QDialog.DialogCode.Accepted:
            values = dlg.values()
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                ok, info = send_email_smtp(
                    values["account_id"] or sender["id"],
                    values["to"],
                    values["subject"],
                    values["body"],
                    values["attachments"] or None,
                    cc=values["cc"] or None,
                    bcc=values["bcc"] or None,
                    html_body=values["html"],
                )
            finally:
                QApplication.restoreOverrideCursor()
            if ok:
                QMessageBox.information(self, "Email", "Wiadomość została wysłana.")
                return True
            QMessageBox.warning(self, "Email", f"Nie udało się wysłać wiadomości:\n{info}")
        return False

    def _current_original(self) -> Optional[email.message.Message]:
        if not self.current_account or not self.current_uid or not self.current_folder_local:
            return None
        original = load_message(
            self.current_account["id"], self.current_folder_local, self.current_uid
        )
        if original is None:
            QMessageBox.warning(self, "Email", "Nie udało się wczytać wiadomości.")
        return original

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
            "Odpowiedź", to=to_addr, subject=subject,
            html=self._quote_html(original, "reply"),
        )

    def on_forward(self) -> None:
        original = self._current_original()
        if original is None:
            return
        subject = str(original.get("Subject") or "")
        if not subject.lower().startswith(("fwd:", "fw:")):
            subject = f"Fwd: {subject}"
        self._open_compose(
            "Przekaż wiadomość",
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
            name = os.path.basename(part.get_filename() or f"zalacznik_{index}")
            path = os.path.join(tmp, name)
            with open(path, "wb") as fh:
                fh.write(payload)
            paths.append(path)
        return paths

    # ------------------------------------------------------------------
    # Przenoszenie wiadomości
    # ------------------------------------------------------------------
    def _current_folder_role(self) -> Optional[str]:
        if not self.current_account or not self.current_folder_local:
            return None
        for f in get_folders_for_account(self.current_account["id"]):
            if f["server_name"] == self.current_folder_local:
                return folder_role(f["server_name"], f.get("attributes"), f.get("delimiter"))
        return folder_role(self.current_folder_local)

    def _move_current(self, role: str, title: str, label: str) -> None:
        if not self.current_account or not self.current_uid or not self.current_folder_local:
            return

        konto_id = self.current_account["id"]
        target = find_special_folder(konto_id, role)
        if not target:
            QMessageBox.warning(
                self, title, f"Nie znaleziono na serwerze folderu „{label}”."
            )
            return
        if target == self.current_folder_local:
            return

        try:
            move_email_and_get_sender(
                konto_id, self.current_folder_local, self.current_uid, target
            )
        except Exception as e:
            QMessageBox.warning(self, title, f"Nie udało się przenieść wiadomości:\n{e}")
            return

        self.current_uid = None
        self._clear_view()
        self.load_mail_list_from_db(self.current_folder_local)
        self.load_folders_from_db()

    def on_archive(self) -> None:
        self._move_current("archive", "Archiwizuj", "Archiwum")

    def on_spam(self) -> None:
        self._move_current("junk", "Niechciane", "Spam")

    def on_delete(self) -> None:
        if not self.current_account or not self.current_uid or not self.current_folder_local:
            return

        if self._current_folder_role() == "trash":
            answer = QMessageBox.question(
                self, "Usuń", "Usunąć wiadomość bezpowrotnie?"
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                if not delete_email_remote(
                    self.current_account["id"], self.current_folder_local, self.current_uid
                ):
                    raise RuntimeError("Serwer odrzucił usunięcie wiadomości.")
            except Exception as e:
                QMessageBox.warning(self, "Usuń", f"Nie udało się usunąć wiadomości:\n{e}")
                return
            self.current_uid = None
            self._clear_view()
            self.load_mail_list_from_db(self.current_folder_local)
            self.load_folders_from_db()
            return

        self._move_current("trash", "Usuń", "Kosz")

    def on_more(self) -> None:
        QMessageBox.information(self, "Więcej", "Dodatkowe opcje w przygotowaniu.")

    def closeEvent(self, event) -> None:
        timer = getattr(self, "_timer", None)
        if timer:
            timer.stop()
        super().closeEvent(event)
