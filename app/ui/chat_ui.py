# app/chat_ui/chat_ui.py
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QTextEdit, QLineEdit, QMessageBox, QSplitter, QDialog,
    QFormLayout, QInputDialog
)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt
import sqlite3
from app.chat.chat_model import (
    get_conversations, get_messages, add_message, edit_message
)
from app.chat.chat_notifications import ChatNotifier
from app.config import DB_NAME

class ChatPage(QWidget):
    def __init__(self, pracownik_id):
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

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(8)

        # LEWY PANEL
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        left_layout.addWidget(QLabel("Konwersacje:"))

        self.chat_conversations = QListWidget()
        self.chat_conversations.itemSelectionChanged.connect(self.on_chat_selected)
        left_layout.addWidget(self.chat_conversations)

        btn_new = QPushButton("➕ Nowa konwersacja")
        btn_new.clicked.connect(self.create_new_conversation)
        left_layout.addWidget(btn_new)

        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)

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

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([400, 800])

        left.setMinimumWidth(350)
        right.setMinimumWidth(600)

        layout.addWidget(splitter)

    def refresh_chat_conversations(self):
        self.chat_conversations.clear()
        for konwersacja_id, nazwa in get_conversations(self.pracownik_id):
            item = QListWidgetItem(nazwa)
            item.setData(Qt.ItemDataRole.UserRole, konwersacja_id)
            self.chat_conversations.addItem(item)

    def on_chat_selected(self):
        item = self.chat_conversations.currentItem()
        if not item:
            return

        self.current_konwersacja = item.data(Qt.ItemDataRole.UserRole)
        self.offset = 0
        self.load_messages()

        self.notifier = ChatNotifier(self.current_konwersacja)
        self.notifier.new_message.connect(self.load_messages)
        self.notifier.start()

    def load_messages(self):
        rows = get_messages(self.current_konwersacja, limit=100, offset=self.offset)
        html = ""

        for msg_id, imie, nazwisko, tresc, data, edytowano in rows:
            edit_tag = " <i>(edytowano)</i>" if edytowano else ""
            html += f"<b>{imie} {nazwisko}</b> ({data}){edit_tag}:<br>{tresc}<br><br>"

        self.chat_messages.setHtml(html)

    def load_more_history(self):
        self.offset += 100
        self.load_messages()

    def send_message(self):
        text = self.chat_input.text().strip()
        if not text:
            return

        add_message(self.current_konwersacja, self.pracownik_id, text)
        self.chat_input.clear()
        self.load_messages()

    def edit_message_dialog(self, message_id):
        dialog = QDialog(self)
        dialog.setWindowTitle("Edytuj wiadomość")

        layout = QVBoxLayout(dialog)
        field = QLineEdit()
        layout.addWidget(field)

        btn = QPushButton("Zapisz")
        layout.addWidget(btn)

        def save():
            new_text = field.text().strip()
            if new_text:
                edit_message(message_id, new_text)
                self.load_messages()
                dialog.accept()

        btn.clicked.connect(save)
        dialog.exec()

    def create_new_conversation(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Nowa konwersacja")
        dialog.setFixedSize(400, 250)

        layout = QVBoxLayout(dialog)
        form = QFormLayout()

        nazwa_field = QLineEdit()
        nazwa_field.setPlaceholderText("np: Projekt X - Team")
        form.addRow("Nazwa:", nazwa_field)

        uczestnicy_list = QListWidget()
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT id, imie, nazwisko FROM pracownicy WHERE aktywny = 1 ORDER BY imie")

        for emp_id, imie, nazwisko in cursor.fetchall():
            item = QListWidgetItem(f"{imie} {nazwisko}")
            item.setData(Qt.ItemDataRole.UserRole, emp_id)
            uczestnicy_list.addItem(item)

        conn.close()
        form.addRow("Uczestnicy:", uczestnicy_list)
        layout.addLayout(form)

        btn_layout = QHBoxLayout()
        btn_create = QPushButton("Utwórz")
        btn_cancel = QPushButton("Anuluj")
        btn_layout.addWidget(btn_create)
        btn_layout.addWidget(btn_cancel)
        layout.addLayout(btn_layout)

        def on_create():
            nazwa = nazwa_field.text().strip()
            if not nazwa:
                QMessageBox.warning(dialog, "Błąd", "Wpisz nazwę konwersacji")
                return

            if uczestnicy_list.selectedItems() == []:
                QMessageBox.warning(dialog, "Błąd", "Wybierz co najmniej jednego uczestnika")
                return

            conn2 = sqlite3.connect(DB_NAME)
            cursor2 = conn2.cursor()

            try:
                cursor2.execute("""
                       INSERT INTO konwersacje (nazwa, pracownik_creator_id)
                       VALUES (?, ?)
                   """, (nazwa, self.pracownik_id))
                konwersacja_id = cursor2.lastrowid

                cursor2.execute("""
                       INSERT INTO uczestnicy_chat (konwersacja_id, pracownik_id)
                       VALUES (?, ?)
                   """, (konwersacja_id, self.pracownik_id))

                for item in uczestnicy_list.selectedItems():
                    emp_id = item.data(Qt.ItemDataRole.UserRole)
                    if emp_id != self.pracownik_id:
                        cursor2.execute("""
                               INSERT OR IGNORE INTO uczestnicy_chat (konwersacja_id, pracownik_id)
                               VALUES (?, ?)
                           """, (konwersacja_id, emp_id))

                conn2.commit()
                QMessageBox.information(dialog, "Sukces", "Konwersacja została utworzona")
                dialog.accept()
                self.refresh_chat_conversations()
            except Exception as e:
                QMessageBox.critical(dialog, "Błąd", f"Błąd: {str(e)}")
            finally:
                conn2.close()

        btn_create.clicked.connect(on_create)
        btn_cancel.clicked.connect(dialog.reject)

        dialog.exec()