"""Chat page UI - internal employee messaging."""

import logging
from typing import Optional

from app.utils.ui_imports import *
from app.config import DB_NAME
from app.chat.chat_model import get_conversations, get_messages, add_message, create_conversation

import sqlite3

logger = logging.getLogger(__name__)


def _format_date_header(dt_str: str) -> str:
    """Format date string from DB to 'YYYY-MM-DD'. Accepts common SQLite datetime formats."""
    if not dt_str:
        return ""
    dt = QDateTime.fromString(dt_str, "yyyy-MM-dd HH:mm:ss")
    if not dt.isValid():
        dt = QDateTime.fromString(dt_str, Qt.DateFormat.ISODate)
    if not dt.isValid():
        return dt_str.split(" ")[0]
    return dt.toString("yyyy-MM-dd")


class ChatPage(QWidget):
    """Internal chat messaging page."""
    
    def __init__(self, pracownik_id: int):
        super().__init__()
        self.pracownik_id = pracownik_id
        self.current_konwersacja: Optional[int] = None
        self.offset = 0
        self.current_pracownik_id = pracownik_id

        self._build_ui()
        self.refresh_chat_conversations()

    def _build_ui(self):
        """Build chat UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(MARGIN_NORMAL, MARGIN_NORMAL, MARGIN_NORMAL, MARGIN_NORMAL)
        layout.setSpacing(SPACING_NORMAL)

        # Title
        title = create_section_header("💬 Chat wewnętrzny")
        layout.addWidget(title)

        # Main container
        container = QHBoxLayout()
        container.setSpacing(SPACING_LARGE)

        # LEFT: conversations list
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(SPACING_NORMAL)
        
        left_layout.addWidget(QLabel("Konwersacje:"))

        self.chat_conversations = QListWidget()
        self.chat_conversations.itemSelectionChanged.connect(self.on_chat_selected)
        left_layout.addWidget(self.chat_conversations)

        btn_new = create_primary_button("➕ Nowa konwersacja")
        btn_new.clicked.connect(self.create_new_conversation)
        left_layout.addWidget(btn_new)

        # RIGHT: messages area with scroll
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(SPACING_NORMAL)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.messages_container = QWidget()
        self.messages_layout = QVBoxLayout(self.messages_container)
        self.messages_layout.setContentsMargins(MARGIN_SMALL, MARGIN_SMALL, MARGIN_SMALL, MARGIN_SMALL)
        self.messages_layout.setSpacing(SPACING_SMALL)
        self.messages_container.setLayout(self.messages_layout)
        self.scroll_area.setWidget(self.messages_container)
        right_layout.addWidget(self.scroll_area)

        btn_history = create_secondary_button("⬆ Pokaż starsze wiadomości")
        btn_history.clicked.connect(self.load_more_history)
        right_layout.addWidget(btn_history)

        # Message input
        bottom = QHBoxLayout()
        bottom.setSpacing(SPACING_NORMAL)
        
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("Napisz wiadomość...")
        bottom.addWidget(self.chat_input)

        btn_send = create_primary_button("Wyślij")
        btn_send.clicked.connect(self.send_message)
        bottom.addWidget(btn_send)

        right_layout.addLayout(bottom)

        # Layout split
        container.addWidget(left, 1)
        container.addWidget(right, 3)
        layout.addLayout(container)

    def on_chat_selected(self):
        """Handle chat selection."""
        current = self.chat_conversations.currentItem()
        if current:
            self.current_konwersacja = current.data(Qt.ItemDataRole.UserRole)
            self.offset = 0
            self.load_messages()
        logger.debug(f"Selected conversation: {self.current_konwersacja}")

    def refresh_chat_conversations(self):
        """Refresh conversation list."""
        try:
            conversations = get_conversations(self.pracownik_id)
            self.chat_conversations.clear()
            
            for conv in conversations:
                item = QListWidgetItem(conv["nazwa"])
                item.setData(Qt.ItemDataRole.UserRole, conv["id"])
                self.chat_conversations.addItem(item)
                logger.debug(f"Added conversation: {conv['nazwa']}")
        except Exception as e:
            logger.error(f"Failed to refresh conversations: {e}")
            QMessageBox.critical(self, "Błąd", f"Nie udało się załadować konwersacji: {str(e)}")

    def create_new_conversation(self):
        """Create new conversation."""
        text, ok = QInputDialog.getText(self, "Nowa konwersacja", "Nazwa konwersacji:")
        if ok and text.strip():
            try:
                conv_id = create_conversation(text.strip(), self.pracownik_id)
                self.refresh_chat_conversations()
                logger.info(f"Created conversation: {text} (ID: {conv_id})")
            except Exception as e:
                logger.error(f"Failed to create conversation: {e}")
                QMessageBox.critical(self, "Błąd", f"Nie udało się utworzyć konwersacji: {str(e)}")

    def load_messages(self):
        """Load messages from current conversation."""
        if not self.current_konwersacja:
            return
        
        try:
            # Clear layout
            while self.messages_layout.count():
                self.messages_layout.takeAt(0).widget().deleteLater()
            
            messages = get_messages(self.current_konwersacja, self.offset)
            for msg in reversed(messages):
                label = QLabel(f"<b>{msg['pracownik_name']}:</b> {msg['tresc']}")
                label.setWordWrap(True)
                self.messages_layout.addWidget(label)
            
            self.messages_layout.addStretch()
            logger.debug(f"Loaded {len(messages)} messages")
        except Exception as e:
            logger.error(f"Failed to load messages: {e}")

    def load_more_history(self):
        """Load older messages."""
        self.offset += 10
        self.load_messages()
        logger.debug(f"Loading more history, offset: {self.offset}")

    def send_message(self):
        """Send new message."""
        if not self.current_konwersacja:
            QMessageBox.warning(self, "Błąd", "Wybierz konwersację")
            return
        
        text = self.chat_input.text().strip()
        if not text:
            return
        
        try:
            add_message(self.current_konwersacja, self.pracownik_id, text)
            self.chat_input.clear()
            self.offset = 0
            self.load_messages()
            logger.info(f"Message sent to conversation {self.current_konwersacja}")
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            QMessageBox.critical(self, "Błąd", f"Nie udało się wysłać wiadomości: {str(e)}")
