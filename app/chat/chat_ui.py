# app/chat/chat_ui.py
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QTextEdit, QLineEdit, QMessageBox,
    QScrollArea, QSizePolicy, QFrame, QDialog, QDialogButtonBox, QFormLayout
)

from PyQt6.QtGui import QFont, QColor
from PyQt6.QtCore import Qt, QDateTime, pyqtSignal
from typing import Optional

from app.chat.chat_model import (
    get_conversations, get_messages, add_message, create_conversation,
    get_conversation, get_participant_ids, get_active_employees,
    update_conversation, can_manage_conversation, is_participant,
    get_direct_conversations,
)

import sqlite3


class ChatMessageEdit(QTextEdit):
    send_requested = pyqtSignal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
            else:
                self.send_requested.emit()
            return
        super().keyPressEvent(event)


def _format_date_header(dt_str: str) -> str:
    """
    Format date string from DB to 'YYYY-MM-DD'. Accepts common SQLite datetime formats.
    """
    if not dt_str:
        return ""
    dt = QDateTime.fromString(dt_str, "yyyy-MM-dd HH:mm:ss")
    if not dt.isValid():
        dt = QDateTime.fromString(dt_str, Qt.DateFormat.ISODate)
    if not dt.isValid():
        # fallback: take date part
        return dt_str.split(" ")[0]
    return dt.toString("yyyy-MM-dd")


class ChatPage(QWidget):
    def __init__(self, pracownik_id: int):
        super().__init__()
        self.pracownik_id = pracownik_id
        self.current_konwersacja: Optional[int] = None
        self.offset = 0
        self.page_size = 100
        self.has_more_history = True
        self.loading_history = False
        self.current_pracownik_id = pracownik_id  # alias used in rendering

        self._build_ui()
        self.refresh_chat_conversations()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        title = QLabel("Czat wewnętrzny")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        layout.addWidget(title)

        # LEFT: conversations list
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Konwersacje:"))

        self.chat_conversations = QListWidget()
        self.chat_conversations.itemSelectionChanged.connect(self.on_chat_selected)
        left_layout.addWidget(self.chat_conversations)

        btn_new = QPushButton("➕ Nowa konwersacja")
        btn_new.clicked.connect(self.create_new_conversation)
        left_layout.addWidget(btn_new)

        self.btn_manage = QPushButton("Zarządzaj dostępem")
        self.btn_manage.setEnabled(False)
        self.btn_manage.clicked.connect(self.manage_access)
        left_layout.addWidget(self.btn_manage)

        # RIGHT: messages area with scroll
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.messages_container = QWidget()
        self.messages_layout = QVBoxLayout(self.messages_container)
        self.messages_layout.setContentsMargins(6, 6, 6, 6)
        self.messages_layout.setSpacing(6)
        self.messages_container.setLayout(self.messages_layout)
        self.scroll_area.setWidget(self.messages_container)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)
        right_layout.addWidget(self.scroll_area)

        bottom = QHBoxLayout()
        self.chat_input = ChatMessageEdit()
        self.chat_input.setAcceptRichText(False)
        self.chat_input.setFixedHeight(70)
        self.chat_input.setPlaceholderText("Napisz wiadomość...")
        self.chat_input.send_requested.connect(self.send_message)
        bottom.addWidget(self.chat_input)

        btn_send = QPushButton("Wyślij")
        btn_send.clicked.connect(self.send_message)
        bottom.addWidget(btn_send)

        right_layout.addLayout(bottom)

        # Layout split
        container = QHBoxLayout()
        container.addWidget(left, 1)
        container.addWidget(right, 3)
        layout.addLayout(container)

    # ============================================================
    # CONVERSATIONS
    # ============================================================
    def refresh_chat_conversations(self, select_id: Optional[int] = None):
        self.chat_conversations.blockSignals(True)
        self.chat_conversations.clear()
        rows = [r for r in get_conversations(self.pracownik_id) if r.get("typ") != "prywatna"]
        directs = get_direct_conversations(self.pracownik_id)
        rows = rows + directs

        def header(text):
            h = QListWidgetItem(text)
            h.setFlags(Qt.ItemFlag.NoItemFlags)
            f = h.font()
            f.setBold(True)
            f.setPointSize(max(f.pointSize() - 1, 7))
            h.setFont(f)
            h.setForeground(QColor("#607d8b"))
            self.chat_conversations.addItem(h)

        def add_item(r, prefix):
            nazwa = r.get("nazwa") or f"Konwersacja {r.get('id')}"
            item = QListWidgetItem(prefix + nazwa)
            item.setData(Qt.ItemDataRole.UserRole, r.get("id"))
            self.chat_conversations.addItem(item)
            if select_id is not None and r.get("id") == select_id:
                self.chat_conversations.setCurrentItem(item)

        header("KANAŁY")
        for r in rows[:len(rows) - len(directs)]:
            add_item(r, "# ")
        header("ROZMOWY")
        for r in directs:
            add_item(r, "● ")
        self.chat_conversations.blockSignals(False)
        if select_id is not None and self.chat_conversations.currentItem():
            self.on_chat_selected()
        elif self.current_konwersacja not in [r.get("id") for r in rows]:
            self.current_konwersacja = None
            self._clear_messages()
            self.btn_manage.setEnabled(False)

    def on_chat_selected(self):
        item = self.chat_conversations.currentItem()
        if not item:
            return
        self.current_konwersacja = item.data(Qt.ItemDataRole.UserRole)
        self.btn_manage.setEnabled(can_manage_conversation(self.current_konwersacja, self.pracownik_id))
        self.offset = 0
        self.has_more_history = True
        self.load_messages()

    # ============================================================
    # MESSAGES LOADING & RENDERING (grouped by sender + date)
    # ============================================================
    def load_messages(self, scroll_to_bottom: bool = True):
        if not self.current_konwersacja:
            return

        messages = get_messages(
            self.current_konwersacja,
            limit=self.offset + self.page_size,
            offset=0,
        )
        self.has_more_history = len(messages) >= self.offset + self.page_size
        # messages are returned oldest-first by dao; render grouped
        self._render_grouped_messages(messages, scroll_to_bottom=scroll_to_bottom)

    def load_more_history(self):
        if self.loading_history or not self.has_more_history:
            return
        self.loading_history = True
        old_value = self.scroll_area.verticalScrollBar().value()
        old_maximum = self.scroll_area.verticalScrollBar().maximum()
        self.offset += self.page_size
        self.load_messages(scroll_to_bottom=False)
        new_maximum = self.scroll_area.verticalScrollBar().maximum()
        self.scroll_area.verticalScrollBar().setValue(
            old_value + new_maximum - old_maximum
        )
        self.loading_history = False

    def _on_scroll_changed(self, value: int):
        if value <= 20:
            self.load_more_history()

    def _clear_messages(self):
        while self.messages_layout.count():
            item = self.messages_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _render_grouped_messages(self, messages, scroll_to_bottom: bool = True):
        """
        Render messages grouped by date and sender.
        Consecutive messages from same sender on same date are shown under one header.
        """
        self._clear_messages()

        last_sender_id = None
        last_date = None
        group_layout = None

        for msg in messages:
            sender_id = msg.get("pracownik_id")
            sender_name = (msg.get("imie") or "") + ((" " + msg.get("nazwisko")) if msg.get("nazwisko") else "")
            timestamp = msg.get("data_wysylki") or ""
            date_header = _format_date_header(timestamp)

            # New date header if date changed
            if date_header != last_date:
                date_label = QLabel(date_header)
                date_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                date_label.setStyleSheet("color: #666; font-weight: bold; margin: 8px 0;")
                self.messages_layout.addWidget(date_label)
                last_date = date_header
                # reset sender grouping on date change
                last_sender_id = None
                group_layout = None

            # New group if sender changed
            if sender_id != last_sender_id:
                # create group frame
                group_frame = QFrame()
                group_frame.setFrameShape(QFrame.Shape.NoFrame)
                group_vlayout = QVBoxLayout()
                group_vlayout.setContentsMargins(6, 4, 6, 8)
                group_vlayout.setSpacing(4)
                group_frame.setLayout(group_vlayout)

                # header with sender name and optional time of first message in group
                time_str = ""
                try:
                    dt = QDateTime.fromString(timestamp, "yyyy-MM-dd HH:mm:ss")
                    if not dt.isValid():
                        dt = QDateTime.fromString(timestamp, Qt.DateFormat.ISODate)
                    if dt.isValid():
                        time_str = dt.toString("HH:mm")
                except Exception:
                    time_str = ""

                header_text = f"{sender_name}" + (f"  {time_str}" if time_str else "")
                header = QLabel(header_text)
                header.setStyleSheet("font-weight: 600; color: #222; margin-bottom: 4px;")
                group_vlayout.addWidget(header)

                self.messages_layout.addWidget(group_frame)
                group_layout = group_vlayout

                last_sender_id = sender_id

            # message bubble
            text_label = QLabel(msg.get("tresc") or "")
            text_label.setWordWrap(True)
            text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            text_label.setStyleSheet("""
                background: #f1f1f1;
                border-radius: 8px;
                padding: 8px;
                margin-bottom: 4px;
                max-width: 600px;
            """)

            # alignment wrapper
            wrapper = QHBoxLayout()
            if sender_id == self.current_pracownik_id:
                wrapper.addStretch()
                wrapper.addWidget(text_label)
            else:
                wrapper.addWidget(text_label)
                wrapper.addStretch()

            group_layout.addLayout(wrapper)

        # stretch at the end to push content up if few messages
        self.messages_layout.addStretch()
        if scroll_to_bottom:
            self.scroll_area.verticalScrollBar().setValue(
                self.scroll_area.verticalScrollBar().maximum()
            )

    # ============================================================
    # SENDING MESSAGES
    # ============================================================
    def send_message(self):
        text = self.chat_input.toPlainText().strip()
        if not text:
            return

        if not self.current_konwersacja:
            QMessageBox.warning(self, "Brak konwersacji", "Wybierz lub utwórz konwersację przed wysłaniem wiadomości.")
            return

        if not is_participant(self.current_konwersacja, self.pracownik_id):
            QMessageBox.warning(self, "Brak dostępu", "Nie masz dostępu do tego kanału.")
            return

        try:
            add_message(self.current_konwersacja, self.pracownik_id, text)
        except Exception as e:
            QMessageBox.critical(self, "Błąd zapisu", f"Nie udało się wysłać wiadomości: {e}")
            return

        self.chat_input.clear()
        # reload messages (keeps grouping)
        self.load_messages()

    # ============================================================
    # CREATE NEW CONVERSATION
    # ============================================================
    def create_new_conversation(self):
        dlg = ChannelAccessDialog(self, self.pracownik_id)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            new_id = create_conversation(dlg.nazwa(), self.pracownik_id, dlg.selected_ids())
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się utworzyć konwersacji: {e}")
            return
        self.refresh_chat_conversations(select_id=new_id)

    def manage_access(self):
        if not self.current_konwersacja:
            return
        conv = get_conversation(self.current_konwersacja)
        if not conv or not can_manage_conversation(self.current_konwersacja, self.pracownik_id):
            QMessageBox.warning(self, "Brak uprawnień", "Tylko właściciel kanału lub administrator może zarządzać dostępem.")
            return
        dlg = ChannelAccessDialog(
            self, self.pracownik_id, nazwa=conv["nazwa"],
            selected=get_participant_ids(self.current_konwersacja),
            locked_id=conv["pracownik_creator_id"],
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            update_conversation(self.current_konwersacja, dlg.nazwa(), dlg.selected_ids(), self.pracownik_id)
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Nie udało się zapisać dostępu: {e}")
            return
        self.refresh_chat_conversations(select_id=self.current_konwersacja)


class ChannelAccessDialog(QDialog):
    """Nazwa kanału + wybór aktywnych pracowników, którzy mają do niego dostęp."""

    def __init__(self, parent, pracownik_id: int, nazwa: str = "",
                 selected=None, locked_id: Optional[int] = None):
        super().__init__(parent)
        self.setWindowTitle("Dostęp do kanału" if nazwa else "Nowa konwersacja")
        self.resize(420, 460)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.nazwa_field = QLineEdit(nazwa)
        form.addRow("Nazwa:", self.nazwa_field)
        layout.addLayout(form)
        layout.addWidget(QLabel("Pracownicy z dostępem do kanału:"))

        self.employees = QListWidget()
        selected = set(selected or [])
        locked_id = locked_id or pracownik_id
        for emp in get_active_employees():
            item = QListWidgetItem(f"{emp['imie']} {emp['nazwisko']}")
            item.setData(Qt.ItemDataRole.UserRole, emp["id"])
            flags = item.flags() | Qt.ItemFlag.ItemIsUserCheckable
            checked = emp["id"] in selected or emp["id"] == locked_id
            if emp["id"] == locked_id:
                flags &= ~Qt.ItemFlag.ItemIsEnabled
                item.setText(item.text() + " (właściciel)")
            item.setFlags(flags)
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self.employees.addItem(item)
        layout.addWidget(self.employees)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Zapisz")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Anuluj")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        if not self.nazwa():
            QMessageBox.warning(self, "Błąd", "Wpisz nazwę konwersacji")
            return
        self.accept()

    def nazwa(self) -> str:
        return self.nazwa_field.text().strip()

    def selected_ids(self):
        return [
            self.employees.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.employees.count())
            if self.employees.item(i).checkState() == Qt.CheckState.Checked
        ]