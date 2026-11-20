"""Lista wiadomości i podgląd — jedyne części strony poczty odświeżane przy pracy.


Powłoka (pasek narzędzi, drzewo folderów, etykiety) żyje w `mail_shell`; tutaj są
te panele, których zawartość zmienia się przy każdej synchronizacji."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)
from app.email.mail_constants import ROLE_MSGID, ROLE_REFS
from app.email.mail_helpers import build_folder_tree, display_names, employee_tag, format_list_date, group_threads

from PyQt6.QtCore import QItemSelectionModel
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QColor
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QFileDialog
from PyQt6.QtWidgets import QListWidgetItem
from PyQt6.QtWidgets import QMenu
from PyQt6.QtWidgets import QMessageBox
from PyQt6.QtWidgets import QTreeWidgetItem
from app.email.email_actions import load_message
from app.email.email_helpers import attachment_name
from app.email.email_helpers import attachment_save_path
from app.email.email_helpers import extract_html
from app.email.email_helpers import extract_text
from app.email.email_helpers import folder_role
from app.email.email_helpers import get_attachments
from app.email.email_helpers import inline_resources
from app.email.email_helpers import is_risky_attachment
from app.email.email_helpers import iter_message_attachments
from app.email.email_helpers import safe_header
from app.email.email_helpers import viewable_attachment_extension
from app.email.email_labels import label_color
from app.email.email_storage import find_special_folder
from app.email.email_storage import get_assignment_map
from app.email.email_storage import get_completed_map
from app.email.email_storage import get_folder_id
from app.email.email_storage import get_folders_for_account
from app.email.email_storage import get_mail_page
from app.email.email_storage import mail_read_snapshot
from app.email.email_storage import set_employee_assignment
from app.email.email_storage import set_messages_completed
from app.email.email_storage import update_message_read_flag
from app.email.email_sync import mark_read_remote
from app.email.email_sync import mark_unread_remote
from app.email.email_sync import move_emails
from app.email.mail_model import MailItem
from app.utils.background import LatestRead
from app.utils.view_state import ItemViewState
from email.utils import getaddresses
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import json
import logging
import email
from html import escape as html_escape
import time

from PyQt6.QtWidgets import (QAbstractItemView, QButtonGroup, QFrame, QHBoxLayout,
                             QHeaderView, QLabel, QListWidget, QPushButton, QStyle,
                             QToolButton, QVBoxLayout, QWidget)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QKeySequence, QShortcut

from app.email.email_labels import COMPLETED_LABEL_NUMBER
from app.email.mail_constants import MAX_NUMBER_KEYS, ROLE_ROW_ID
from app.email.mail_folder_tree import FolderTree
from app.email.mail_model import MailTreeView
from app.email.mail_view import MailBrowser



class MailArea:
    """Lista wiadomości i podgląd. `attach(page)` buduje panele i wpina je w rozdzielacz."""

    def _build(self):
        page = self.page
        left = QFrame()
        left_layout = QVBoxLayout(left)
        # Etykiety pracowników i ukrywanie folderów: logika w powłoce, widgety tutaj.
        left_layout.setContentsMargins(0, 2, 2, 0)
        left_layout.setSpacing(2)
        labels_title = QLabel("Etykiety")
        labels_title.setStyleSheet("font-weight: bold; padding: 3px 6px;")
        left_layout.addWidget(labels_title)
        page.chips_frame = QFrame()
        page.chips_layout = QVBoxLayout(page.chips_frame)
        page.chips_layout.setContentsMargins(4, 0, 4, 4)
        page.chips_layout.setSpacing(1)
        page.chips_group = QButtonGroup(page)
        page.chips_group.setExclusive(True)
        left_layout.addWidget(page.chips_frame)
        page.show_hidden_button = QPushButton("Pokaż ukryte foldery")
        page.show_hidden_button.setCheckable(True)
        page.show_hidden_button.toggled.connect(page._toggle_show_hidden_folders)
        left_layout.addWidget(page.show_hidden_button)
        left_layout.addWidget(self.folder_tree, 1)
        self._left = left

        page._mail_toolbar = QHBoxLayout()
        page.btn_new = page._create_mail_action(
            "Nowa wiadomość", QStyle.StandardPixmap.SP_FileIcon, page.on_new_message)
        page.btn_reply = page._create_mail_action(
            "Odpowiedz", QStyle.StandardPixmap.SP_ArrowBack, page.on_reply)
        page.btn_forward = page._create_mail_action(
            "Przekaż", QStyle.StandardPixmap.SP_ArrowForward, page.on_forward)
        page.btn_archive = page._create_mail_action(
            "Archiwizuj", QStyle.StandardPixmap.SP_DriveHDIcon, page.on_archive)
        page.btn_spam = page._create_mail_action(
            "Niechciane", QStyle.StandardPixmap.SP_MessageBoxWarning, page.on_spam)
        page.btn_delete = page._create_mail_action(
            "Usuń", QStyle.StandardPixmap.SP_TrashIcon, page.on_delete)
        page.btn_more = page._create_mail_action(
            "Więcej", QStyle.StandardPixmap.SP_TitleBarUnshadeButton, page.on_more)
        for button in (page.btn_new, page.btn_reply, page.btn_forward, page.btn_archive,
                       page.btn_spam, page.btn_delete, page.btn_more):
            page._mail_toolbar.addWidget(button)

        middle = QWidget()
        middle_layout = QVBoxLayout(middle)
        middle_layout.setContentsMargins(0, 0, 0, 0)
        page.mail_list = MailTreeView(ROLE_ROW_ID)
        page.mail_list.setColumnCount(3)
        page.mail_list.setHeaderLabels(["Temat", "Korespondent", "Data"])
        page.mail_list.setRootIsDecorated(True)
        page.mail_list.setUniformRowHeights(True)
        page.mail_list.setAlternatingRowColors(True)
        page.mail_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        page.mail_list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        page.mail_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        page.mail_list.setDragEnabled(True)
        page.mail_list.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        page.mail_list.setDefaultDropAction(Qt.DropAction.CopyAction)
        for number in range(1, MAX_NUMBER_KEYS + 1):
            shortcut = QShortcut(QKeySequence(str(number)), page.mail_list)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            if number == COMPLETED_LABEL_NUMBER:
                shortcut.activated.connect(page._toggle_completed_by_number)
            else:
                shortcut.activated.connect(lambda n=number: page._toggle_employee_by_number(n))
        page.mail_list.setStyleSheet(
            "QTreeView::item { padding: 5px 6px; }"
            "QTreeView::item:selected { background: palette(highlight); color: palette(highlighted-text); }"
        )
        header = page.mail_list.header()
        header.setStretchLastSection(False)
        header.setSectionsMovable(True)
        for column in range(3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate(page._saved_column_widths()):
            page.mail_list.setColumnWidth(column, width)
        page._columns_timer = QTimer(page)
        page._columns_timer.setSingleShot(True)
        page._columns_timer.setInterval(600)
        page._columns_timer.timeout.connect(page._save_column_widths)
        header.sectionResized.connect(lambda *_: page._columns_timer.start())
        page.mail_list.itemSelectionChanged.connect(page._select_timer.start)
        page.mail_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        page.mail_list.customContextMenuRequested.connect(page._on_mail_context_menu)
        middle_layout.addWidget(page.mail_list)
        page.older_messages = QPushButton("Wczytaj starsze wiadomości")
        page.older_messages.setVisible(False)
        page.older_messages.clicked.connect(lambda: page._load_mail_async(more=True))
        middle_layout.addWidget(page.older_messages)

        preview = QFrame()
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.addLayout(page._mail_toolbar)
        page.mail_view = MailBrowser()
        if hasattr(page.mail_view, "address_clicked"):
            page.mail_view.address_clicked.connect(page._on_address_clicked)
        page.mail_view.setPlainText("Wybierz wiadomość z listy.")
        preview_layout.addWidget(page.mail_view, 1)
        page.att_list = QListWidget()
        page.att_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        page.att_list.itemDoubleClicked.connect(page.on_attachment_item_activated)
        page.att_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        page.att_list.customContextMenuRequested.connect(page._on_att_list_menu)
        page.att_list.hide()
        preview_layout.addWidget(page.att_list)
        page.att_bar = QFrame()
        page.att_bar.setFrameShape(QFrame.Shape.StyledPanel)
        bar = QHBoxLayout(page.att_bar)
        bar.setContentsMargins(6, 3, 6, 3)
        page.att_toggle = QToolButton()
        page.att_toggle.setText(">")
        page.att_toggle.setAutoRaise(True)
        page.att_toggle.clicked.connect(page._toggle_att_list)
        bar.addWidget(page.att_toggle)
        page.att_summary = QLabel("")
        page.att_summary.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        page.att_summary.customContextMenuRequested.connect(page._on_att_summary_menu)
        bar.addWidget(page.att_summary, 1)
        page.att_save = QPushButton("Zapisz")
        page.att_save.setToolTip("Zapisz załączniki")
        page.att_save.clicked.connect(page.on_save_attachments_clicked)
        bar.addWidget(page.att_save)
        page.att_bar.hide()
        preview_layout.addWidget(page.att_bar)

        self._middle, self._preview = middle, preview

    def attach(self, page, splitter):
        """Buduje panele listy i podglądu (raz) i wstawia je do rozdzielacza powłoki."""
        if getattr(self, "_built", False):
            return
        self.page = page
        self.folder_tree = FolderTree(page)
        self._build()
        self._built = True
        splitter.addWidget(self._left)
        splitter.addWidget(self._middle)
        splitter.addWidget(self._preview)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 3)
        splitter.setSizes([260, 880, 700])
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
                        _label_background(color),
                    )
                    item.setForeground(
                        column,
                        _label_foreground(),
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
                            _label_background(color),
                        )
                        item.setForeground(
                            column,
                            _label_foreground(),
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

    def _fetch_mail_data(self, request, before=None, existing=(), through=None, ensure_row=None):
        account, folder, query, employee, completed = request
        from app.remote.state import client
        if client() is not None:
            from app.remote.screen_data import mail_view_snapshot
            return mail_view_snapshot(account,folder,query,employee,completed,before,existing,through,ensure_row)
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

    def _mail_request(self, local_folder=None):
        # Assignment/completion filters are account-wide. Opening a result in
        # another folder changes the message target, not the list being paged.
        folder = None if self.employee_filter is not None or self.completed_filter else (local_folder or self.current_folder_local)
        return (self.current_account["id"], folder,
                self.search_input.text().strip(), self.employee_filter, self.completed_filter)

    def _mail_paging_requested(self, request, *, more=False, keep_view=False):
        if request != self._mail_context:
            return False
        pending = self._pending_mail_load
        # A preserving refresh supersedes the snapshot, not the user's pending
        # page request. Scope changes and explicit resets must start afresh.
        return more or bool(keep_view and pending and pending[0] == request and pending[1])

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

    def _queue_search(self):
        self._cancel_mail_load()
        self._search_timer.start()

    def _search_mail(self):
        if self.current_folder_local:
            self._load_mail_async()

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
        request = self._mail_request()

        def is_current() -> bool:
            return (token == self._load_token
                    and self.current_account is not None
                    and self.current_account["id"] == konto_id
                    and self.current_folder_local == local_folder
                    and str(self.current_uid) == str(uid)
                    and self._current_message_row_id == row_id
                    and self._current_message_account_id == konto_id
                    and self._mail_request() == request)

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
            and is_current()
            and self.mail_view.setPlainText("Pobieranie wiadomości…"),
        )

        def show(msg) -> None:
            if not is_current():
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
            if not is_current():
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
        # Clearing the preview is also a context/selection reset. A body read
        # accepted before this reset must never repopulate it afterward.
        self._load_token += 1
        self._message_jobs.invalidate()
        self._loading_message_key = None
        self.current_uid = None
        self._current_message_row_id = None
        self._current_message_account_id = None
        self.cached_message = None
        self.mail_view.contact_links = {}
        self.mail_view.clear()
        self.mail_view.resources = {}
        self.att_bar.hide()
        self.att_list.hide()
        self.att_list.clear()

    def _style_mail_item(self, item: QTreeWidgetItem, unread: bool) -> None:
        for col in range(item.columnCount()):
            font = item.font(col)
            font.setBold(bool(unread))
            item.setFont(col, font)

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

    def _targets(self) -> List[Tuple[Optional[QTreeWidgetItem], str, str]]:
        refs = self._selected_refs()
        if not refs and self.current_uid and self.current_folder_local and self._target_row_id(None, self.current_folder_local, self.current_uid):
            refs = [(None, self.current_folder_local, self.current_uid)]
        return refs

    def _target_row_id(self, item, folder, uid):
        if item is not None:
            return item.data(0, ROLE_ROW_ID)
        if (self.current_account and self.current_account["id"] == getattr(self, "_current_message_account_id", None)
                and folder == self.current_folder_local and str(uid) == str(self.current_uid)):
            return getattr(self, "_current_message_row_id", None)
        return None

    def _role_of(self, folder: str) -> Optional[str]:
        return self._folder_roles(self.current_account["id"]).get(folder) if self.current_account else None

    def _current_folder_role(self) -> Optional[str]:
        return self._role_of(self.current_folder_local) if self.current_folder_local else None

    def _folder_roles(self, konto_id: int, *, connection=None) -> Dict[str, Optional[str]]:
        try:
            return {
                f["server_name"]: folder_role(f["server_name"], f.get("attributes"), f.get("delimiter"))
                for f in get_folders_for_account(konto_id, connection=connection)
            }
        except Exception:
            return {}

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
            return attachment_name(part.get_filename(), part.get_payload(decode=True))

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
            path = attachment_save_path(path, part.get_payload(decode=True))
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
        if part is not None and self._open_attachment_externally(part):
            return
        self.save_attachments([index])

    @staticmethod
    def _is_safe_pdf_part(part) -> bool:
        """Otwieramy PDF rozpoznany po nagłówku; nie ufamy samemu rozszerzeniu/MIME."""
        return viewable_attachment_extension(part.get_filename(),part.get_payload(decode=True)) == '.pdf'

    def _open_attachment_externally(self, part) -> bool:
        # Fixed local name and content-derived extension; no executable is launched.
        extension = viewable_attachment_extension(part.get_filename(),part.get_payload(decode=True))
        if extension is None:
            return False
        directory = tempfile.mkdtemp(prefix="biuro_zal_")
        path = os.path.join(directory, "zalacznik" + extension)
        if not self._write_attachment(part, path):
            shutil.rmtree(directory, ignore_errors=True)
            return False
        QTimer.singleShot(
            10 * 60 * 1000, lambda: shutil.rmtree(directory, ignore_errors=True)
        )
        return QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def _label_background(color):
    """Tło etykiety pracownika: jasne w jasnym motywie, przygaszone w ciemnym."""
    from app.preferences.state_colors import is_dark
    return QColor(color).darker(140) if is_dark() else QColor(color).lighter(185)


def _label_foreground():
    """Tekst etykiety czytelny w obu motywach (na jasnym tle ciemny, na ciemnym jasny)."""
    from app.preferences.state_colors import is_dark
    return QColor('#e5eaf2') if is_dark() else QColor('#202020')
