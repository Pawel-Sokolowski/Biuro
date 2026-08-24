# app/chat/chat_ui.py
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QTextEdit, QLineEdit, QMessageBox
)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt
import sqlite3
from app.config import DB_NAME


class ChatPage(QWidget):
    def __init__(self, pracownik_id: int):
        super().__init__()
        self.pracownik_id = pracownik_id
        self.current_konwersacja = None
        self.offset = 0

        self._build_ui()
        self.refresh_chat_conversations()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        title = QLabel("Chat wewnętrzny")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        layout.addWidget(title)

        # LEFT: conversations
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("Konwersacje:"))

        self.chat_conversations = QListWidget()
        self.chat_conversations.itemSelectionChanged.connect(self.on_chat_selected)
        left_layout.addWidget(self.chat_conversations)

        btn_new = QPushButton("➕ Nowa konwersacja")
        btn_new.clicked.connect(self.create_new_conversation)
        left_layout.addWidget(btn_new)

        # RIGHT: messages
        right = QWidget()
        right_layout = QVBoxLayout(right)

        self.chat_messages = QTextEdit()
        self.chat_messages.setReadOnly(True)
        right_layout.addWidget(self.chat_messages)

        btn_history = QPushButton("⬆ Pokaż starsze wiadomości")
        btn_history.clicked.connect(self.load_more_history)
        right_layout.addWidget(btn_history)

        bottom = QHBoxLayout()
        self.chat_input = QLineEdit()
        bottom.addWidget(self.chat_input)

        btn_send = QPushButton("Wyślij")
        btn_send.clicked.connect(self.send_message)
        bottom.addWidget(btn_send)

        right_layout.addLayout(bottom)

        # Layout
        container = QHBoxLayout()
        container.addWidget(left, 1)
        container.addWidget(right, 3)
        layout.addLayout(container)

    # ============================================================
    # LOAD CONVERSATIONS
    # ============================================================
    def refresh_chat_conversations(self):
        self.chat_conversations.clear()
        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()

        cur.execute("""
            SELECT id, nazwa
            FROM konwersacje
            ORDER BY data_utworzenia DESC
        """)

        for konwersacja_id, nazwa in cur.fetchall():
            item = QListWidgetItem(nazwa)
            item.setData(Qt.ItemDataRole.UserRole, konwersacja_id)
            self.chat_conversations.addItem(item)

        conn.close()

    # ============================================================
    # SELECT CONVERSATION
    # ============================================================
    def on_chat_selected(self):
        item = self.chat_conversations.currentItem()
        if not item:
            return

        self.current_konwersacja = item.data(Qt.ItemDataRole.UserRole)
        self.offset = 0
        self.load_messages()

    # ============================================================
    # LOAD MESSAGES
    # ============================================================
    def load_messages(self):
        if not self.current_konwersacja:
            return

        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()

        cur.execute("""
            SELECT w.id, p.imie, p.nazwisko, w.tresc, w.data_wysylki
            FROM wiadomosci_chat w
            JOIN pracownicy p ON p.id = w.pracownik_id
            WHERE w.konwersacja_id = ?
            ORDER BY w.data_wysylki DESC
            LIMIT 100 OFFSET ?
        """, (self.current_konwersacja, self.offset))

        rows = cur.fetchall()
        conn.close()

        html = ""
        for msg_id, imie, nazwisko, tresc, data in rows:
            html += f"<b>{imie} {nazwisko}</b> ({data}):<br>{tresc}<br><br>"

        self.chat_messages.setHtml(html)

    def load_more_history(self):
        self.offset += 100
        self.load_messages()

    # ============================================================
    # SEND MESSAGE
    # ============================================================
    def send_message(self):
        text = self.chat_input.text().strip()
        if not text:
            return

        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO wiadomosci_chat (konwersacja_id, pracownik_id, tresc)
            VALUES (?, ?, ?)
        """, (self.current_konwersacja, self.pracownik_id, text))

        conn.commit()
        conn.close()

        self.chat_input.clear()
        self.load_messages()

    # ============================================================
    # CREATE NEW CONVERSATION
    # ============================================================
    def create_new_conversation(self):
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QLineEdit, QPushButton, QMessageBox

        dialog = QDialog(self)
        dialog.setWindowTitle("Nowa konwersacja")
        dialog.setFixedSize(400, 200)

        layout = QVBoxLayout(dialog)
        form = QFormLayout()

        nazwa_field = QLineEdit()
        form.addRow("Nazwa:", nazwa_field)
        layout.addLayout(form)

        btn_create = QPushButton("Utwórz")
        layout.addWidget(btn_create)

        def on_create():
            nazwa = nazwa_field.text().strip()
            if not nazwa:
                QMessageBox.warning(dialog, "Błąd", "Wpisz nazwę konwersacji")
                return

            conn = sqlite3.connect(DB_NAME)
            cur = conn.cursor()

            cur.execute("""
                INSERT INTO konwersacje (nazwa, pracownik_creator_id)
                VALUES (?, ?)
            """, (nazwa, self.pracownik_id))

            conn.commit()
            conn.close()

            dialog.accept()
            self.refresh_chat_conversations()

        btn_create.clicked.connect(on_create)
        dialog.exec()
