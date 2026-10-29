# app/email/email_page.py
from __future__ import annotations

import base64
import json
import os
import re
import email
import tempfile
import threading
import unicodedata
from urllib.parse import quote, unquote
from datetime import datetime
from collections import Counter
from email.utils import getaddresses, parseaddr
from html import escape as html_escape
from typing import List, Dict, Any, Optional, Tuple

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QTreeWidget, QTreeWidgetItem, QMessageBox, QSplitter, QPushButton,
    QLabel, QFrame, QSizePolicy, QDialog, QDialogButtonBox, QTextEdit,
    QToolButton, QStyle, QLineEdit, QApplication, QMenu,
    QHeaderView, QAbstractItemView, QFileDialog, QProgressBar, QScrollArea,
    QButtonGroup, QInputDialog
)
from PyQt6.QtCore import QCoreApplication, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QKeySequence, QShortcut, QDesktopServices
QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView

from app.db.dao_app_settings import get_setting, set_setting
from app.db.dao_employees import list_employees
from app.email.email_labels import (
    COMPLETED_LABEL_NUMBER,
    label_color,
    resolve_label_numbers,
)
from app.email.compose_dialog import ComposeDialog
from app.email.email_connection import check_imap
from app.email import email_cache
from app.email.email_storage import (
    find_special_folder,
    get_assignment_map,
    get_completed_map,
    list_assigned_rows,
    list_completed_rows,
    set_messages_completed,
    set_employee_assignment,
    update_message_read_flag,
    get_account_info,
    get_email_accounts_for_employee,
    get_folder_id,
    get_folders_for_account,
    get_unread_counts,
    filter_visible_unread_counts,
    refresh_email_list,
    search_messages,
)
from app.email.email_sync import (
    list_folders_from_imap_and_store,
    mark_read_remote,
    delete_email_remote,
    mark_unread_remote,
    create_folder,
    move_emails,
    rename_folder,
    sync_and_prefetch,
)

from app.email.email_rules import apply_rules_to_folder, import_thunderbird_filters
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
ROLE_ROW_ID = Qt.ItemDataRole.UserRole + 3
ROLE_FOLDER = Qt.ItemDataRole.UserRole + 4
MAX_NUMBER_KEYS = 7
ROLE_MSGID = Qt.ItemDataRole.UserRole + 20
ROLE_REFS = Qt.ItemDataRole.UserRole + 21
DEFAULT_COLUMN_WIDTHS = [430, 220, 180]
_SUBJECT_PREFIX = re.compile(r"^\s*(re|odp|fw|fwd|pd|wg|aw|przekaż)\s*:\s*", re.IGNORECASE)


def group_threads(entries: List[Tuple[Any, str, str]]) -> List[List[Any]]:
    """Grupuje (element, message_id, refs) w wątki po In-Reply-To/References; kolejność jak na wejściu."""
    parent = list(range(len(entries)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by_id = {mid: i for i, (_, mid, _) in enumerate(entries) if mid}
    by_ref: Dict[str, int] = {}
    for i, (_, _, refs) in enumerate(entries):
        for ref in (refs or "").split():
            if ref in by_id:
                parent[find(i)] = find(by_id[ref])
            elif ref in by_ref:
                parent[find(i)] = find(by_ref[ref])
            else:
                by_ref[ref] = i
    groups: Dict[int, List[Any]] = {}
    for i, (item, _, _) in enumerate(entries):
        groups.setdefault(find(i), []).append(item)
    return list(groups.values())


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
    """'2026-10-15 08:00' -> '15.10.2026, 08:00' (dzisiaj tylko godzina)."""
    try:
        moment = datetime.strptime(value, "%Y-%m-%d %H:%M")
        if moment.date() == datetime.now().date():
            return moment.strftime("%H:%M")
        return moment.strftime("%d.%m.%Y, %H:%M")
    except (TypeError, ValueError):
        return value or ""


def display_names(value: str) -> str:
    """'Jan Kowalski <jan@x.pl>, b@y.pl' -> 'Jan Kowalski, b@y.pl' (sam adres tylko gdy brak nazwy)."""
    names = []
    for name, address in getaddresses([value or ""]):
        label = (name or "").strip().strip('"').strip() or address
        if label:
            names.append(label)
    return ", ".join(names) or (value or "")


def employee_tag(employee: Dict[str, Any]) -> str:
    surname = (employee.get("nazwisko") or "").strip()
    return f"{employee.get('imie') or ''} {surname[:1]}.".strip() if surname else (employee.get("imie") or "")


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


class _MailWebPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        if navigation_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            if url.scheme().lower() in ("http", "https", "mailto"):
                QDesktopServices.openUrl(url)
            return False
        return url.scheme().lower() in ("about", "data", "http", "https")


class MailBrowser(QWebEngineView):
    """Podgląd HTML wiadomości w Chromium; JavaScript i dostęp do plików są wyłączone."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.resources: Dict[str, Any] = {}
        self.setPage(_MailWebPage(self))
        settings = self.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.AutoLoadImages, True)

    def show_html(self, html: str, header: str = "") -> None:
        self.setHtml(self.prepare_html(html, self.resources, header))

    def setPlainText(self, text: str) -> None:
        self.setHtml(
            "<!doctype html><html><head><meta charset='utf-8'></head>"
            "<body><pre style='white-space:pre-wrap'>"
            + html_escape(text)
            + "</pre></body></html>"
        )

    def clear(self) -> None:
        self.setHtml("")

    @staticmethod
    def prepare_html(html: str, resources: Dict[str, Any], header: str = "") -> str:
        resource_map = {key.strip("<> ").casefold(): value for key, value in resources.items()}

        def replace_cid(match):
            key = unquote(match.group(1)).strip("<> ").casefold()
            entry = resource_map.get(key)
            if not entry:
                return match.group(0)
            payload, mime_type = entry
            encoded = base64.b64encode(payload).decode("ascii")
            return f"data:{mime_type};base64,{encoded}"

        html = re.sub(r"(?i)cid:([^\"'\s)>]+)", replace_cid, html)
        header_html = (
            "<div style='font:12px Arial,sans-serif;color:#444;padding:8px;"
            "border-bottom:1px solid #bbb;margin-bottom:8px'>"
            + header
            + "</div>"
            if header
            else (
                "<div style='font:12px Arial,sans-serif;color:#444;padding:8px;"
                "border-bottom:1px solid #bbb;margin-bottom:8px'></div>"
            )
        )
        # Keep complete email documents intact and place the app's message header in their body.
        body_open = re.search(r"<body\b[^>]*>", html, re.IGNORECASE)
        if body_open:
            html = html[:body_open.end()] + header_html + html[body_open.end():]
            return html
        elif re.search(r"<html\b", html, re.IGNORECASE):
            head_end = re.search(r"</head\s*>", html, re.IGNORECASE)
            if head_end:
                html = html[:head_end.end()] + "<body>" + header_html + html[head_end.end():]
            else:
                html_open = re.search(r"<html\b[^>]*>", html, re.IGNORECASE)
                html = html[:html_open.end()] + "<body>" + header_html + html[html_open.end():]
            if not re.search(r"</body\s*>", html, re.IGNORECASE):
                close_html = re.search(r"</html\s*>", html, re.IGNORECASE)
                if close_html:
                    return html[:close_html.start()] + "</body>" + html[close_html.start():]
                return html + "</body>"
            return html
        else:
            return (
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                "</head><body>" + header_html + html + "</body></html>"
            )


class FolderTree(QTreeWidget):
    """Drzewo folderów przyjmujące upuszczone wiadomości i foldery (przenoszenie obsługuje strona)."""

    mails_dropped = pyqtSignal(object)
    folder_dropped = pyqtSignal(object, object)

    def __init__(self, mail_source: QWidget, parent=None):
        super().__init__(parent)
        self.mail_source = mail_source
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    def _accepts(self, event) -> bool:
        return event.source() in (self, self.mail_source)

    def dragEnterEvent(self, event) -> None:
        if self._accepts(event):
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        item = self.itemAt(event.position().toPoint())
        if self._accepts(event) and item is not None and item.data(0, ROLE_FOLDER):
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        item = self.itemAt(event.position().toPoint())
        if item is None or not item.data(0, ROLE_FOLDER) or not self._accepts(event):
            event.ignore()
            return
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        if event.source() is self:
            dragged = self.currentItem()
            if dragged is not None:
                self.folder_dropped.emit(dragged, item)
        else:
            self.mails_dropped.emit(item)


class EmailPage(QWidget):
    sync_finished = pyqtSignal(bool)
    headers_ready = pyqtSignal()
    sync_progress = pyqtSignal(object)
    async_result = pyqtSignal(object)

    def __init__(self, pracownik_id: int):
        super().__init__()
        self.pracownik_id = pracownik_id
        self.accounts = get_email_accounts_for_employee(pracownik_id)
        self.current_account: Optional[Dict[str, Any]] = None
        self.current_folder_local: Optional[str] = None
        self.current_uid: Optional[str] = None
        self.cached_message: Optional[email.message.Message] = None
        self.current_message_labels: List[Tuple[str, str]] = []
        self.unread_map: Dict[str, bool] = {}
        self._sync_running = False
        self._resync_pending = False
        self._tree_signature = None
        self.employee_filter: Optional[int] = None
        self.completed_filter = False
        self.employees: List[Dict[str, Any]] = []
        self.assign_map: Dict[int, List[int]] = {}
        self.completed_map: set[int] = set()
        self._chips_signature = None
        self._load_token = 0
        self.label_numbers: Dict[int, int] = {}
        self.hidden_folders: Dict[str, set[str]] = self._load_hidden_folders()
        self.show_hidden_folders = False
        self._reload_employees()
        self._chips_signature = None
        self._loading_message_key = None

        self._select_timer = QTimer(self)
        self._select_timer.setSingleShot(True)
        self._select_timer.setInterval(120)
        self._select_timer.timeout.connect(self._open_selected_now)

        self._build_ui()
        self._refresh_chips()
        self.async_result.connect(self._on_async_result)
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
    def _reload_employees(self) -> None:
        try:
            employees = [e for e in list_employees() if e.get('aktywny', 1)]
        except Exception:
            employees = []
        self.label_numbers = resolve_label_numbers(employees)
        employees.sort(key=lambda e: (self.label_numbers.get(e["id"], 99), e["id"]))
        self.employees = employees
        self._chips_signature = None

    def _columns_key(self) -> str:
        return f"email_kolumny_{self.pracownik_id}"

    def _saved_column_widths(self) -> List[int]:
        try:
            widths = json.loads(get_setting(self._columns_key()) or "[]")
            if len(widths) == 3 and all(isinstance(w, int) and 30 <= w <= 3000 for w in widths):
                return widths
        except Exception:
            logger.debug("Nie można wczytać szerokości kolumn", exc_info=True)
        return list(DEFAULT_COLUMN_WIDTHS)

    def _save_column_widths(self) -> None:
        widths = [self.mail_list.columnWidth(c) for c in range(3)]
        try:
            set_setting(self._columns_key(), json.dumps(widths))
        except Exception:
            logger.debug("Nie można zapisać szerokości kolumn", exc_info=True)

    def _employee_color(self, employee_id: int) -> str:
        return label_color(self.label_numbers.get(employee_id))

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
            jobs.append((account["id"], folders))
        jobs.sort(key=lambda j: j[0] != self.current_account["id"])
        def work() -> None:
            ok = True
            for konto_id, folders in jobs:
                try:
                    sync_and_prefetch(
                        konto_id, folders,
                        on_headers=lambda: self.headers_ready.emit(),
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
        if self.current_folder_local:
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

        btn_filters = QToolButton()
        btn_filters.setText("Filtry")
        btn_filters.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        filters_menu = QMenu(btn_filters)
        filters_menu.addAction("Importuj filtry z Thunderbirda…").triggered.connect(self.on_import_filters)
        filters_menu.addAction("Zastosuj filtry do folderu Odebrane").triggered.connect(self.on_apply_filters)
        btn_filters.setMenu(filters_menu)
        toolbar.addWidget(btn_filters)

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

        # Labels and folder tree
        self.mail_list = QTreeWidget()

        self.folder_tree = FolderTree(self.mail_list)
        self.folder_tree.setColumnCount(2)
        self.folder_tree.setHeaderLabels(["Folder", ""])

        self.folder_tree.setIndentation(16)
        self.folder_tree.setMinimumWidth(240)

        header = self.folder_tree.header()
        header.setStretchLastSection(False)

        header.setSectionResizeMode(
            0,
            QHeaderView.ResizeMode.Stretch,
        )

        header.setSectionResizeMode(
            1,
            QHeaderView.ResizeMode.ResizeToContents,
        )

        self.folder_tree.setHeaderHidden(True)

        self.folder_tree.currentItemChanged.connect(
            self._on_folder_current_changed
        )
        self.folder_tree.currentItemChanged.connect(self._on_folder_current_changed)
        self.folder_tree.mails_dropped.connect(self._on_mails_dropped)
        self.folder_tree.folder_dropped.connect(self._on_folder_dropped)
        self.folder_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.folder_tree.customContextMenuRequested.connect(self._on_folder_context_menu)

        left_container = QFrame()
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(0, 2, 2, 0)
        left_layout.setSpacing(2)
        labels_title = QLabel("Etykiety")
        labels_title.setStyleSheet("font-weight: bold; padding: 3px 6px;")
        left_layout.addWidget(labels_title)
        self.chips_frame = QFrame()
        self.chips_layout = QVBoxLayout(self.chips_frame)
        self.chips_layout.setContentsMargins(4, 0, 4, 4)
        self.chips_layout.setSpacing(1)
        self.chips_group = QButtonGroup(self)
        self.chips_group.setExclusive(True)
        left_layout.addWidget(self.chips_frame)
        self.show_hidden_button = QPushButton("Pokaż ukryte foldery")
        self.show_hidden_button.setCheckable(True)
        self.show_hidden_button.toggled.connect(self._toggle_show_hidden_folders)
        left_layout.addWidget(self.show_hidden_button)
        left_layout.addWidget(self.folder_tree, 1)
        splitter.addWidget(left_container)

        # Middle panel
        right_container = QFrame()
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_container.setLayout(right_layout)

        self.mail_list.setColumnCount(3)
        self.mail_list.setHeaderLabels(["Temat", "Korespondent", "Data"])
        self.mail_list.setRootIsDecorated(True)
        self.mail_list.setUniformRowHeights(True)
        self.mail_list.setAlternatingRowColors(True)
        self.mail_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.mail_list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.mail_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.mail_list.setDragEnabled(True)
        self.mail_list.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.mail_list.setDefaultDropAction(Qt.DropAction.CopyAction)
        for number in range(1, MAX_NUMBER_KEYS + 1):
            shortcut = QShortcut(QKeySequence(str(number)), self.mail_list)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            if number == COMPLETED_LABEL_NUMBER:
                shortcut.activated.connect(self._toggle_completed_by_number)
            else:
                shortcut.activated.connect(lambda n=number: self._toggle_employee_by_number(n))
        self.mail_list.setStyleSheet(
            "QTreeWidget::item { padding: 5px 6px; }"
            "QTreeWidget::item:selected { background: #cfe3f7; color: #000; }"
        )
        hdr = self.mail_list.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionsMovable(True)
        for column in range(3):
            hdr.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate(self._saved_column_widths()):
            self.mail_list.setColumnWidth(column, width)
        self._columns_timer = QTimer(self)
        self._columns_timer.setSingleShot(True)
        self._columns_timer.setInterval(600)
        self._columns_timer.timeout.connect(self._save_column_widths)
        hdr.sectionResized.connect(lambda *_: self._columns_timer.start())
        self.mail_list.itemSelectionChanged.connect(self._select_timer.start)
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
        self.mail_view.setPlainText("Wybierz wiadomość z listy.")
        message_layout.addWidget(self.mail_view, 1)

        self.att_list = QListWidget()
        self.att_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.att_list.itemDoubleClicked.connect(self.on_attachment_item_activated)
        self.att_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.att_list.customContextMenuRequested.connect(self._on_att_list_menu)
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
        self.att_summary.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.att_summary.customContextMenuRequested.connect(self._on_att_summary_menu)
        bar.addWidget(self.att_summary, 1)
        self.att_save = QPushButton("Zapisz")
        self.att_save.setToolTip("Zapisz załączniki")
        self.att_save.clicked.connect(self.on_save_attachments_clicked)
        bar.addWidget(self.att_save)
        self.att_bar.hide()
        message_layout.addWidget(self.att_bar)
        splitter.addWidget(message_container)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 3)
        splitter.setSizes([260, 880, 700])

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
                unread = filter_visible_unread_counts(
                    folders,
                    get_unread_counts(konto_id),
                    self.hidden_folders.get(str(konto_id), set()),
                )
            except Exception:
                folders, unread = [], {}

            if not any(f["server_name"].upper() == "INBOX" for f in folders):
                folders = folders + [{
                    "server_name": "INBOX",
                    "display_name": "INBOX",
                    "delimiter": None,
                    "attributes": "",
                    "id": None,
                }]

            data.append((account, folders, unread))

        current_id = self.current_account["id"] if self.current_account else None

        signature = (
            current_id,
            self.current_folder_local,
            self.show_hidden_folders,
            tuple(
                (
                    account["id"],
                    tuple(sorted(self.hidden_folders.get(str(account["id"]), set())))
                )
                for account in self.accounts
            ),
            tuple(
                (
                    acc["id"],
                    tuple(
                        (f.get("id"), f["server_name"], f.get("attributes"))
                        for f in folders
                    ),
                    tuple(sorted(unread.items())),
                )
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

        previously_expanded = self._expanded_folder_keys()
        had_tree = self.folder_tree.topLevelItemCount() > 0

        self.folder_tree.blockSignals(True)
        self.folder_tree.clear()

        selected_item: Optional[QTreeWidgetItem] = None

        def subtree_unread(
                unread: Dict[Any, int],
                node: Dict[str, Any],
        ) -> int:
            total = 0

            folder = node.get("folder")

            if folder and folder.get("id"):
                total += unread.get(folder["id"], 0)

            for child in node["children"]:
                total += subtree_unread(unread, child)

            return total

        def add(
                account_id: int,
                unread: Dict[Any, int],
                node: Dict[str, Any],
                parent: QTreeWidgetItem,
                flatten: bool = False,
                under_staff: bool = False,
        ) -> Optional[QTreeWidgetItem]:

            nonlocal selected_item

            hidden = node["path"] in self.hidden_folders.get(
                str(account_id),
                set(),
            )

            if hidden and not self.show_hidden_folders:
                return None

            role = node["role"]

            label = SPECIAL_FOLDER_LABELS.get(role) if role else None

            if label is None:
                label = clean_folder_label(node["label"])

                if under_staff:
                    label = employee_label(label, employees)

            folder = node["folder"]

            count = subtree_unread(
                unread,
                node,
            )

            text_label = label

            if count:
                text_label = f"{text_label} ({count})"

            if hidden:
                text_label += " (ukryty)"

            item = QTreeWidgetItem([
                text_label,
                "",
            ])

            selectable = (
                    folder is not None
                    and "\\noselect"
                    not in (folder.get("attributes") or "").lower()
            )

            if selectable:
                item.setData(0, ROLE_DATA, node["path"])
                item.setData(0, ROLE_ACCOUNT, account_id)
                item.setToolTip(
                    0,
                    decode_mailbox_utf7(node["path"]),
                )
            else:
                item.setFlags(
                    item.flags()
                    & ~Qt.ItemFlag.ItemIsSelectable
                )

            if count:
                font = item.font(0)
                font.setBold(True)
                item.setFont(0, font)

                badge_font = item.font(1)
                badge_font.setBold(True)
                item.setFont(1, badge_font)

                item.setForeground(1, QColor("#0078D4"))

                item.setTextAlignment(
                    1,
                    Qt.AlignmentFlag.AlignCenter,
                )

            if hidden:
                font = item.font(0)
                font.setItalic(True)
                item.setFont(0, font)

                item.setForeground(
                    0,
                    QColor("#888888"),
                )

            item.setData(0, ROLE_FOLDER, node["path"])
            item.setData(0, ROLE_ACCOUNT, account_id)

            parent.addChild(item)

            if (
                    selectable
                    and account_id == current_id
                    and node["path"] == self.current_folder_local
            ):
                selected_item = item

            if not flatten:
                staff = (
                        clean_folder_label(node["label"])
                        .casefold()
                        == "pracownicy"
                )

                for child in node["children"]:
                    add(
                        account_id,
                        unread,
                        child,
                        item,
                        under_staff=staff,
                    )

            return item

        for account, folders, unread in data:

            account_item = QTreeWidgetItem([
                account.get("email", ""),
                "",
            ])

            account_item.setFlags(
                account_item.flags()
                & ~Qt.ItemFlag.ItemIsSelectable
            )

            account_item.setData(
                0,
                ROLE_ACCOUNT,
                account["id"],
            )

            font = account_item.font(0)
            font.setBold(True)
            account_item.setFont(0, font)

            self.folder_tree.addTopLevelItem(account_item)

            account_item.setExpanded(True)

            for root in build_folder_tree(folders):

                if root["role"] == "inbox":

                    inbox_hidden = (
                            root["path"]
                            in self.hidden_folders.get(
                        str(account["id"]),
                        set(),
                    )
                    )

                    add(
                        account["id"],
                        unread,
                        root,
                        account_item,
                        flatten=True,
                    )

                    if not inbox_hidden or self.show_hidden_folders:
                        for child in root["children"]:
                            add(
                                account["id"],
                                unread,
                                child,
                                account_item,
                            )

                else:
                    add(
                        account["id"],
                        unread,
                        root,
                        account_item,
                    )

        if had_tree:
            self._restore_expanded(previously_expanded)

        if selected_item is not None:

            self.folder_tree.setCurrentItem(selected_item)

            parent = selected_item.parent()

            while parent is not None:
                parent.setExpanded(True)
                parent = parent.parent()

        self.folder_tree.blockSignals(False)

    @staticmethod
    def _folder_key(item: QTreeWidgetItem):
        return (item.data(0, ROLE_ACCOUNT), item.data(0, ROLE_FOLDER) or item.text(0))

    def _expanded_folder_keys(self) -> set:
        keys = set()

        def walk(item: QTreeWidgetItem) -> None:
            if item.childCount() and item.isExpanded():
                keys.add(self._folder_key(item))
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.folder_tree.topLevelItemCount()):
            walk(self.folder_tree.topLevelItem(i))
        return keys

    def _restore_expanded(self, keys: set) -> None:
        def walk(item: QTreeWidgetItem, top: bool) -> None:
            if item.childCount():
                wanted = self._folder_key(item) in keys
                if top or wanted:
                    item.setExpanded(wanted)
            for i in range(item.childCount()):
                walk(item.child(i), False)

        for i in range(self.folder_tree.topLevelItemCount()):
            walk(self.folder_tree.topLevelItem(i), True)

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
        self.employee_filter = None
        self.completed_filter = False
        self.load_mail_list_from_db(server_name)
        self.start_background_sync()

    def _style_mail_item(self, item: QTreeWidgetItem, unread: bool) -> None:
        for col in range(item.columnCount()):
            font = item.font(col)
            font.setBold(bool(unread))
            item.setFont(col, font)

    def _folder_roles(self, konto_id: int) -> Dict[str, Optional[str]]:
        try:
            return {
                f["server_name"]: folder_role(f["server_name"], f.get("attributes"), f.get("delimiter"))
                for f in get_folders_for_account(konto_id)
            }
        except Exception:
            return {}

    def load_mail_list_from_db(
            self,
            local_folder: str,
            keep_view: bool = False,
    ) -> None:

        selected_uids = {
            it.data(0, Qt.ItemDataRole.UserRole)
            for it in self.mail_list.selectedItems()
        } if keep_view else set()

        scroll = (
            self.mail_list.verticalScrollBar().value()
            if keep_view else 0
        )

        selected_uid = (
            self.current_uid
            if keep_view else None
        )

        self.mail_list.blockSignals(True)
        self.mail_list.clear()

        if not keep_view:
            self._clear_view()
            self.cached_message = None

        self.unread_map.clear()
        self.assign_map = {}

        if not self.current_account:
            self.mail_list.blockSignals(False)
            return

        konto_id = self.current_account["id"]
        roles = self._folder_roles(konto_id)

        try:
            if self.completed_filter:
                rows = list_completed_rows(konto_id)
            elif self.employee_filter:
                rows = list_assigned_rows(
                    konto_id,
                    self.employee_filter,
                )
            else:
                rows = refresh_email_list(
                    konto_id,
                    local_folder,
                )
        except Exception:
            logger.exception("Nie można wczytać listy wiadomości")
            rows = []

        query = self.search_input.text().strip().casefold()

        if query:
            rows = [
                row
                for row in rows
                if query in " ".join(
                    str(row.get(field) or "")
                    for field in (
                        "temat",
                        "nadawca",
                        "od",
                        "do",
                    )
                ).casefold()
            ]

        try:
            self.assign_map = get_assignment_map(
                [
                    r["id"]
                    for r in rows
                    if r.get("id")
                ]
            )
        except Exception:
            self.assign_map = {}

        try:
            self.completed_map = get_completed_map(
                [
                    r["id"]
                    for r in rows
                    if r.get("id")
                ]
            )
        except Exception:
            logger.warning(
                "Nie można wczytać statusu ukończenia wiadomości",
                exc_info=True,
            )
            self.completed_map = set()

        by_id = {
            e["id"]: e
            for e in self.employees
        }

        restore = None
        all_items = []
        to_select = []

        for r in rows:

            uid = r.get("uid")
            folder = r.get("server_name") or local_folder

            is_unread = not bool(
                r.get("przeczytana")
            )

            self.unread_map[uid] = is_unread

            outgoing = (
                    roles.get(folder)
                    in ("sent", "drafts")
            )

            full = (
                       r.get("odbiorca")
                       if outgoing
                       else r.get("nadawca")
                   ) or ""

            tags = [
                by_id[i]
                for i in self.assign_map.get(
                    r.get("id"),
                    [],
                )
                if i in by_id
            ]

            subject = (
                    r.get("temat")
                    or "(brak tematu)"
            )

            completed = (
                    r.get("id")
                    in self.completed_map
            )

            if completed:
                subject = "✓ " + subject

            if tags:
                tags.sort(
                    key=lambda e:
                    self.label_numbers.get(
                        e["id"],
                        99,
                    )
                )

                tag_text = ", ".join(
                    employee_tag(emp)
                    for emp in tags
                )

                subject = (
                    f"[{tag_text}] {subject}"
                )

            if r.get("has_attachments"):
                subject = "📎 " + subject

            item = QTreeWidgetItem([
                subject,
                display_names(full),
                format_list_date(
                    r.get("data") or ""
                ),
            ])

            item.setToolTip(
                1,
                full,
            )

            if tags:

                tooltip = (
                        "Pracownicy: "
                        + ", ".join(
                    employee_tag(e)
                    for e in tags
                )
                )

                color = QColor(
                    self._employee_color(
                        tags[0]["id"]
                    )
                )

                for column in range(
                        item.columnCount()
                ):
                    item.setBackground(
                        column,
                        color.lighter(185),
                    )
                    item.setForeground(
                        column,
                        QColor("#202020"),
                    )
            else:
                tooltip = ""

            if completed:

                tooltip = (
                    f"{tooltip}\nZrobione"
                    if tooltip
                    else "Zrobione"
                )

                if not tags:
                    color = QColor(
                        label_color(
                            COMPLETED_LABEL_NUMBER
                        )
                    )

                    for column in range(
                            item.columnCount()
                    ):
                        item.setBackground(
                            column,
                            color.lighter(185),
                        )
                        item.setForeground(
                            column,
                            QColor("#202020"),
                        )

            if tooltip:
                item.setToolTip(
                    0,
                    tooltip,
                )

            item.setData(
                0,
                Qt.ItemDataRole.UserRole,
                uid,
            )

            item.setData(
                0,
                Qt.ItemDataRole.UserRole + 1,
                folder,
            )

            item.setData(
                0,
                ROLE_ROW_ID,
                r.get("id"),
            )

            self._style_mail_item(
                item,
                is_unread,
            )

            item.setData(
                0,
                ROLE_MSGID,
                (
                        r.get("message_id")
                        or ""
                ).strip(),
            )

            item.setData(
                0,
                ROLE_REFS,
                r.get("refs") or "",
            )

            all_items.append(item)

            if (
                    selected_uid is not None
                    and uid == selected_uid
            ):
                restore = item

            if uid in selected_uids:
                to_select.append(item)

        if (
                self.employee_filter
                or self.completed_filter
        ):
            grouped = [
                [it]
                for it in all_items
            ]
        else:
            grouped = group_threads(
                [
                    (
                        it,
                        it.data(0, ROLE_MSGID),
                        it.data(0, ROLE_REFS),
                    )
                    for it in all_items
                ]
            )

        for members in grouped:

            head = members[0]

            for child in members[1:]:
                head.addChild(child)

            if len(members) > 1:

                head.setText(
                    0,
                    f"{head.text(0)} [{len(members)}]"
                )

                if any(
                        self.unread_map.get(
                            m.data(
                                0,
                                Qt.ItemDataRole.UserRole,
                            )
                        )
                        for m in members
                ):
                    self._style_mail_item(
                        head,
                        True,
                    )

            self.mail_list.addTopLevelItem(
                head
            )

            if (
                    any(
                        m.parent() is head
                        for m in to_select
                    )
                    or (
                    restore is not None
                    and restore.parent()
                    is head
            )
            ):
                head.setExpanded(True)

        if restore is not None:
            self.mail_list.setCurrentItem(
                restore
            )

        for it in to_select:
            it.setSelected(True)

        if keep_view:
            self.mail_list.verticalScrollBar().setValue(
                scroll
            )

        self.mail_list.blockSignals(False)

        self._refresh_chips()

    # ------------------------------------------------------------------
    # Etykiety pracowników nad listą
    # ------------------------------------------------------------------
    def _refresh_chips(self) -> None:
        signature = (
            self.employee_filter, self.completed_filter,
            tuple((e["id"], self.label_numbers.get(e["id"])) for e in self.employees),
        )
        if signature == self._chips_signature:
            return
        self._chips_signature = signature
        while self.chips_layout.count():
            w = self.chips_layout.takeAt(0).widget()
            if w is not None:
                self.chips_group.removeButton(w) if isinstance(w, QPushButton) else None
                w.deleteLater()

        def chip(text: str, color: str, checked: bool, on_click, tooltip: str = "") -> QPushButton:
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setChecked(checked)
            btn.setMinimumHeight(22)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            selected_background = QColor(color).lighter(175).name()
            btn.setStyleSheet(
                f"QPushButton {{ border: 0; border-radius: 3px; padding: 2px 6px;"
                f" text-align: left; color: {color}; background: transparent; }}"
                f"QPushButton:checked {{ color: #111; background: {selected_background}; font-weight: bold; }}"
                f"QPushButton:hover {{ background: {selected_background}; }}"
            )
            if tooltip:
                btn.setToolTip(tooltip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(on_click)
            self.chips_group.addButton(btn)
            self.chips_layout.addWidget(btn)
            return btn

        chip(
            "Wszystkie", "#555555",
            self.employee_filter is None and not self.completed_filter,
            lambda _=False: self._on_chip_clicked(None),
        )
        chip(
            f"{COMPLETED_LABEL_NUMBER} Zrobione", label_color(COMPLETED_LABEL_NUMBER),
            self.completed_filter,
            lambda _=False: self._on_completed_chip_clicked(),
            f"Klawisz {COMPLETED_LABEL_NUMBER} oznacza zaznaczone wiadomości jako zrobione; "
            "kliknij, aby je wyświetlić",
        )
        for emp in self.employees:
            number = self.label_numbers.get(emp["id"])
            chip(
                employee_tag(emp),
                self._employee_color(emp["id"]),
                not self.completed_filter and self.employee_filter == emp["id"],
                lambda _=False, e=emp["id"]: self._on_chip_clicked(e),
                f"Klawisz {number} przypisuje zaznaczone wiadomości" if number else "",
            )

    def _on_chip_clicked(self, employee_id: Optional[int]) -> None:
        self.employee_filter = employee_id
        self.completed_filter = False
        self.current_uid = None
        if self.current_folder_local:
            self.load_mail_list_from_db(self.current_folder_local)

    def _on_completed_chip_clicked(self) -> None:
        self.employee_filter = None
        self.completed_filter = True
        self.current_uid = None
        if self.current_folder_local:
            self.load_mail_list_from_db(self.current_folder_local)

    # ------------------------------------------------------------------
    # Asynchroniczne akcje (sieć poza wątkiem interfejsu)
    # ------------------------------------------------------------------
    def _run_async(self, work, done) -> None:
        def target() -> None:
            try:
                payload = (done, work(), None)
            except Exception as exc:
                logger.warning("Akcja e-mail nie powiodła się", exc_info=True)
                payload = (done, None, exc)
            try:
                self.async_result.emit(payload)
            except RuntimeError:
                pass  # widżet został już zamknięty

        threading.Thread(target=target, daemon=True).start()

    def _on_async_result(self, payload) -> None:
        done, result, error = payload
        done(result, error)

    # ------------------------------------------------------------------
    # Zaznaczenie
    # ------------------------------------------------------------------
    def _selected_refs(self) -> List[Tuple[QTreeWidgetItem, str, str]]:
        """[(pozycja, folder, uid)] dla zaznaczonych wiadomości."""
        refs = []
        for it in self.mail_list.selectedItems():
            uid = it.data(0, Qt.ItemDataRole.UserRole)
            folder = it.data(0, Qt.ItemDataRole.UserRole + 1)
            if uid and folder:
                refs.append((it, folder, uid))
        return refs

    def _open_selected_now(self) -> None:
        items = self.mail_list.selectedItems()
        if len(items) == 1:
            if items[0].data(0, Qt.ItemDataRole.UserRole) != self.current_uid or self.cached_message is None:
                self.on_mail_clicked(items[0])
        elif not items:
            self.current_uid = None

    def _on_mail_current_changed(self, current, previous) -> None:
        self._open_selected_now()

    def _on_mail_context_menu(self, pos) -> None:
        item = self.mail_list.itemAt(pos)
        if item is None or not self.current_account:
            return
        if not item.isSelected():
            self.mail_list.clearSelection()
            item.setSelected(True)
        refs = self._selected_refs()
        if not refs:
            return
        konto_id = self.current_account["id"]
        count = len(refs)
        any_unread = any(self.unread_map.get(uid, False) for _, _, uid in refs)
        menu = QMenu(self)
        suffix = f" ({count})" if count > 1 else ""

        read_act = menu.addAction(
            ("Oznacz jako przeczytane" if any_unread else "Oznacz jako nieprzeczytane") + suffix
        )
        read_act.triggered.connect(lambda: self._set_read_selected(any_unread))
        menu.addSeparator()

        source_folders = {folder for _, folder, _ in refs}
        move_menu = menu.addMenu("Przenieś do" + suffix)
        try:
            tree = build_folder_tree(get_folders_for_account(konto_id))
        except Exception:
            tree = []
        self._fill_move_menu(move_menu, tree, source_folders, False)
        if move_menu.isEmpty():
            move_menu.setEnabled(False)

        assign_menu = menu.addMenu("Przypisz do pracownika" + suffix)
        row_ids = [it.data(0, ROLE_ROW_ID) for it, _, _ in refs if it.data(0, ROLE_ROW_ID)]
        for emp in self.employees:
            number = self.label_numbers.get(emp["id"])
            name = f"{emp.get('imie') or ''} {emp.get('nazwisko') or ''}".strip()
            act = assign_menu.addAction(f"{name}\t{number}" if number else name)
            act.setCheckable(True)
            has = bool(row_ids) and all(emp["id"] in self.assign_map.get(rid, []) for rid in row_ids)
            act.setChecked(has)
            act.triggered.connect(lambda _=False, e=emp["id"], h=has: self._assign_selected(e, not h))
        if not self.employees:
            assign_menu.setEnabled(False)

        completed = bool(row_ids) and all(rid in self.completed_map for rid in row_ids)
        completed_action = menu.addAction(
            ("Oznacz jako niewykonane" if completed else "Oznacz jako zrobione") + suffix
        )
        completed_action.setToolTip("Klawisz 3")
        completed_action.triggered.connect(lambda: self._set_selected_completed(not completed))

        menu.addSeparator()
        arch = menu.addAction("Archiwizuj" + suffix)
        arch.triggered.connect(self.on_archive)
        spam = menu.addAction("Oznacz jako niechciane" + suffix)
        spam.triggered.connect(self.on_spam)
        delete = menu.addAction("Usuń" + suffix)
        delete.triggered.connect(self.on_delete)
        menu.exec(self.mail_list.viewport().mapToGlobal(pos))

    def _folder_menu_label(self, node: Dict[str, Any], under_staff: bool) -> str:
        label = SPECIAL_FOLDER_LABELS.get(node["role"]) if node["role"] else None
        if label is None:
            label = clean_folder_label(node["label"])
            if under_staff:
                label = employee_label(label, self.employees)
        return label

    def _fill_move_menu(self, menu: QMenu, nodes: List[Dict[str, Any]], exclude: set,
                        under_staff: bool) -> None:
        for node in nodes:
            folder = node["folder"]
            selectable = folder is not None and "\\noselect" not in (folder.get("attributes") or "").lower()
            label = self._folder_menu_label(node, under_staff)
            staff = clean_folder_label(node["label"]).casefold() == "pracownicy"
            children = node["children"]
            if node["role"] == "inbox":
                if selectable and node["path"] not in exclude:
                    menu.addAction(label).triggered.connect(
                        lambda _=False, t=node["path"]: self._move_selected_to(t))
                self._fill_move_menu(menu, children, exclude, False)
                continue
            if children:
                sub = menu.addMenu(label)
                if selectable and node["path"] not in exclude:
                    sub.addAction(label).triggered.connect(
                        lambda _=False, t=node["path"]: self._move_selected_to(t))
                    sub.addSeparator()
                self._fill_move_menu(sub, children, exclude, staff)
            elif selectable and node["path"] not in exclude:
                menu.addAction(label).triggered.connect(
                    lambda _=False, t=node["path"]: self._move_selected_to(t))

    def _set_read_selected(self, read: bool) -> None:
        refs = self._selected_refs()
        if not refs or not self.current_account:
            return
        konto_id = self.current_account["id"]
        for item, folder, uid in refs:
            self.unread_map[uid] = not read
            self._style_mail_item(item, not read)
            try:
                update_message_read_flag(konto_id, uid, read, get_folder_id(konto_id, folder))
            except Exception:
                logger.warning("Nie można zapisać flagi przeczytania", exc_info=True)
        self.load_folders_from_db()

        def work():
            remote = mark_read_remote if read else mark_unread_remote
            return [remote(konto_id, folder, uid) for _, folder, uid in refs]

        self._run_async(work, lambda res, err: None)

    def _assign_selected(self, employee_id: int, assigned: bool) -> None:
        row_ids = [it.data(0, ROLE_ROW_ID) for it, _, _ in self._selected_refs() if it.data(0, ROLE_ROW_ID)]
        if not row_ids:
            return
        try:
            set_employee_assignment(row_ids, employee_id, assigned)
        except Exception as e:
            QMessageBox.warning(self, "Przypisanie", f"Nie udało się zapisać przypisania:\n{e}")
            return
        self._chips_signature = None
        if self.current_folder_local:
            self.load_mail_list_from_db(self.current_folder_local, keep_view=True)

    def _set_selected_completed(self, completed: bool) -> None:
        row_ids = [
            it.data(0, ROLE_ROW_ID)
            for it, _, _ in self._selected_refs()
            if it.data(0, ROLE_ROW_ID)
        ]
        if not row_ids:
            return
        try:
            set_messages_completed(row_ids, completed)
        except Exception as exc:
            QMessageBox.warning(self, "Status wiadomości", f"Nie udało się zapisać statusu:\n{exc}")
            return
        self._chips_signature = None
        if self.current_folder_local:
            self.load_mail_list_from_db(self.current_folder_local, keep_view=True)

    def _toggle_completed_by_number(self) -> None:
        row_ids = [
            it.data(0, ROLE_ROW_ID)
            for it, _, _ in self._selected_refs()
            if it.data(0, ROLE_ROW_ID)
        ]
        if row_ids:
            completed = all(rid in self.completed_map for rid in row_ids)
            self._set_selected_completed(not completed)

    def on_mail_clicked(
            self,
            item: QTreeWidgetItem,
            column: int = 0,
    ) -> None:
        uid = item.data(0, Qt.ItemDataRole.UserRole)
        local_folder = item.data(
            0,
            Qt.ItemDataRole.UserRole + 1,
        )

        if not uid or not local_folder or not self.current_account:
            return

        konto_id = self.current_account["id"]

        load_key = (
            konto_id,
            str(local_folder),
            str(uid),
        )

        # Nie uruchamiaj ponownie pobierania tej samej wiadomości,
        # jeśli poprzednie pobieranie nadal trwa.
        if self._loading_message_key == load_key:
            return

        self.current_uid = uid
        self.current_folder_local = local_folder
        self.cached_message = None

        row_id = item.data(0, ROLE_ROW_ID)

        by_id = {
            employee["id"]: employee
            for employee in self.employees
        }

        row_labels = sorted(
            self.assign_map.get(row_id, []),
            key=lambda employee_id: self.label_numbers.get(
                employee_id,
                99,
            ),
        )

        self.current_message_labels = [
            (
                employee_tag(by_id[employee_id]),
                self._employee_color(employee_id),
            )
            for employee_id in row_labels
            if employee_id in by_id
        ]

        if row_id in self.completed_map:
            self.current_message_labels.append(
                (
                    "Zrobione",
                    label_color(COMPLETED_LABEL_NUMBER),
                )
            )

        self._load_token += 1
        token = self._load_token

        self._loading_message_key = load_key

        self._clear_view()
        self.mail_view.setPlainText("Pobieranie wiadomości…")

        def show(msg) -> None:
            if token != self._load_token:
                return

            if not msg:
                self.mail_view.setPlainText(
                    "Nie udało się pobrać treści wiadomości.\n"
                    "Sprawdź połączenie z serwerem i bazą PostgreSQL, "
                    "a następnie spróbuj ponownie."
                )
                return

            self._render_message(msg)

            self._mark_opened_as_read(
                item,
                konto_id,
                local_folder,
                uid,
            )

        def done(msg, err) -> None:
            if token != self._load_token:
                return

            self._loading_message_key = None

            if err:
                logger.error(
                    "Nie udało się załadować wiadomości "
                    "konto=%s folder=%s uid=%s",
                    konto_id,
                    local_folder,
                    uid,
                    exc_info=(
                        type(err),
                        err,
                        err.__traceback__,
                    ) if isinstance(err, BaseException) else None,
                )

                self.mail_view.setPlainText(
                    "Wystąpił błąd podczas pobierania wiadomości.\n"
                    "Sprawdź logi aplikacji, połączenie z PostgreSQL "
                    "oraz dostępność serwera IMAP."
                )
                return

            show(msg)

        try:
            self._run_async(
                lambda: load_message(
                    konto_id,
                    local_folder,
                    uid,
                ),
                done,
            )
        except Exception:
            self._loading_message_key = None
            logger.exception(
                "Nie udało się uruchomić pobierania wiadomości "
                "konto=%s folder=%s uid=%s",
                konto_id,
                local_folder,
                uid,
            )
            self.mail_view.setPlainText(
                "Nie udało się uruchomić pobierania wiadomości."
            )

    def _mark_opened_as_read(self, item: QTreeWidgetItem, konto_id: int, folder: str, uid: str) -> None:
        if not self.unread_map.get(uid, False):
            return
        self.unread_map[uid] = False
        self._style_mail_item(item, False)
        try:
            update_message_read_flag(konto_id, uid, True, get_folder_id(konto_id, folder))
        except Exception:
            logger.warning("Nie można zapisać flagi przeczytania", exc_info=True)
        self.load_folders_from_db()
        self._run_async(lambda: mark_read_remote(konto_id, folder, uid), lambda res, err: None)

    def _render_message(self, msg: email.message.Message) -> None:
        self.cached_message = msg
        labels_html = ""
        if self.current_message_labels:
            badges = "".join(
                f"<span style='display:inline-block; margin:2px 4px 2px 0; padding:3px 8px;"
                f" border-radius:3px; background:{html_escape(color)}; color:#fff;'>"
                f"{html_escape(name)}</span>"
                for name, color in self.current_message_labels
            )
            labels_html = f"Etykiety: {badges}<br>"
        header = (
            f"<b>{html_escape(str(msg.get('Subject') or '(brak tematu)'))}</b><br>"
            f"Od: {html_escape(str(msg.get('From') or ''))}<br>"
            f"Do: {html_escape(str(msg.get('To') or ''))}<br>"
            f"Data: {html_escape(str(msg.get('Date') or ''))}<br>"
            f"{labels_html}<hr>"
        )
        html = extract_html(msg)
        self.mail_view.resources = inline_resources(msg)

        if html:
            self.mail_view.show_html(html, header)
        else:
            self.mail_view.show_html(
                "<pre style='white-space: pre-wrap;'>"
                + html_escape(extract_text(msg)) + "</pre>",
                header,
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
        self.att_save.setText("Zapisz" if count == 1 else "Zapisz wszystkie")
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
        self.save_attachments([
            self.att_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.att_list.count())
        ])

    def _attachment_menu(self, indices: List[int], global_pos) -> None:
        if not indices:
            return
        menu = QMenu(self)
        if len(indices) == 1:
            index = indices[0]
            menu.addAction("Otwórz", lambda: self._open_attachment_index(index))
            menu.addAction("Zapisz jako…", lambda: self.save_attachments([index]))
        else:
            menu.addAction("Zapisz zaznaczone…", lambda: self.save_attachments(indices))
        menu.exec(global_pos)

    def _on_att_list_menu(self, pos) -> None:
        item = self.att_list.itemAt(pos)
        if item is not None and not item.isSelected():
            self.att_list.clearSelection()
            item.setSelected(True)
        indices = [it.data(Qt.ItemDataRole.UserRole) for it in self.att_list.selectedItems()]
        self._attachment_menu(indices, self.att_list.viewport().mapToGlobal(pos))

    def _on_att_summary_menu(self, pos) -> None:
        if self.att_list.count() == 1:
            index = self.att_list.item(0).data(Qt.ItemDataRole.UserRole)
            self._attachment_menu([index], self.att_summary.mapToGlobal(pos))

    def _open_attachment_index(self, index: int) -> None:
        parts = self._attachment_parts()
        if isinstance(index, int) and 0 <= index < len(parts):
            if not self._open_attachment_externally(parts[index]):
                self.save_attachments([index])

    def on_attachment_item_activated(self, item: QListWidgetItem) -> None:
        index = item.data(Qt.ItemDataRole.UserRole)
        parts = self._attachment_parts()
        part = parts[index] if isinstance(index, int) and 0 <= index < len(parts) else None
        is_pdf = part is not None and (
            part.get_content_type() == "application/pdf"
            or str(part.get_filename() or "").lower().endswith(".pdf")
        )
        if is_pdf and self._open_attachment_externally(part):
            return
        self.save_attachments([index])

    def _open_attachment_externally(self, part) -> bool:
        name = os.path.basename(str(part.get_filename() or "zalacznik.pdf")) or "zalacznik.pdf"
        directory = tempfile.mkdtemp(prefix="biuro_zal_")
        path = os.path.join(directory, name)
        if not self._write_attachment(part, path):
            return False
        return QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def on_manual_sync(self) -> None:
        if not self.current_account:
            return

        konto_id = self.current_account["id"]

        ok, msg = check_imap(get_account_info(konto_id) or {})
        if not ok:
            QMessageBox.warning(self, "Synchronizacja", f"Nie można połączyć się z serwerem:\n{msg}")
            return

        # Nie uruchamiaj drugiego wątku na już trwającej synchronizacji.
        self.start_background_sync()

    def refresh_all(self) -> None:
        self._reload_employees()
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
        while dlg.exec() == QDialog.DialogCode.Accepted:
            values = dlg.values()
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                account_id = values["account_id"] or sender["id"]
                target = copy_folder
                if target is None:
                    try:
                        from app.email.email_actions import choose_copy_folder
                        account = next((a for a in self.accounts if a["id"] == account_id), sender)
                        target = choose_copy_folder(
                            account_id, account.get("email", ""),
                            list(values["to"]) + list(values["cc"] or []), values["subject"],
                        )
                    except Exception:
                        target = None
                ok, info = send_email_smtp(
                    account_id,
                    values["to"],
                    values["subject"],
                    values["body"],
                    values["attachments"] or None,
                    cc=values["cc"] or None,
                    bcc=values["bcc"] or None,
                    html_body=values["html"],
                    in_reply_to=str(reply_to.get("Message-ID") or "").strip() if reply_to else None,
                    references=str(reply_to.get("References") or "").strip() if reply_to else None,
                    save_copy=True,
                    copy_folder=target,
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
    def _role_of(self, folder: str) -> Optional[str]:
        return self._folder_roles(self.current_account["id"]).get(folder) if self.current_account else None

    def _current_folder_role(self) -> Optional[str]:
        return self._role_of(self.current_folder_local) if self.current_folder_local else None

    def _targets(self) -> List[Tuple[QTreeWidgetItem, str, str]]:
        refs = self._selected_refs()
        if not refs and self.current_uid and self.current_folder_local:
            refs = [(None, self.current_folder_local, self.current_uid)]
        return refs

    def _after_change(self) -> None:

        current_item = self.mail_list.currentItem()

        current_row = 0

        if current_item:
            current_row = self.mail_list.indexOfTopLevelItem(current_item)

        self.current_uid = None
        self.cached_message = None
        self._chips_signature = None

        if self.current_folder_local:
            self.load_mail_list_from_db(self.current_folder_local)

            count = self.mail_list.topLevelItemCount()

            if count:

                row = min(
                    current_row,
                    count - 1,
                )

                item = self.mail_list.topLevelItem(row)

                if item:
                    self.mail_list.setCurrentItem(item)

        self.load_folders_from_db()

    def _move_refs(self, refs, target: str, title: str) -> None:
        if not self.current_account or not refs:
            return
        konto_id = self.current_account["id"]
        by_folder: Dict[str, List[str]] = {}
        for _, folder, uid in refs:
            by_folder.setdefault(folder, []).append(uid)

        def work():
            for folder, uids in by_folder.items():
                move_emails(konto_id, folder, uids, target)

        def done(res, err) -> None:
            if err is not None:
                QMessageBox.warning(self, title, f"Nie udało się przenieść wiadomości:\n{err}")
            self._after_change()

        # lista odświeża się od razu, a serwer pracuje w tle
        self.mail_view.setPlainText("Przenoszenie…")
        self._run_async(work, done)

    def _move_selected_to(self, target: str) -> None:
        self._move_refs(self._targets(), target, "Przenieś do folderu")

    def _move_current(self, role: str, title: str, label: str) -> None:
        refs = self._targets()
        if not self.current_account or not refs:
            return
        target = find_special_folder(self.current_account["id"], role)
        if not target:
            QMessageBox.warning(self, title, f"Nie znaleziono na serwerze folderu „{label}”.")
            return
        self._move_refs([r for r in refs if r[1] != target], target, title)

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
            noun = "wiadomość" if len(refs) == 1 else f"wiadomości ({len(refs)})"
            if QMessageBox.question(self, "Usuń", f"Usunąć {noun} bezpowrotnie?") \
                    != QMessageBox.StandardButton.Yes:
                return

            def work():
                for _, folder, uid in refs:
                    if not delete_email_remote(konto_id, folder, uid):
                        raise RuntimeError("Serwer odrzucił usunięcie wiadomości.")

            def done(res, err) -> None:
                if err is not None:
                    QMessageBox.warning(self, "Usuń", f"Nie udało się usunąć wiadomości:\n{err}")
                self._after_change()

            self._run_async(work, done)
            return

        self._move_current("trash", "Usuń", "Kosz")

    def _toggle_employee_by_number(self, number: int) -> None:
        if number == COMPLETED_LABEL_NUMBER:
            return
        emp = next((e for e in self.employees if self.label_numbers.get(e["id"]) == number), None)
        if emp is None:
            return
        row_ids = [it.data(0, ROLE_ROW_ID) for it, _, _ in self._selected_refs() if it.data(0, ROLE_ROW_ID)]
        if not row_ids:
            return
        has = all(emp["id"] in self.assign_map.get(rid, []) for rid in row_ids)
        self._assign_selected(emp["id"], not has)

    # ------------------------------------------------------------------
    # Foldery: tworzenie, zmiana nazwy i przenoszenie (także na serwerze)
    # ------------------------------------------------------------------
    def _folder_delimiter(self, konto_id: int, path: str) -> str:
        folders = get_folders_for_account(konto_id)
        for f in folders:
            if f["server_name"] == path and f.get("delimiter"):
                return f["delimiter"]
        return next((f["delimiter"] for f in folders if f.get("delimiter")), ".")

    @staticmethod
    def _leaf_of(path: str, delimiter: str) -> str:
        return path.rsplit(delimiter, 1)[-1] if delimiter in path else path

    @staticmethod
    def _parent_of(path: str, delimiter: str) -> str:
        return path.rsplit(delimiter, 1)[0] if delimiter in path else ""

    def _reload_after_folder_change(self, renamed: Optional[Tuple[str, str]] = None) -> None:
        if renamed and self.current_folder_local:
            old, new = renamed
            cur = self.current_folder_local
            if cur == old or cur.startswith(old):
                self.current_folder_local = new + cur[len(old):]
        self._tree_signature = None
        self.load_folders_from_db()
        if self.current_folder_local:
            self.load_mail_list_from_db(self.current_folder_local)

    def _on_folder_context_menu(self, pos) -> None:
        item = self.folder_tree.itemAt(pos)
        account_id = item.data(0, ROLE_ACCOUNT) if item is not None else None
        if item is None or account_id is None:
            return
        path = item.data(0, ROLE_FOLDER)
        menu = QMenu(self)
        menu.addAction("Nowy folder…").triggered.connect(lambda: self._new_folder(account_id, path))
        if path:
            hidden = path in self.hidden_folders.get(str(account_id), set())
            visibility_action = menu.addAction("Pokaż folder" if hidden else "Ukryj folder")
            visibility_action.triggered.connect(
                lambda _=False, aid=account_id, folder=path, hide=not hidden:
                self._set_folder_hidden(aid, folder, hide)
            )
            role = folder_role(path, None, self._folder_delimiter(account_id, path))
            rename = menu.addAction("Zmień nazwę…")
            rename.setEnabled(role is None and path.upper() != "INBOX")
            rename.triggered.connect(lambda: self._rename_folder(account_id, path))
        menu.exec(self.folder_tree.viewport().mapToGlobal(pos))

    def _hidden_folders_setting_key(self) -> str:
        return f"email_ukryte_foldery_{self.pracownik_id}"

    def _load_hidden_folders(self) -> Dict[str, set[str]]:
        try:
            raw = get_setting(f"email_ukryte_foldery_{self.pracownik_id}") or "{}"
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("Nieprawidłowy format ustawienia ukrytych folderów.")
            return {
                str(account_id): {str(path) for path in paths if isinstance(path, str)}
                for account_id, paths in data.items()
                if isinstance(paths, list)
            }
        except (TypeError, ValueError):
            logger.warning("Nie można odczytać ustawienia ukrytych folderów", exc_info=True)
            return {}

    def _toggle_show_hidden_folders(self, show: bool) -> None:
        self.show_hidden_folders = show
        self.show_hidden_button.setText(
            "Ukryj ukryte foldery" if show else "Pokaż ukryte foldery"
        )
        if not show and self._current_folder_is_hidden():
            self.current_folder_local = None
            self.current_uid = None
            self._clear_view()
        self._tree_signature = None
        self.load_folders_from_db()
        if self.current_folder_local is None:
            self._select_first_visible_folder()

    def _current_folder_is_hidden(self) -> bool:
        if not self.current_account or not self.current_folder_local:
            return False
        paths = self.hidden_folders.get(str(self.current_account["id"]), set())
        delimiter = self._folder_delimiter(
            self.current_account["id"], self.current_folder_local
        )
        return any(
            self.current_folder_local == path
            or self.current_folder_local.startswith(path + delimiter)
            for path in paths
        )

    def _select_first_visible_folder(self) -> None:
        account_id = self.current_account["id"] if self.current_account else None

        def find(item: QTreeWidgetItem) -> Optional[QTreeWidgetItem]:
            if item.data(0, ROLE_ACCOUNT) == account_id and item.data(0, ROLE_DATA):
                return item
            for index in range(item.childCount()):
                found = find(item.child(index))
                if found is not None:
                    return found
            return None

        for index in range(self.folder_tree.topLevelItemCount()):
            found = find(self.folder_tree.topLevelItem(index))
            if found is not None:
                self.folder_tree.setCurrentItem(found)
                return

    def _set_folder_hidden(self, account_id: int, path: str, hidden: bool) -> None:
        key = str(account_id)
        paths = self.hidden_folders.setdefault(key, set())
        was_hidden = path in paths
        if hidden:
            paths.add(path)
        else:
            paths.discard(path)
        try:
            stored = {
                account: sorted(folders)
                for account, folders in self.hidden_folders.items()
                if folders
            }
            set_setting(self._hidden_folders_setting_key(), json.dumps(stored, ensure_ascii=False))
        except Exception:
            if was_hidden:
                paths.add(path)
            else:
                paths.discard(path)
            logger.exception("Nie udało się zapisać ustawienia ukrytych folderów")
            QMessageBox.warning(self, "Foldery", "Nie udało się zapisać ustawienia widoczności folderu.")
            return

        current_will_be_hidden = (
            hidden
            and self.current_account
            and self.current_account["id"] == account_id
            and self.current_folder_local
            and (
                self.current_folder_local == path
                or self.current_folder_local.startswith(
                    path + self._folder_delimiter(account_id, path)
                )
            )
        )
        if current_will_be_hidden:
            self.current_folder_local = None
            self.current_uid = None
            self._clear_view()
        self._tree_signature = None
        self.load_folders_from_db()
        if current_will_be_hidden:
            self._select_first_visible_folder()

    def _folder_name_dialog(self, title: str, text: str = "") -> Optional[str]:
        name, ok = QInputDialog.getText(self, title, "Nazwa folderu:", text=text)
        name = name.strip()
        if not ok or not name:
            return None
        if any(ch in name for ch in "./\\"):
            QMessageBox.warning(self, title, "Nazwa folderu nie może zawierać znaków . / \\")
            return None
        return name

    def _new_folder(self, konto_id: int, parent_path: Optional[str]) -> None:
        name = self._folder_name_dialog("Nowy folder")
        if not name:
            return
        parent = parent_path or "INBOX"
        delimiter = self._folder_delimiter(konto_id, parent)
        full = decode_mailbox_utf7(parent) + delimiter + name

        def done(res, err) -> None:
            if err is not None:
                QMessageBox.warning(self, "Nowy folder", f"Nie udało się utworzyć folderu:\n{err}")
                return
            self._reload_after_folder_change()

        self._run_async(lambda: create_folder(konto_id, full), done)

    def _rename_folder(self, konto_id: int, path: str) -> None:
        delimiter = self._folder_delimiter(konto_id, path)
        name = self._folder_name_dialog(
            "Zmień nazwę folderu", decode_mailbox_utf7(self._leaf_of(path, delimiter))
        )
        if not name:
            return
        parent = self._parent_of(path, delimiter)
        full = (decode_mailbox_utf7(parent) + delimiter if parent else "") + name
        self._run_folder_rename(konto_id, path, full)

    def _run_folder_rename(self, konto_id: int, old: str, new_name: str) -> None:
        def done(res, err) -> None:
            if err is not None:
                QMessageBox.warning(self, "Folder", f"Serwer nie zmienił nazwy folderu:\n{err}")
                return
            self._reload_after_folder_change((old, res))

        self._run_async(lambda: rename_folder(konto_id, old, new_name), done)

    def _on_folder_dropped(self, source, target) -> None:
        konto_id = source.data(0, ROLE_ACCOUNT)
        if konto_id is None or konto_id != target.data(0, ROLE_ACCOUNT):
            return
        src, dst = source.data(0, ROLE_FOLDER), target.data(0, ROLE_FOLDER)
        if not src or not dst or src == dst or src.upper() == "INBOX":
            return
        delimiter = self._folder_delimiter(konto_id, src)
        if dst.startswith(src + delimiter) or self._parent_of(src, delimiter) == dst:
            return
        if folder_role(src, None, delimiter) is not None:
            QMessageBox.information(self, "Folder", "Folderów systemowych nie można przenosić.")
            return
        leaf = decode_mailbox_utf7(self._leaf_of(src, delimiter))
        label = clean_folder_label(decode_mailbox_utf7(dst))
        if QMessageBox.question(self, "Przenieś folder", f"Przenieść folder „{leaf}” do „{label}”?") \
                != QMessageBox.StandardButton.Yes:
            return
        self._run_folder_rename(konto_id, src, decode_mailbox_utf7(dst) + delimiter + leaf)

    def _on_mails_dropped(self, target) -> None:
        folder = target.data(0, ROLE_DATA)
        if not folder or not self.current_account or target.data(0, ROLE_ACCOUNT) != self.current_account["id"]:
            return
        refs = [r for r in self._selected_refs() if r[1] != folder]
        self._move_refs(refs, folder, "Przenieś do folderu")

    # ------------------------------------------------------------------
    # Filtry (import z Thunderbirda, stosowanie do Odebranych)
    # ------------------------------------------------------------------
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

    def on_apply_filters(self) -> None:
        if not self.current_account:
            return
        konto_id = self.current_account["id"]

        def done(res, err) -> None:
            if err is not None:
                QMessageBox.warning(self, "Filtry", f"Nie udało się zastosować filtrów:\n{err}")
            else:
                QMessageBox.information(self, "Filtry", f"Przeniesiono wiadomości: {res}")
            self._after_change()

        self._run_async(lambda: apply_rules_to_folder(konto_id, "INBOX"), done)

    def on_more(self) -> None:
        QMessageBox.information(self, "Więcej", "Dodatkowe opcje w przygotowaniu.")

    def closeEvent(self, event) -> None:
        timer = getattr(self, "_timer", None)
        if timer:
            timer.stop()
        super().closeEvent(event)
