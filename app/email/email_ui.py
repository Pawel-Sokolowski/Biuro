# app/email/email_page.py
from __future__ import annotations

import re
import email
from collections import Counter
from typing import List, Dict, Any, Optional, Tuple

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QTreeWidget, QTreeWidgetItem, QMessageBox, QSplitter, QPushButton,
    QLabel, QFrame, QSizePolicy, QDialog, QDialogButtonBox, QTextEdit,
    QToolButton, QStyle
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from app.email.email_storage import (
    get_email_accounts_for_employee,
    get_folders_for_account,
    refresh_email_list,
    search_messages,
)

from app.email.email_actions import (
    list_folders_from_imap_and_store,
    fetch_headers_from_imap_and_store,
    fetch_message_from_imap_and_store,
    mark_read_remote,
    mark_unread_remote,
    delete_email_remote,
    move_email_and_get_sender,
    create_folder,
    delete_folder,
    sync_all_emails,
    send_email_smtp,
    send_batch_emails,
    denormalize_folder_name,
    normalize_folder_name,
    load_message,
    extract_html,
    get_attachments,
    open_attachment,
)

ARCHIVE_FOLDER = "INBOX.Archive"
SPAM_FOLDER = "INBOX.spam"
TRASH_FOLDER = "INBOX.Trash"


class EmailPage(QWidget):
    def __init__(self, pracownik_id: int):
        super().__init__()
        self.pracownik_id = pracownik_id
        self.accounts = get_email_accounts_for_employee(pracownik_id)
        self.current_account: Optional[Dict[str, Any]] = None
        self.current_folder_local: Optional[str] = None
        self.current_uid: Optional[str] = None
        self.cached_message: Optional[email.message.Message] = None
        self.unread_map: Dict[str, bool] = {}

        self._build_ui()

        if self.accounts:
            self.current_account = self.accounts[0]
            try:
                list_folders_from_imap_and_store(self.current_account["id"])
            except Exception:
                pass
            self.load_folders_from_db()
        else:
            QMessageBox.warning(self, "Brak kont", "Nie znaleziono żadnych kont email.")

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

        toolbar.addStretch()
        main_layout.addLayout(toolbar)

        # Message actions, arranged like a mail client toolbar.
        mail_toolbar = QHBoxLayout()

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
            self.btn_reply,
            self.btn_forward,
            self.btn_archive,
            self.btn_spam,
            self.btn_delete,
            self.btn_more,
        ):
            mail_toolbar.addWidget(button)

        mail_toolbar.addStretch()
        main_layout.addLayout(mail_toolbar)

        # Splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # Folder tree
        self.folder_tree = QTreeWidget()
        self.folder_tree.setHeaderLabel("Foldery")
        self.folder_tree.itemClicked.connect(self.on_folder_clicked)
        splitter.addWidget(self.folder_tree)

        # Middle panel
        right_container = QFrame()
        right_layout = QVBoxLayout()
        right_container.setLayout(right_layout)

        header = QFrame()
        h_layout = QHBoxLayout()
        header.setLayout(h_layout)

        lbl_att = QLabel("")
        lbl_att.setFixedWidth(20)
        h_layout.addWidget(lbl_att)

        lbl_thread = QLabel("")
        lbl_thread.setFixedWidth(20)
        h_layout.addWidget(lbl_thread)

        lbl_subject = QLabel("Temat")
        lbl_subject.setStyleSheet("font-weight: bold;")
        h_layout.addWidget(lbl_subject, 3)

        lbl_dot = QLabel("")
        lbl_dot.setFixedWidth(16)
        h_layout.addWidget(lbl_dot)

        lbl_from = QLabel("Korespondenci")
        lbl_from.setStyleSheet("font-weight: bold;")
        lbl_from.setFixedWidth(220)
        h_layout.addWidget(lbl_from, 2)

        lbl_date = QLabel("Data")
        lbl_date.setStyleSheet("font-weight: bold;")
        lbl_date.setFixedWidth(140)
        h_layout.addWidget(lbl_date)

        right_layout.addWidget(header)

        self.mail_list = QListWidget()
        self.mail_list.itemClicked.connect(self.on_mail_clicked)
        right_layout.addWidget(self.mail_list)

        splitter.addWidget(right_container)

        # Message view
        self.btn_download_message = QPushButton("Pobierz pełną wiadomość z serwera")
        self.btn_download_message.setEnabled(False)
        self.btn_download_message.clicked.connect(self.on_download_current_message)
        right_layout.addWidget(self.btn_download_message)

        self.mail_view = QTextEdit()
        self.mail_view.setReadOnly(True)
        splitter.addWidget(self.mail_view)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 3)

    def _create_mail_action(self, text: str, icon_name, callback) -> QToolButton:
        button = QToolButton(self)
        button.setText(text)
        button.setIcon(self.style().standardIcon(icon_name))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setAutoRaise(True)
        button.setToolTip(text)
        button.clicked.connect(callback)
        return button
    def normalize_name(self, s: str) -> str:
        if not s:
            return ""
        s = re.sub(r'\s+', ' ', s).strip()
        return s.replace('\x00', '').strip()

    def folder_sort_key(self, display_label: str) -> Tuple[int, int, str]:
        label = (display_label or "").strip()
        low = label.lower()
        main_order = {"odebrane": 0, "wyslane": 1, "spam": 2, "archiwum": 3}
        if low in main_order:
            return (0, main_order[low], "")
        for n in range(1, 5):
            prefix = f"{n} "
            if low.startswith(prefix):
                return (1, n, low)
        return (2, 0, low)

    def load_folders_from_db(self) -> None:
        self.folder_tree.clear()
        if not self.current_account:
            return

        root = QTreeWidgetItem([self.current_account["email"]])
        self.folder_tree.addTopLevelItem(root)

        try:
            folders = get_folders_for_account(self.current_account["id"])
        except Exception:
            folders = []

        mains = ["Odebrane", "Wysłane", "Spam", "Archiwum"]
        for m in reversed(mains):
            if m in folders:
                folders.remove(m)
        for m in reversed(mains):
            folders.insert(0, m)

        numbered = [f for f in folders if re.match(r'^[1-4]\b', f)]
        others = [f for f in folders if f not in mains and f not in numbered]
        others_sorted = sorted(others, key=lambda n: self.folder_sort_key(n))
        final = mains + numbered + others_sorted

        nodes: Dict[str, QTreeWidgetItem] = {}
        for f in final:
            parts = [p.strip() for p in f.split(".") if p.strip()]
            parent = root
            path_segments: List[str] = []
            for part in parts:
                path_segments.append(part)
                server_path = ".".join(path_segments)
                display = self.normalize_name(part)
                if server_path not in nodes:
                    item = QTreeWidgetItem([display])
                    item.setData(0, Qt.ItemDataRole.UserRole, server_path)
                    parent.addChild(item)
                    nodes[server_path] = item
                parent = nodes[server_path]

        self.folder_tree.expandAll()

    def on_folder_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        local_name = item.data(0, Qt.ItemDataRole.UserRole)
        if not local_name:
            return
        self.current_folder_local = local_name
        self.load_mail_list_from_db(local_name)

    def create_message_widget(self, subject: str, sender: str, date: str,
                              has_attachment: bool, has_thread: bool, is_unread: bool) -> QFrame:
        w = QFrame()
        h = QHBoxLayout()
        h.setContentsMargins(6, 4, 6, 4)
        h.setSpacing(8)

        att_label = QLabel("📎" if has_attachment else "")
        att_label.setFixedWidth(20)
        h.addWidget(att_label)

        thread_btn = QPushButton("⤴" if has_thread else "")
        thread_btn.setFlat(True)
        thread_btn.setFixedWidth(20)
        thread_btn.setObjectName("thread_btn")
        h.addWidget(thread_btn)

        subj_label = QLabel(subject or "(brak tematu)")
        subj_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        h.addWidget(subj_label, 3)

        dot_btn = QPushButton("●" if is_unread else "○")
        dot_btn.setFlat(True)
        dot_btn.setFixedWidth(16)
        dot_btn.setObjectName("dot_btn")
        dot_btn.setStyleSheet("color: #1E90FF;" if is_unread else "color: #888888;")
        h.addWidget(dot_btn)

        sender_label = QLabel(sender)
        sender_label.setFixedWidth(220)
        h.addWidget(sender_label, 2)

        date_label = QLabel(date)
        date_label.setFixedWidth(140)
        h.addWidget(date_label)

        font = subj_label.font()
        font.setBold(bool(is_unread))
        subj_label.setFont(font)

        w.setLayout(h)
        return w

    def load_mail_list_from_db(self, local_folder: str) -> None:
        self.mail_list.clear()
        self.mail_view.clear()
        self.cached_message = None
        self.unread_map.clear()

        if not self.current_account:
            return

        konto_id = self.current_account["id"]

        try:
            rows = refresh_email_list(konto_id, local_folder)
        except Exception:
            rows = []

        subject_counts = Counter(
            (r.get("temat") or "").strip().casefold()
            for r in rows
            if (r.get("temat") or "").strip()
        )

        for r in rows:
            uid = r.get("uid")
            sender = r.get("nadawca") or ""
            subject = r.get("temat") or ""
            date = r.get("data") or ""
            is_unread = not bool(r.get("przeczytana"))
            self.unread_map[uid] = is_unread

            has_thread = subject_counts.get(subject.strip().casefold(), 0) > 1
            # Full MIME bodies are intentionally not loaded while rendering
            # the list. They are fetched only when the user opens/downloads a
            # message.
            has_attachment = False

            widget = self.create_message_widget(subject, sender, date, has_attachment, has_thread, is_unread)
            item = QListWidgetItem()
            item.setSizeHint(widget.sizeHint())
            item.setData(Qt.ItemDataRole.UserRole, uid)
            item.setData(Qt.ItemDataRole.UserRole + 1, local_folder)
            self.mail_list.addItem(item)
            self.mail_list.setItemWidget(item, widget)

            thread_btn = widget.findChild(QPushButton, "thread_btn")
            dot_btn = widget.findChild(QPushButton, "dot_btn")

            if thread_btn:
                thread_btn.clicked.connect(lambda _, u=uid, s=subject, lf=local_folder: self.on_thread_clicked(u, s, lf))
            if dot_btn:
                dot_btn.clicked.connect(lambda _, u=uid, it=item, lf=local_folder: self.on_toggle_read(u, it, lf))
    def on_thread_clicked(self, uid: str, subject: str, local_folder: str) -> None:
        konto_id = self.current_account["id"]
        try:
            results = search_messages(konto_id, local_folder, subject or "")
        except Exception:
            results = []

        texts = []
        for r in results:
            texts.append(
                f"UID {r.get('uid')}\nFrom: {r.get('od') or r.get('nadawca')}\n"
                f"Subject: {r.get('temat')}\nDate: {r.get('data')}\n"
            )

        dlg = QDialog(self)
        dlg.setWindowTitle("Konwersacja")
        dlg.resize(700, 400)
        v = QVBoxLayout()
        te = QTextEdit()
        te.setReadOnly(True)
        te.setPlainText("\n\n".join(texts) if texts else "Brak powiązanych wiadomości.")
        v.addWidget(te)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        dlg.setLayout(v)
        dlg.exec()

    def on_toggle_read(self, uid: str, item: QListWidgetItem, local_folder: str) -> None:
        konto_id = self.current_account["id"]
        server_folder = denormalize_folder_name(local_folder)
        is_unread = self.unread_map.get(uid, False)

        try:
            if is_unread:
                ok = mark_read_remote(konto_id, server_folder, uid)
            else:
                ok = mark_unread_remote(konto_id, server_folder, uid)
        except Exception:
            ok = False

        if ok:
            self.unread_map[uid] = not is_unread
            self.update_item_widget(item, uid)

    def update_item_widget(self, item: QListWidgetItem, uid: str) -> None:
        widget = self.mail_list.itemWidget(item)
        if not widget:
            return

        dot_btn = widget.findChild(QPushButton, "dot_btn")
        labels = widget.findChildren(QLabel)
        is_unread = self.unread_map.get(uid, False)

        if dot_btn:
            dot_btn.setText("●" if is_unread else "○")
            dot_btn.setStyleSheet("color: #1E90FF;" if is_unread else "color: #888888;")

        if labels and len(labels) >= 3:
            subj = labels[2]
            f = subj.font()
            f.setBold(bool(is_unread))
            subj.setFont(f)

    def on_mail_clicked(self, item: QListWidgetItem) -> None:
        uid = item.data(Qt.ItemDataRole.UserRole)
        local_folder = item.data(Qt.ItemDataRole.UserRole + 1)

        if not uid or not local_folder or not self.current_account:
            return

        self.current_uid = uid
        self.cached_message = None
        self.btn_download_message.setEnabled(False)
        konto_id = self.current_account["id"]

        try:
            msg = load_message(konto_id, local_folder, uid)
        except Exception:
            msg = None

        if not msg:
            self.btn_download_message.setEnabled(True)
            self.mail_view.setPlainText(
                "Ta wiadomość nie jest przechowywana lokalnie.\n"
                "Kliknij przycisk powyżej, aby pobrać jej pełną treść z serwera."
            )
            return

        self._render_message(msg)

        if self.unread_map.get(uid, False):
            try:
                mark_read_remote(konto_id, denormalize_folder_name(local_folder), uid)
            except Exception:
                pass
            self.unread_map[uid] = False
            self.update_item_widget(item, uid)

    def on_download_current_message(self) -> None:
        if not self.current_account or not self.current_uid or not self.current_folder_local:
            return

        konto_id = self.current_account["id"]
        server_folder = denormalize_folder_name(self.current_folder_local)
        self.btn_download_message.setEnabled(False)
        try:
            if not fetch_message_from_imap_and_store(konto_id, server_folder, self.current_uid):
                raise RuntimeError("Serwer nie zwrócił pełnej wiadomości.")
            msg = load_message(konto_id, self.current_folder_local, self.current_uid)
            if not msg:
                raise RuntimeError("Nie udało się odczytać pobranej wiadomości.")
            self._render_message(msg)
        except Exception as exc:
            self.btn_download_message.setEnabled(True)
            QMessageBox.warning(self, "IMAP", f"Nie można pobrać treści wiadomości:\n{exc}")

    def _render_message(self, msg: email.message.Message) -> None:
        self.cached_message = msg
        html = extract_html(msg)

        if html:
            self.mail_view.setHtml(html)
        else:
            text = ""
            try:
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            text = part.get_payload(decode=True).decode(
                                part.get_content_charset() or "utf-8",
                                errors="replace"
                            )
                            break
                else:
                    if msg.get_content_type() == "text/plain":
                        text = msg.get_payload(decode=True).decode(
                            msg.get_content_charset() or "utf-8",
                            errors="replace"
                        )
            except Exception:
                text = ""
            self.mail_view.setPlainText(text)

        attachments = get_attachments(msg)
        self.show_attachments(attachments)

    def show_attachments(self, attachments: List[Dict[str, Any]]) -> None:
        if not attachments:
            self.mail_view.append("\n\nBrak załączników.")
            return

        self.mail_view.append("\n\n<b>Załączniki:</b>")
        for att in attachments:
            fname = att["filename"]
            idx = att["index"]
            self.mail_view.append(f'<a href="attachment:{idx}">{fname}</a>')

    def on_attachment_clicked(self, url) -> None:
        href = url.toString()
        if not href.startswith("attachment:"):
            return

        idx = int(href.split(":")[1])

        if not self.current_account or not self.current_folder_local or not self.current_uid:
            return

        konto_id = self.current_account["id"]

        try:
            path = open_attachment(
                konto_id,
                self.current_folder_local,
                self.current_uid,
                idx
            )
            if path:
                QMessageBox.information(self, "Załącznik", f"Załącznik zapisano: {path}")
            else:
                QMessageBox.warning(self, "Załącznik", "Nie można pobrać załącznika.")
        except Exception:
            QMessageBox.warning(self, "Załącznik", "Nie można pobrać załącznika.")

    def on_manual_sync(self) -> None:
        if not self.current_account:
            return

        konto_id = self.current_account["id"]

        try:
            if self.current_folder_local:
                server_folder = denormalize_folder_name(self.current_folder_local)
                try:
                    fetch_headers_from_imap_and_store(konto_id, server_folder)
                except Exception:
                    pass

            try:
                list_folders_from_imap_and_store(konto_id)
            except Exception:
                pass

            self.load_folders_from_db()

            if self.current_folder_local:
                self.load_mail_list_from_db(self.current_folder_local)

            QMessageBox.information(self, "Synchronizacja", "Synchronizacja zakończona.")
        except Exception as e:
            QMessageBox.warning(self, "Synchronizacja", f"Błąd synchronizacji:\n{e}")

    def refresh_all(self) -> None:
        if self.current_folder_local:
            self.load_mail_list_from_db(self.current_folder_local)

    def on_reply(self) -> None:
        if not self.current_account or not self.current_uid or not self.current_folder_local:
            return

        konto_id = self.current_account["id"]

        try:
            send_email_smtp(
                konto_id=konto_id,
                reply_to_uid=self.current_uid,
                folder=self.current_folder_local,
            )
            QMessageBox.information(self, "Email", "Wysłano odpowiedź.")
        except Exception as e:
            QMessageBox.warning(self, "Email", f"Nie udało się wysłać odpowiedzi:\n{e}")

    def on_forward(self) -> None:
        if not self.current_account or not self.current_uid or not self.current_folder_local:
            return

        konto_id = self.current_account["id"]

        try:
            send_email_smtp(
                konto_id=konto_id,
                forward_uid=self.current_uid,
                folder=self.current_folder_local,
            )
            QMessageBox.information(self, "Email", "Wiadomość przekazana.")
        except Exception as e:
            QMessageBox.warning(self, "Email", f"Nie udało się przekazać wiadomości:\n{e}")

    def on_archive(self) -> None:
        if not self.current_account or not self.current_uid or not self.current_folder_local:
            return

        konto_id = self.current_account["id"]

        try:
            server_folder = denormalize_folder_name(self.current_folder_local)
        except Exception:
            server_folder = self.current_folder_local

        try:
            move_email_and_get_sender(
                konto_id,
                server_folder,
                self.current_uid,
                ARCHIVE_FOLDER,
            )
            self.load_mail_list_from_db(self.current_folder_local)
        except Exception as e:
            QMessageBox.warning(self, "Archiwizuj", f"Nie udało się przenieść do archiwum:\n{e}")

    def on_spam(self) -> None:
        if not self.current_account or not self.current_uid or not self.current_folder_local:
            return

        konto_id = self.current_account["id"]

        try:
            server_folder = denormalize_folder_name(self.current_folder_local)
        except Exception:
            server_folder = self.current_folder_local

        try:
            move_email_and_get_sender(
                konto_id,
                server_folder,
                self.current_uid,
                SPAM_FOLDER,
            )
            self.load_mail_list_from_db(self.current_folder_local)
        except Exception as e:
            QMessageBox.warning(self, "Niechciane", f"Nie udało się przenieść do spamu:\n{e}")

    def on_delete(self) -> None:
        if not self.current_account or not self.current_uid or not self.current_folder_local:
            return

        konto_id = self.current_account["id"]

        try:
            server_folder = denormalize_folder_name(self.current_folder_local)
        except Exception:
            server_folder = self.current_folder_local

        try:
            move_email_and_get_sender(
                konto_id,
                server_folder,
                self.current_uid,
                TRASH_FOLDER,
            )
            self.load_mail_list_from_db(self.current_folder_local)
        except Exception as e:
            QMessageBox.warning(self, "Usuń", f"Nie udało się przenieść do kosza:\n{e}")

    def on_more(self) -> None:
        QMessageBox.information(self, "Więcej", "Dodatkowe opcje w przygotowaniu.")

    def closeEvent(self, event) -> None:
        super().closeEvent(event)
