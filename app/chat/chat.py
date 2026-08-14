# chat.py
import sqlite3
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QTextEdit, QLineEdit, QMessageBox, QSplitter, QDialog,
    QFormLayout
)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt, QTimer
from config import DB_NAME

class ChatPage(QWidget):
    def __init__(self, pracownik_id):
        super().__init__()
        self.pracownik_id = pracownik_id

        self.chat_conversations = None
        self.chat_messages = None
        self.chat_input = None
        self.chat_timer = None

        self._build_ui()
        self.refresh_chat_conversations()

    # ============================================================
    # UI
    # ============================================================
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        lbl_title = QLabel("Chat wewnętrzny")
        lbl_title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        layout.addWidget(lbl_title)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # === LEWA STRONA ===
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.addWidget(QLabel("Konwersacje:"))

        self.chat_conversations = QListWidget()
        self.chat_conversations.itemSelectionChanged.connect(self.on_chat_selected)
        left_layout.addWidget(self.chat_conversations)

        btn_new_chat = QPushButton("➕ Nowa konwersacja")
        btn_new_chat.setStyleSheet("background-color: #27ae60; color: white; font-weight: bold;")
        btn_new_chat.clicked.connect(self.create_new_conversation)
        left_layout.addWidget(btn_new_chat)

        left_widget.setFixedWidth(300)
        splitter.addWidget(left_widget)

        # === PRAWA STRONA ===
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.addWidget(QLabel("Wiadomości:"))

        self.chat_messages = QTextEdit()
        self.chat_messages.setReadOnly(True)
        right_layout.addWidget(self.chat_messages)

        bottom_layout = QHBoxLayout()
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Wpisz wiadomość...")
        bottom_layout.addWidget(self.chat_input)

        btn_send = QPushButton("Wyślij")
        btn_send.setStyleSheet("background-color: #3498db; color: white; font-weight: bold;")
        btn_send.clicked.connect(self.send_chat_message)
        bottom_layout.addWidget(btn_send)

        right_layout.addLayout(bottom_layout)
        splitter.addWidget(right_widget)

        layout.addWidget(splitter)

        # Timer
        self.chat_timer = QTimer()
        self.chat_timer.timeout.connect(self.refresh_chat_display)
        self.chat_timer.start(2000)

    # ============================================================
    # KONWERSACJE
    # ============================================================
    def refresh_chat_conversations(self):
        self.chat_conversations.blockSignals(True)
        try:
            self.chat_conversations.clear()
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT DISTINCT k.id, k.nazwa 
                FROM konwersacje k
                JOIN uczestnicy_chat uc ON k.id = uc.konwersacja_id
                WHERE uc.pracownik_id = ?
                ORDER BY k.data_utworzenia DESC
            """, (self.pracownik_id,))

            for konwersacja_id, nazwa in cursor.fetchall():
                item = QListWidgetItem(nazwa)
                item.setData(Qt.ItemDataRole.UserRole, konwersacja_id)
                self.chat_conversations.addItem(item)

            conn.close()
        finally:
            self.chat_conversations.blockSignals(False)

    # ============================================================
    # WIADOMOŚCI
    # ============================================================
    def refresh_chat_display(self):
        if self.chat_conversations.currentRow() < 0:
            return

        current_item = self.chat_conversations.currentItem()
        if not current_item:
            return

        konwersacja_id = current_item.data(Qt.ItemDataRole.UserRole)
        if konwersacja_id is None:
            return

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT p.imie, p.nazwisko, wc.tresc, wc.data_wysylki
            FROM wiadomosci_chat wc
            JOIN pracownicy p ON wc.pracownik_id = p.id
            WHERE wc.konwersacja_id = ?
            ORDER BY wc.data_wysylki ASC
        """, (konwersacja_id,))

        nowy_tekst = ""
        for imie, nazwisko, tresc, data in cursor.fetchall():
            nowy_tekst += f"<b>{imie} {nazwisko}</b> ({data}):<br>{tresc}<br><br>"

        conn.close()

        # Aktualizacja tylko gdy są zmiany
        if self.chat_messages.toPlainText().strip() != nowy_tekst.replace('<br>', '\n').replace('<b>', '').replace('</b>', '').strip():
            self.chat_messages.setHtml(nowy_tekst)
            self.chat_messages.verticalScrollBar().setValue(
                self.chat_messages.verticalScrollBar().maximum()
            )

    def on_chat_selected(self):
        self.refresh_chat_display()

    def send_chat_message(self):
        if self.chat_conversations.currentRow() < 0:
            QMessageBox.warning(self, "Błąd", "Wybierz konwersację")
            return

        message = self.chat_input.text().strip()
        if not message:
            return

        current_item = self.chat_conversations.currentItem()
        konwersacja_id = current_item.data(Qt.ItemDataRole.UserRole)

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        try:
            cursor.execute("""
                INSERT INTO wiadomosci_chat (konwersacja_id, pracownik_id, tresc, data_wysylki)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """, (konwersacja_id, self.pracownik_id, message))
            conn.commit()
            self.chat_input.clear()
            self.refresh_chat_display()
        except Exception as e:
            QMessageBox.critical(self, "Błąd", f"Błąd wysyłania: {str(e)}")
        finally:
            conn.close()

    # ============================================================
    # NOWA KONWERSACJA
    # ============================================================
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
