# app/email/email_page.py
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import email
import tempfile
import threading
import unicodedata
from uuid import uuid4
from urllib.parse import quote, unquote
from datetime import datetime
import time
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
from PyQt6.QtCore import QCoreApplication, Qt, QTimer, QUrl, pyqtSignal, QItemSelectionModel
from PyQt6.QtGui import QAction, QColor, QFont, QKeySequence, QShortcut, QDesktopServices
QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
from PyQt6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlRequestInterceptor,
)
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
    mail_read_snapshot,
    list_assigned_rows,
    count_unread_assigned_by_employee,
    resolve_assigned_message,
    resolve_notification_message,
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
    sync_automatic_accounts,
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
    is_risky_attachment,
    iter_message_attachments,
    safe_attachment_name,
    safe_header,
)
import logging
from app.utils.background import TaskRunner, CommandRunner, LatestRead
from app.utils.view_state import ItemViewState
from app.email.mail_model import MailItem, MailTreeView
from app.email.email_storage import get_mail_page

logger = logging.getLogger(__name__)

AUTO_SYNC_INTERVAL_MS = 120_000
ROLE_DATA = Qt.ItemDataRole.UserRole
ROLE_ACCOUNT = Qt.ItemDataRole.UserRole + 2
ROLE_ROW_ID = Qt.ItemDataRole.UserRole + 3
ROLE_FOLDER = Qt.ItemDataRole.UserRole + 4
MAX_NUMBER_KEYS = 7
ROLE_MSGID = Qt.ItemDataRole.UserRole + 20
ROLE_REFS = Qt.ItemDataRole.UserRole + 21
ROLE_FOLDER_LABEL = Qt.ItemDataRole.UserRole + 30
ROLE_FOLDER_IDS = Qt.ItemDataRole.UserRole + 31
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
    """Usuwa numer porządkowy ("3 Pracownicy") i prefiks "DEMO " z nazwy folderu."""
    label = re.sub(r"^\d+\s+", "", label.strip())
    label = re.sub(r"^DEMO\s+", "", label, flags=re.IGNORECASE)
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


def employee_name(employee: Dict[str, Any]) -> str:
    return f"{employee.get('imie') or ''} {employee.get('nazwisko') or ''}".strip()


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


class _RemoteContentBlocker(QWebEngineUrlRequestInterceptor):
    """Blokuje zdalne zasoby (piksele śledzące, obrazy, CSS) dopóki użytkownik ich nie zaakceptuje."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.allow_remote = False

    def interceptRequest(self, info) -> None:
        if not self.allow_remote and info.requestUrl().scheme().lower() in ("http", "https", "ftp"):
            info.block(True)


class _MailWebPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        scheme = url.scheme().lower()
        if navigation_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            if scheme == "biuro-images":
                view = self.parent()
                if hasattr(view, "load_remote_images"):
                    view.load_remote_images()
            elif scheme == 'biuro-contact':
                view = self.parent()
                target = getattr(view, 'contact_links', {}).get(url.path())
                if target is not None:
                    view.address_clicked.emit(*target)
            elif scheme in ("http", "https", "mailto"):
                QDesktopServices.openUrl(url)
            return False
        return scheme in ("about", "data")


class MailBrowser(QWebEngineView):
    """Podgląd HTML wiadomości w Chromium; JavaScript, pliki i zdalne zasoby są domyślnie blokowane."""
    address_clicked = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.resources: Dict[str, Any] = {}
        self.contact_links = {}
        self._last_html = ""
        self._last_header = ""
        # Osobny profil bez zapisu na dysku, żeby blokada nie dotyczyła innych widoków aplikacji.
        self._profile = QWebEngineProfile(self)
        self._blocker = _RemoteContentBlocker(self)
        self._profile.setUrlRequestInterceptor(self._blocker)
        self.setPage(_MailWebPage(self._profile, self))
        self.page().setBackgroundColor(QColor("white"))
        settings = self.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.AutoLoadImages, True)

    def load_remote_images(self) -> None:
        self._blocker.allow_remote = True
        self.setHtml(self.prepare_html(self._last_html, self.resources, self._last_header))

    def show_html(self, html: str, header: str = "") -> None:
        self._blocker.allow_remote = False
        self._last_html, self._last_header = html, header
        if re.search(r"(?i)(https?:)?//[^\s\"'<>)]+", html or "") and re.search(
            r"(?i)<img\b|url\(|<link\b", html or ""
        ):
            header += (
                "<div style='margin:4px 0'><a href='biuro-images:load'>"
                "Zdalne obrazy zablokowane – kliknij, aby załadować</a></div>"
            )
        self.setHtml(self.prepare_html(html, self.resources, header))

    def setPlainText(self, text: str) -> None:
        self.setHtml(
            "<!doctype html><html><head><meta charset='utf-8'></head>"
            "<body><pre style='white-space:pre-wrap'>"
            + html_escape(text)
            + "</pre></body></html>"
        )

    def clear(self) -> None:
        self.contact_links = {}
        self.setHtml("")

    def contextMenuEvent(self, event):
        request = self.lastContextMenuRequest()
        url = request.linkUrl() if request is not None else QUrl()
        target = self.contact_links.get(url.path()) if url.scheme() == 'biuro-contact' else None
        if target is not None:
            self.address_clicked.emit(*target)
            event.accept()
            return
        super().contextMenuEvent(event)

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
    folder_visibility_changed = pyqtSignal()
    sync_finished = pyqtSignal(bool)
    sync_failed = pyqtSignal(str)
    headers_ready = pyqtSignal()
    sync_progress = pyqtSignal(object)
    async_result = pyqtSignal(object)

    def __init__(self, pracownik_id: int):
        super().__init__()
        self.pracownik_id = pracownik_id
        self._list_jobs = TaskRunner(self)
        self._message_jobs = TaskRunner(self)
        self._command_jobs = CommandRunner(self)
        self._contact_jobs = TaskRunner(self)
        self._contact_editor_pending = False
        self._sync_jobs = CommandRunner(self)
        self._mail_rows = []
        self._mail_cursor = None
        self._mail_context = None
        self._pending_mail_load = None
        self.accounts = get_email_accounts_for_employee(pracownik_id)
        self.current_account: Optional[Dict[str, Any]] = None
        self.current_folder_local: Optional[str] = None
        self.current_uid: Optional[str] = None
        self.cached_message: Optional[email.message.Message] = None
        self.current_message_labels: List[Tuple[str, str]] = []
        self.unread_map: Dict[int, bool] = {}
        self._sync_running = False
        self._sync_cancel = threading.Event()
        self._last_account_check = 0.0
        self._last_sync_error = ""
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
        self.sync_failed.connect(self._on_sync_failed)
        self._headers_refresh_timer = QTimer(self)
        self._headers_refresh_timer.setSingleShot(True)
        self._headers_refresh_timer.setInterval(500)
        self._headers_refresh_timer.timeout.connect(self._on_headers_ready)
        self.headers_ready.connect(self._schedule_headers_refresh)
        self.sync_progress.connect(self._on_sync_progress)

        if self.accounts:
            self.current_account = self.accounts[0]
            self.load_folders_from_db()
            self.start_background_sync()
        else:
            QMessageBox.warning(
                self,
                "Brak kont",
                "Nie masz przypisanego żadnego konta e-mail.\n"
                "Administrator może je przypisać w Ustawienia → Konta e-mail → "
                "Dostęp pracowników.",
            )

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.start_background_sync)
        self._timer.start(AUTO_SYNC_INTERVAL_MS)
        self._assignment_timer = QTimer(self)
        self._assignment_timer.setInterval(15000)
        self._assignment_timer.timeout.connect(self._refresh_chips)
        self._assignment_timer.start()

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
        if self._sync_cancel.is_set():
            return
        if self._sync_running:
            self._resync_pending = True
            return
        if time.monotonic() - self._last_account_check > 30:
            try:
                accounts = get_email_accounts_for_employee(self.pracownik_id)
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

    def _on_sync_failed(self, message):
        self._last_sync_error = message
        self.sync_status.setText("Błąd synchronizacji · ponowna próba co 2 min")
        self.sync_status.setToolTip(message)

    def _on_sync_progress(self, data) -> None:
        label, done, total, folder, headers = data
        elapsed = int(time.monotonic() - getattr(self, "_sync_started_at", time.monotonic()))
        where = f" – {clean_folder_label(decode_mailbox_utf7(folder))}" if folder else ""
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

    def _on_sync_finished(self, ok: bool) -> None:
        self._headers_refresh_timer.stop()
        self._sync_running = False
        if ok:
            self.sync_status.setText(f"Ostatnia synchronizacja: {datetime.now():%H:%M:%S} · co 2 min")
            self.sync_status.setToolTip("")
        self._on_headers_ready()
        self._refresh_chips()
        if self._resync_pending and not self._sync_cancel.is_set():
            self.start_background_sync()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout()
        self.setLayout(main_layout)

        # Toolbar 1
        toolbar = QHBoxLayout()
        btn_sync = QPushButton("Synchronizacja wiadomości")
        btn_sync.clicked.connect(self.on_manual_sync)
        toolbar.addWidget(btn_sync)
        self.sync_status = QLabel("Automatyczna synchronizacja co 2 min")

        btn_refresh = QPushButton("Odśwież widok")
        btn_refresh.clicked.connect(self.refresh_all)
        toolbar.addWidget(btn_refresh)
        self.btn_bulk_mail = QPushButton('Mail masowy')
        self.btn_bulk_mail.clicked.connect(self._open_bulk_mail)
        toolbar.addWidget(self.btn_bulk_mail)
        self.btn_contacts = QPushButton('Kontakty')
        self.btn_contacts.clicked.connect(self._open_address_book)
        toolbar.addWidget(self.btn_contacts)
        toolbar.addWidget(self.sync_status)

        btn_filters = QToolButton()
        btn_filters.setText("Filtry")
        btn_filters.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        filters_menu = QMenu(btn_filters)
        filters_menu.addAction("Importuj filtry z Thunderbirda…").triggered.connect(self.on_import_filters)
        self.apply_filters_action = filters_menu.addAction("Zastosuj filtry do bieżącego folderu")
        self.apply_filters_action.triggered.connect(self.on_apply_filters)
        filters_menu.aboutToShow.connect(self._update_apply_filters_action)
        self._update_apply_filters_action()
        btn_filters.setMenu(filters_menu)
        toolbar.addWidget(btn_filters)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Szukaj w temacie, nadawcy lub odbiorcy...")
        self.search_input.setClearButtonEnabled(True)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self._search_mail)
        self.search_input.textChanged.connect(self._queue_search)
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
        self.mail_list = MailTreeView(ROLE_ROW_ID)

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
        self.labels_scroll = QScrollArea()
        self.labels_scroll.setWidgetResizable(True)
        self.labels_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.labels_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.labels_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.labels_scroll.setMinimumHeight(60)
        self.labels_scroll.setMaximumHeight(180)
        self.labels_scroll.setWidget(self.chips_frame)
        left_layout.addWidget(self.labels_scroll)
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
            "QTreeView::item { padding: 5px 6px; }"
            "QTreeView::item:selected { background: palette(highlight); color: palette(highlighted-text); }"
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
        self.older_messages = QPushButton("Wczytaj starsze wiadomości")
        self.older_messages.setVisible(False)
        self.older_messages.clicked.connect(lambda: self._load_mail_async(more=True))
        right_layout.addWidget(self.older_messages)

        splitter.addWidget(right_container)

        # Message content pane
        message_container = QFrame()
        message_layout = QVBoxLayout(message_container)
        message_layout.setContentsMargins(0, 0, 0, 0)
        message_layout.addLayout(mail_toolbar)

        self.mail_view = MailBrowser()
        if hasattr(self.mail_view, 'address_clicked'):
            self.mail_view.address_clicked.connect(self._on_address_clicked)
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
                logger.exception("Nie można odświeżyć folderów; zachowano bieżący widok")
                return

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
            self.show_hidden_folders,
            tuple((e["id"], e.get("imie"), e.get("nazwisko")) for e in self.employees),
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
                    acc.get("email"),
                    tuple(
                        (f.get("id"), f["server_name"], f.get("attributes"), f.get("delimiter"), f.get("display_name"))
                        for f in folders
                    ),
                )
                for acc, folders, unread in data
            ),
        )

        if signature == self._tree_signature:
            self._update_folder_unread(data)
            return

        self._tree_signature = signature

        employees = self.employees

        previously_expanded = self._expanded_folder_keys()
        had_tree = self.folder_tree.topLevelItemCount() > 0
        tree_state = ItemViewState(self.folder_tree, lambda index: self._folder_key(
            self.folder_tree.itemFromIndex(index)))

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

        def subtree_folder_ids(node):
            folder = node.get("folder")
            ids = [folder["id"]] if folder and folder.get("id") is not None else []
            for child in node["children"]:
                ids.extend(subtree_folder_ids(child))
            return ids

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
            item.setData(0, ROLE_FOLDER_LABEL, label + (" (ukryty)" if hidden else ""))
            item.setData(0, ROLE_FOLDER_IDS, subtree_folder_ids(node))

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

        if had_tree:
            tree_state.restore()
        self.folder_tree.blockSignals(False)

    def _update_folder_unread(self, data):
        unread = {account["id"]: counts for account, _folders, counts in data}
        def walk(item):
            label = item.data(0, ROLE_FOLDER_LABEL)
            if label is not None:
                counts = unread.get(item.data(0, ROLE_ACCOUNT), {})
                count = sum(counts.get(folder_id, 0) for folder_id in item.data(0, ROLE_FOLDER_IDS) or [])
                text = f"{label} ({count})" if count else label
                if item.text(0) != text:
                    item.setText(0, text)
                font = item.font(0)
                if font.bold() != bool(count):
                    font.setBold(bool(count))
                    item.setFont(0, font)
            for row in range(item.childCount()):
                walk(item.child(row))
        for row in range(self.folder_tree.topLevelItemCount()):
            walk(self.folder_tree.topLevelItem(row))

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
        self._load_mail_async()
        self.start_background_sync()

    def _style_mail_item(self, item: QTreeWidgetItem, unread: bool) -> None:
        for col in range(item.columnCount()):
            font = item.font(col)
            font.setBold(bool(unread))
            item.setFont(col, font)

    def _folder_roles(self, konto_id: int, *, connection=None) -> Dict[str, Optional[str]]:
        try:
            return {
                f["server_name"]: folder_role(f["server_name"], f.get("attributes"), f.get("delimiter"))
                for f in get_folders_for_account(konto_id, connection=connection)
            }
        except Exception:
            return {}

    def _queue_search(self):
        self._cancel_mail_load()
        self._search_timer.start()

    def _search_mail(self):
        if self.current_folder_local:
            self._load_mail_async()

    def _mail_request(self, local_folder=None):
        # Assignment/completion filters are account-wide. Opening a result in
        # another folder changes the message target, not the list being paged.
        folder = None if self.employee_filter is not None or self.completed_filter else (local_folder or self.current_folder_local)
        return (self.current_account["id"], folder,
                self.search_input.text().strip(), self.employee_filter, self.completed_filter)

    def _fetch_mail_data(self, request, before=None, existing=(), through=None, ensure_row=None):
        account, folder, query, employee, completed = request
        ensure_ids = [ensure_row["id"]] if ensure_row and ensure_row.get("konto_id") == account else []
        with mail_read_snapshot() as connection:
            rows, more, cursor = get_mail_page(account, folder, phrase=query, before=before,
                                              employee_id=employee, completed=completed,
                                              include_ids=ensure_ids, connection=connection)
            if existing:
                # Both ranges must describe the same cut. Mixing an earlier
                # page with a later range can resurrect rows retired in between.
                tail = through or min((row.get("data") or "", row["id"]) for row in existing)
                extra_ids = [row["id"] for row in existing if (row.get("data") or "", row["id"]) < tail]
                retained, retained_more, retained_cursor = get_mail_page(
                    account, folder, phrase=query, employee_id=employee,
                    completed=completed, through=tail, include_ids=extra_ids, connection=connection,
                )
                if retained_cursor is not None and (cursor is None or retained_cursor < cursor):
                    more = retained_more
                    cursor = retained_cursor
                rows = [*rows, *retained]
            rows = sorted({row["id"]: row for row in rows}.values(),
                          key=lambda row: (row.get("data") or "", row["id"]), reverse=True)
            ids = [row["id"] for row in rows]
            return (rows, more, cursor, self._folder_roles(account, connection=connection),
                    get_assignment_map(ids, connection=connection), get_completed_map(ids, connection=connection))

    def _cancel_mail_load(self):
        pending, self._pending_mail_load = self._pending_mail_load, None
        self._list_jobs.invalidate()
        return pending

    def _read_state_changed(self):
        self._mail_presentation = None
        pending = self._cancel_mail_load()
        if pending:
            # Preserve a requested older page when restarting the stale read.
            self._load_mail_async(more=pending[1], keep_view=True)
        else:
            self.older_messages.setEnabled(True)

    def _mail_paging_requested(self, request, *, more=False, keep_view=False):
        if request != self._mail_context:
            return False
        pending = self._pending_mail_load
        # A preserving refresh supersedes the snapshot, not the user's pending
        # page request. Scope changes and explicit resets must start afresh.
        return more or bool(keep_view and pending and pending[0] == request and pending[1])

    def _load_mail_async(self, *, more=False, keep_view=False):
        if not self.current_account or not self.current_folder_local:
            return
        request = self._mail_request()
        paging = self._mail_paging_requested(request, more=more, keep_view=keep_view)
        existing = tuple(self._mail_rows) if request == self._mail_context and (paging or keep_view) else ()
        through = self._mail_cursor if existing else None
        if not keep_view and not paging:
            self._load_token += 1
            self._loading_message_key = None
            self.cached_message = None
            self.current_uid = None
            self._current_message_row_id = None
            self._current_message_account_id = None
            self.mail_list.clear()
            self._mail_rows = []
            # The cursor belongs to displayed rows. Keeping it after replacing
            # the view lets a failed reload's retry skip the recent messages.
            self._mail_cursor = None
            self._mail_context = None
            self.older_messages.hide()
            self.unread_map.clear()
            self.assign_map = {}
            self.completed_map = set()
            self._clear_view()
        cursor = self._mail_cursor if paging else None
        pending = (request, paging)
        self._pending_mail_load = pending
        self.older_messages.setEnabled(False)
        def loaded(payload):
            if self._pending_mail_load is not pending:
                return
            self._pending_mail_load = None
            self.older_messages.setEnabled(True)
            if not self.current_account or request != self._mail_request():
                return
            self.load_mail_list_from_db(self.current_folder_local, keep_view=keep_view or paging, _payload=payload)
        def failed(exc):
            if self._pending_mail_load is not pending:
                return
            self._pending_mail_load = None
            self.older_messages.setEnabled(True)
            logger.warning("Nie można wczytać poczty: %s", exc)
        self._list_jobs.submit(lambda: self._fetch_mail_data(request, cursor, existing, through), loaded, failed)

    def load_mail_list_from_db(
            self,
            local_folder: str,
            keep_view: bool = False,
            ensure_row: Optional[Dict[str, Any]] = None,
            _payload=None,
    ) -> None:
        view_state = ItemViewState(self.mail_list, lambda index: index.data(ROLE_ROW_ID)) if keep_view else None
        selected_ids = {
            it.data(0, ROLE_ROW_ID)
            for it in self.mail_list.selectedItems()
        } if keep_view else set()

        current_item = self.mail_list.currentItem() if keep_view else None
        restore_id = (current_item.data(0, ROLE_ROW_ID) if current_item is not None
                      else getattr(self, "_current_message_row_id", None)) if keep_view else None
        if view_state is not None and view_state.current is None:
            view_state.current = restore_id

        self.mail_list.blockSignals(True)
        if not keep_view:
            self.mail_list.clear()

        if not keep_view:
            self._clear_view()
            self.cached_message = None

        if not self.current_account:
            self.mail_list.clear()
            self.unread_map.clear()
            self.assign_map = {}
            self.mail_list.blockSignals(False)
            return

        konto_id = self.current_account["id"]
        request = self._mail_request(local_folder)
        if _payload is None:
            paging = self._mail_paging_requested(request, keep_view=keep_view)
            self._cancel_mail_load()
            try:
                existing = tuple(self._mail_rows) if request == self._mail_context else ()
                _payload = self._fetch_mail_data(request, before=self._mail_cursor if paging else None, existing=existing,
                                                 through=self._mail_cursor if existing else None, ensure_row=ensure_row)
            except Exception:
                logger.exception("Nie można wczytać listy wiadomości")
                _payload = ([], False, None, {}, {}, set())
        rows, more, cursor, roles, assignments, completed = _payload
        presentation = (dict(roles), tuple((e["id"], employee_tag(e)) for e in self.employees), dict(self.label_numbers))
        unchanged = (keep_view and request == self._mail_context and rows == self._mail_rows
                     and assignments == self.assign_map and completed == self.completed_map
                     and presentation == getattr(self, "_mail_presentation", None)
                     and len(self.mail_list.model().items) == len(rows))
        self.assign_map, self.completed_map = assignments, completed
        self._mail_presentation = presentation
        self._mail_cursor = cursor
        self.older_messages.setEnabled(True)
        self.older_messages.setVisible(more)
        if unchanged:
            if current_item is None and restore_id is not None:
                view_state.restore()
            self._mail_rows = list(rows)
            self.mail_list.blockSignals(False)
            self._refresh_chips()
            return
        self.unread_map.clear()
        current_row_id = getattr(self, "_current_message_row_id", None)
        if keep_view and current_row_id is not None and current_row_id not in {row["id"] for row in rows}:
            # A reused UID is a different message. Never restore its predecessor's
            # preview or let an older body-loading callback render over it.
            self._load_token += 1
            self._loading_message_key = None
            self.current_uid = None
            self._current_message_row_id = None
            self._current_message_account_id = None
            self.cached_message = None
            self._clear_view()
        self._mail_rows = list(rows)
        self._mail_cursor = cursor
        self._mail_context = request
        self.older_messages.setEnabled(True)
        self.older_messages.setVisible(more)

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

            self.unread_map[r["id"]] = is_unread

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

            item = MailItem([
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
                    restore_id is not None
                    and r["id"] == restore_id
            ):
                restore = item

            if r["id"] in selected_ids:
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

        heads = []
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
                                ROLE_ROW_ID,
                            )
                        )
                        for m in members
                ):
                    self._style_mail_item(
                        head,
                        True,
                    )

            heads.append(head)

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

        self.mail_list.apply_items(heads)
        restore = self.mail_list.resolve(restore)
        to_select = [self.mail_list.resolve(item) for item in to_select]
        if restore is not None:
            self.mail_list.setCurrentItem(
                restore, 0, QItemSelectionModel.SelectionFlag.NoUpdate
            )

        for it in to_select:
            it.setSelected(True)

        if keep_view:
            view_state.restore()

        self.mail_list.blockSignals(False)

        self._refresh_chips()

    # ------------------------------------------------------------------
    # Etykiety pracowników nad listą
    # ------------------------------------------------------------------
    def _refresh_chips(self) -> None:
        try:
            unread_counts = count_unread_assigned_by_employee(self.current_account["id"]) if self.current_account else {}
        except Exception:
            logger.exception("Nie można pobrać liczników przypisanej poczty")
            unread_counts = {}
        signature = (
            tuple((e["id"], employee_name(e), self.label_numbers.get(e["id"])) for e in self.employees),
        )
        if signature == self._chips_signature:
            self._update_chips(unread_counts)
            return
        self._chips_signature = signature
        self._chips_buttons = {}
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
                f" text-align: left; color: palette(text); background: transparent; border-left: 3px solid {color}; }}"
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

        self._chips_buttons["all"] = chip(
            "Wszystkie", "#555555",
            self.employee_filter is None and not self.completed_filter,
            lambda _=False: self._on_chip_clicked(None),
        )
        self._chips_buttons["completed"] = chip(
            f"{COMPLETED_LABEL_NUMBER} Zrobione", label_color(COMPLETED_LABEL_NUMBER),
            self.completed_filter,
            lambda _=False: self._on_completed_chip_clicked(),
            f"Klawisz {COMPLETED_LABEL_NUMBER} oznacza zaznaczone wiadomości jako zrobione; "
            "kliknij, aby je wyświetlić",
        )
        for emp in self.employees:
            number = self.label_numbers.get(emp["id"])
            self._chips_buttons[emp["id"]] = chip(
                f"{employee_name(emp)} ({unread_counts.get(emp['id'], 0)})",
                self._employee_color(emp["id"]),
                not self.completed_filter and self.employee_filter == emp["id"],
                lambda _=False, e=emp["id"]: self._on_chip_clicked(e),
                f"Nieprzeczytane przypisane wiadomości na tym koncie: {unread_counts.get(emp['id'], 0)}. "
                + (f"Klawisz {number} przypisuje zaznaczone wiadomości" if number else ""),
            )

    def _update_chips(self, unread_counts):
        buttons = self._chips_buttons
        buttons["all"].setChecked(self.employee_filter is None and not self.completed_filter)
        buttons["completed"].setChecked(self.completed_filter)
        for emp in self.employees:
            number = self.label_numbers.get(emp["id"])
            count = unread_counts.get(emp["id"], 0)
            button = buttons[emp["id"]]
            button.setText(f"{employee_name(emp)} ({count})")
            button.setChecked(not self.completed_filter and self.employee_filter == emp["id"])
            button.setToolTip(f"Nieprzeczytane przypisane wiadomości na tym koncie: {count}. "
                              + (f"Klawisz {number} przypisuje zaznaczone wiadomości" if number else ""))

    def _on_chip_clicked(self, employee_id: Optional[int]) -> None:
        self.employee_filter = employee_id
        self.completed_filter = False
        self.current_uid = None
        if self.current_folder_local:
            self.load_mail_list_from_db(self.current_folder_local)

    def open_assignment_notification(self, target):
        return self._open_notification(target, resolve_assigned_message)

    def open_message_notification(self, target):
        return self._open_notification(target, resolve_notification_message)

    def _open_notification(self, target, resolver):
        try:
            accounts = get_email_accounts_for_employee(self.pracownik_id)
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
        runner = self._message_jobs if isinstance(work, LatestRead) else self._command_jobs
        runner.submit(work, lambda result: done(result, None), lambda exc: done(None, exc))

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
            if items[0].data(0, ROLE_ROW_ID) != getattr(self, "_current_message_row_id", None) or self.cached_message is None:
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
        any_unread = any(self.unread_map.get(item.data(0, ROLE_ROW_ID), False) for item, _, _ in refs)
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
        remote_refs = []
        for item, folder, uid in refs:
            try:
                row_id = item.data(0, ROLE_ROW_ID)
                if update_message_read_flag(konto_id, uid, read, get_folder_id(konto_id, folder), expected_row_id=row_id):
                    remote_refs.append((folder, uid, row_id))
                    self.unread_map[row_id] = not read
                    self._style_mail_item(item, not read)
            except Exception:
                logger.warning("Nie można zapisać flagi przeczytania", exc_info=True)
        if remote_refs:
            changed_ids = {row_id for _, _, row_id in remote_refs}
            for row in self._mail_rows:
                if row["id"] in changed_ids:
                    row["przeczytana"] = int(read)
            self._read_state_changed()
        self.load_folders_from_db()
        self._refresh_chips()

        def work():
            remote = mark_read_remote if read else mark_unread_remote
            return [remote(konto_id, folder, uid, expected_row_id=row_id) for folder, uid, row_id in remote_refs]

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
            item.data(0, ROLE_ROW_ID),
        )

        # Nie uruchamiaj ponownie pobierania tej samej wiadomości,
        # jeśli poprzednie pobieranie nadal trwa.
        if self._loading_message_key == load_key:
            return

        self.current_uid = uid
        self.current_folder_local = local_folder
        self.cached_message = None

        row_id = item.data(0, ROLE_ROW_ID)
        self._current_message_row_id = row_id
        self._current_message_account_id = konto_id

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

        # Stary podgląd zostaje do czasu nadejścia nowej treści – kolejne setHtml("")
        # i "Pobieranie…" powodowały czarne mignięcie na starszych komputerach.
        self.mail_view.resources = {}
        self.att_bar.hide()
        self.att_list.hide()
        self.att_list.clear()
        QTimer.singleShot(
            400,
            lambda: self._loading_message_key == load_key
            and token == self._load_token
            and self.mail_view.setPlainText("Pobieranie wiadomości…"),
        )

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
                row_id,
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
                LatestRead(lambda: load_message(
                    konto_id,
                    local_folder,
                    uid,
                    expected_row_id=row_id,
                )),
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

    def _mark_opened_as_read(self, row_id: int, konto_id: int, folder: str, uid: str) -> None:
        if row_id is None or not self.unread_map.get(row_id, False):
            return
        try:
            if not update_message_read_flag(konto_id, uid, True, get_folder_id(konto_id, folder), expected_row_id=row_id):
                self._load_mail_async()
                return
        except Exception:
            logger.warning("Nie można zapisać flagi przeczytania", exc_info=True)
            return
        self.unread_map[row_id] = False
        for row in self._mail_rows:
            if row["id"] == row_id:
                row["przeczytana"] = 1
                break
        # A retained-page refresh may have replaced the clicked Qt item while
        # its body was loading. Resolve the current item from the captured ID.
        for current in self.mail_list.all_items():
            if current.data(0, ROLE_ROW_ID) == row_id:
                self._style_mail_item(current, False)
                break
        self._read_state_changed()
        self.load_folders_from_db()
        self._refresh_chips()
        self._run_async(lambda: mark_read_remote(konto_id, folder, uid, expected_row_id=row_id), lambda res, err: None)

    def _render_message(self, msg: email.message.Message) -> None:
        self.cached_message = msg
        self.mail_view.contact_links = {}
        from app.email.address_book import normalize_email
        def addresses(raw):
            links = []
            for name, address in getaddresses([str(raw or '')]):
                try:
                    address = normalize_email(address)
                except ValueError:
                    continue
                name = safe_header(name)
                token = uuid4().hex
                self.mail_view.contact_links[token] = (name, address)
                label = f'{name} <{address}>' if name else address
                links.append(f'<a href="biuro-contact:{token}" title="Opcje adresu i dodawanie kontaktu">{html_escape(label)}</a>')
            return ', '.join(links) or html_escape(str(raw or ''))
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
            f"Od: {addresses(msg.get('From'))}<br>"
            f"Do: {addresses(msg.get('To'))}<br>"
            + (f"Kopia: {addresses(msg.get('Cc'))}<br>" if msg.get('Cc') else '')
            + (f"Odpowiedź do: {addresses(msg.get('Reply-To'))}<br>" if msg.get('Reply-To') else '')
            + f"Data: {html_escape(str(msg.get('Date') or ''))}<br>"
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
        self.mail_view.contact_links = {}
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
            return safe_attachment_name(part.get_filename())

        risky = [name_of(p) for _, p in chosen if is_risky_attachment(name_of(p))]
        if risky:
            answer = QMessageBox.warning(
                self,
                "Potencjalnie niebezpieczny załącznik",
                "Ten typ pliku może uruchamiać kod:\n\n" + "\n".join(risky[:10])
                + "\n\nZapisz tylko, jeśli ufasz nadawcy. Kontynuować?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

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
        is_pdf = part is not None and self._is_safe_pdf_part(part)
        if is_pdf and self._open_attachment_externally(part):
            return
        self.save_attachments([index])

    @staticmethod
    def _is_safe_pdf_part(part) -> bool:
        """PDF tylko gdy MIME, ostatnie rozszerzenie i zawartość (%PDF-) są zgodne."""
        if part.get_content_type() != "application/pdf":
            return False
        name = os.path.basename(str(part.get_filename() or "")).strip().lower()
        if not name.endswith(".pdf"):
            return False
        payload = part.get_payload(decode=True)
        return bool(payload) and len(payload) <= 50 * 1024 * 1024 and payload.startswith(b"%PDF-")

    def _open_attachment_externally(self, part) -> bool:
        # Nazwa pliku od nadawcy nigdy nie trafia na dysk – zawsze stała nazwa .pdf.
        if not self._is_safe_pdf_part(part):
            return False
        directory = tempfile.mkdtemp(prefix="biuro_zal_")
        path = os.path.join(directory, "zalacznik.pdf")
        if not self._write_attachment(part, path):
            shutil.rmtree(directory, ignore_errors=True)
            return False
        QTimer.singleShot(
            10 * 60 * 1000, lambda: shutil.rmtree(directory, ignore_errors=True)
        )
        return QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def on_manual_sync(self) -> None:
        self._last_account_check = 0.0
        self.start_background_sync()

    def _open_bulk_mail(self):
        from app.email.bulk_mail_dialog import BulkMailDialog
        try:
            accounts = get_email_accounts_for_employee(self.pracownik_id)
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

    def refresh_all(self) -> None:
        self._reload_employees()
        self.load_folders_from_db()
        if self.current_folder_local:
            self._load_mail_async(keep_view=True)

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

    def _target_row_id(self, item, folder, uid):
        if item is not None:
            return item.data(0, ROLE_ROW_ID)
        if (self.current_account and self.current_account["id"] == getattr(self, "_current_message_account_id", None)
                and folder == self.current_folder_local and str(uid) == str(self.current_uid)):
            return getattr(self, "_current_message_row_id", None)
        return None

    def _targets(self) -> List[Tuple[Optional[QTreeWidgetItem], str, str]]:
        refs = self._selected_refs()
        if not refs and self.current_uid and self.current_folder_local and self._target_row_id(None, self.current_folder_local, self.current_uid):
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
        expected = {}
        for item, folder, uid in refs:
            row_id = self._target_row_id(item, folder, uid)
            if row_id is None:
                return
            expected.setdefault(folder, {})[str(uid)] = row_id
            by_folder.setdefault(folder, []).append(uid)

        def work():
            for folder, uids in by_folder.items():
                move_emails(konto_id, folder, uids, target, expected_rows=expected[folder])

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
        self.folder_visibility_changed.emit()
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

    def _filter_target_folder(self) -> str:
        return self.current_folder_local or "INBOX"

    def _update_apply_filters_action(self) -> None:
        label = clean_folder_label(decode_mailbox_utf7(self._filter_target_folder()))
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
