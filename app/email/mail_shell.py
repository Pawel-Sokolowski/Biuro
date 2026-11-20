"""Stała powłoka strony poczty: pasek narzędzi, drzewo folderów, etykiety.

Budowana raz w konstruktorze. Odświeżanie podmienia treść drzewa i etykiet
(bez przebudowy widgetów); listę wiadomości i podgląd trzyma `mail_area`.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from app.email.mail_constants import (DEFAULT_COLUMN_WIDTHS, ROLE_ACCOUNT, ROLE_DATA, ROLE_FOLDER,
                                     ROLE_FOLDER_IDS, ROLE_FOLDER_LABEL)
from app.email.mail_helpers import build_folder_tree, clean_folder_label, employee_label, employee_name

from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QInputDialog
from PyQt6.QtWidgets import QMessageBox
from PyQt6.QtWidgets import QSizePolicy
from PyQt6.QtWidgets import QTreeWidgetItem
from app.db.dao_app_settings import set_setting
from app.email.email_helpers import SPECIAL_FOLDER_LABELS
from app.email.email_helpers import decode_mailbox_utf7
from app.email.email_helpers import folder_role
from app.email.email_storage import count_unread_assigned_by_employee
from app.email.email_storage import filter_visible_unread_counts
from app.email.email_storage import get_folders_for_account
from app.email.email_storage import get_unread_counts
from app.email.email_sync import create_folder
from app.email.email_sync import rename_folder
from app.utils.view_state import ItemViewState
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

import json
import logging

from PyQt6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QMenu, QPushButton, QSplitter, QToolButton, QVBoxLayout)
from PyQt6.QtCore import Qt, QTimer

from app.db.dao_app_settings import get_setting
from app.email.email_labels import COMPLETED_LABEL_NUMBER, label_color
from app.email.mail_area import MailArea



class MailShellMixin:
    def _build_ui(self) -> None:
        """Buduje całą stronę: stałą powłokę oraz listę wiadomości z podglądem."""
        self._build_shell()

    def _build_shell(self) -> None:
        """Pasek narzędzi i drzewo folderów — budowane raz, potem tylko odświeżane."""
        main_layout = QVBoxLayout(self)
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
        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self._splitter)
        # Lista wiadomości i podgląd: jedyne elementy zmieniane przy pracy.
        self._mail_area = MailArea()
        self._mail_area.attach(self, self._splitter)
        self.folder_tree = self._mail_area.folder_tree
        self.folder_tree.setColumnCount(2)
        self.folder_tree.setHeaderLabels(["Folder", ""])
        self.folder_tree.setIndentation(16)
        self.folder_tree.setMinimumWidth(240)
        header = self.folder_tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.folder_tree.setHeaderHidden(True)
        self.folder_tree.currentItemChanged.connect(self._on_folder_current_changed)
        self.folder_tree.mails_dropped.connect(self._on_mails_dropped)
        self.folder_tree.folder_dropped.connect(self._on_folder_dropped)
        self.folder_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.folder_tree.customContextMenuRequested.connect(self._on_folder_context_menu)

    def _create_mail_action(self, text: str, icon_name, callback) -> QToolButton:
        button = QToolButton(self)
        button.setText(text)
        button.setIcon(self.style().standardIcon(icon_name))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setAutoRaise(True)
        button.setToolTip(text)
        button.clicked.connect(callback)
        return button
    def _refresh_sidebar_async(self):
        from app.remote.view_reads import mail_sidebar_snapshot
        actor = self.pracownik_id
        def loaded(snapshot):
            previous = self.current_account['id'] if self.current_account else None
            self.accounts = snapshot['accounts']
            self.current_account = next((a for a in self.accounts if a['id']==previous),self.accounts[0] if self.accounts else None)
            self._office_folder_prefix = snapshot['folder_prefix']
            self.employees = snapshot['employees']
            self.label_numbers = snapshot['labels']
            self.employees.sort(key=lambda e:(self.label_numbers.get(e['id'],99),e['id']))
            data = []
            for account in self.accounts:
                key = account['id']
                folders = snapshot['folders'].get(key,[])
                unread = filter_visible_unread_counts(folders,snapshot['unread'].get(key,{}),self.hidden_folders.get(str(key),set()))
                if not any(f['server_name'].upper()=='INBOX' for f in folders):
                    folders = [*folders,{'server_name':'INBOX','display_name':'INBOX','delimiter':None,'attributes':'','id':None}]
                data.append((account,folders,unread))
            if not self.accounts:
                self.folder_tree.clear()
                self.mail_list.clear()
                self._clear_view()
                return
            self._render_folder_snapshot(data)
        def failed(error):
            from app.remote.secrets import safe_error
            message = safe_error(error)
            self.sync_status.setText('Nie udało się wczytać folderów poczty: '+message)
            logger.warning('mail_sidebar_failed error_type=%s message=%s',type(error).__name__,message)
        self._sidebar_reads.submit(lambda:mail_sidebar_snapshot(actor),loaded,failed,key=('mail-sidebar',actor))

    def load_folders_from_db(self) -> None:
        from app.remote.state import client
        if client() is not None:
            self._refresh_sidebar_async()
            return
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

        self._render_folder_snapshot(data)

    def _render_folder_snapshot(self,data):
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
                label = self._clean_folder_label(node["label"])

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
                        self._clean_folder_label(node["label"])
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

    def _clean_folder_label(self, label):
        return clean_folder_label(label, getattr(self, '_office_folder_prefix', ''))

    def _refresh_chips(self) -> None:
        from app.remote.state import client
        if client() is not None:
            from app.utils.background import TaskRunner
            if not hasattr(self,'_chip_reads'):
                self._chip_reads = TaskRunner(self)
            account_id = self.current_account['id'] if self.current_account else None
            def loaded(counts):
                current = self.current_account['id'] if self.current_account else None
                if current == account_id:
                    self._render_chips(counts)
            self._chip_reads.submit(
                lambda: count_unread_assigned_by_employee(account_id) if account_id else {},loaded,
                lambda exc: logger.warning('Nie można odświeżyć liczników poczty: %s',exc),key=('chips',account_id))
            return
        try:
            unread_counts = count_unread_assigned_by_employee(self.current_account["id"]) if self.current_account else {}
        except Exception:
            logger.exception("Nie można pobrać liczników przypisanej poczty")
            unread_counts = {}
        self._render_chips(unread_counts)

    def _render_chips(self,unread_counts):
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
            from app.preferences.state_colors import is_dark
            selected_background = (QColor(color).darker(150) if is_dark()
                                   else QColor(color).lighter(175)).name()
            checked_text = '#e5eaf2' if is_dark() else '#111'
            btn.setStyleSheet(
                f"QPushButton {{ border: 0; border-radius: 3px; padding: 2px 6px;"
                f" text-align: left; color: palette(text); background: transparent; border-left: 3px solid {color}; }}"
                f"QPushButton:checked {{ color: {checked_text}; background: {selected_background}; font-weight: bold; }}"
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

    def _on_completed_chip_clicked(self) -> None:
        self.employee_filter = None
        self.completed_filter = True
        self.current_uid = None
        if self.current_folder_local:
            self.load_mail_list_from_db(self.current_folder_local)

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

    def _hidden_folders_setting_key(self) -> str:
        return f"email_ukryte_foldery_{self.pracownik_id}"

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

    def _folder_menu_label(self, node: Dict[str, Any], under_staff: bool) -> str:
        label = SPECIAL_FOLDER_LABELS.get(node["role"]) if node["role"] else None
        if label is None:
            label = self._clean_folder_label(node["label"])
            if under_staff:
                label = employee_label(label, self.employees)
        return label

    def _fill_move_menu(self, menu: QMenu, nodes: List[Dict[str, Any]], exclude: set,
                        under_staff: bool) -> None:
        # Qt otherwise wraps long menus into columns on Windows. Force vertical
        # scrolling for every level of the destination-folder menu.
        menu.setStyleSheet('QMenu { menu-scrollable: 1; }')
        screen = self.screen()
        menu.setMaximumHeight(min(420, max(120, screen.availableGeometry().height() - 80)) if screen else 420)
        for node in nodes:
            folder = node["folder"]
            selectable = folder is not None and "\\noselect" not in (folder.get("attributes") or "").lower()
            label = self._folder_menu_label(node, under_staff)
            staff = self._clean_folder_label(node["label"]).casefold() == "pracownicy"
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
        label = self._clean_folder_label(decode_mailbox_utf7(dst))
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
