"""Email page UI - IMAP email management."""

from __future__ import annotations

import re
import email
import logging
from typing import List, Dict, Any, Optional, Tuple

from app.utils.ui_imports import *

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

logger = logging.getLogger(__name__)

ARCHIVE_FOLDER = "INBOX.Archive"
SPAM_FOLDER = "INBOX.spam"
TRASH_FOLDER = "INBOX.Trash"


class EmailPage(QWidget):
    """Email management page."""
    
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
            except Exception as e:
                logger.warning(f"Failed to list folders: {e}")
            self.load_folders_from_db()
        else:
            QMessageBox.warning(self, "Brak kont", "Nie znaleziono żadnych kont email.")

    def _build_ui(self) -> None:
        """Build email UI."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(MARGIN_NORMAL, MARGIN_NORMAL, MARGIN_NORMAL, MARGIN_NORMAL)
        main_layout.setSpacing(SPACING_NORMAL)
        
        # Title
        title = create_section_header("✉️  Email")
        main_layout.addWidget(title)

        # Toolbar 1 - Sync controls
        toolbar = QHBoxLayout()
        toolbar.setSpacing(SPACING_NORMAL)
        
        btn_sync = create_primary_button("🔄 Synchronizacja wiadomości")
        btn_sync.clicked.connect(self.on_manual_sync)
        toolbar.addWidget(btn_sync)

        btn_refresh = create_primary_button("🔃 Odśwież widok")
        btn_refresh.clicked.connect(self.refresh_all)
        toolbar.addWidget(btn_refresh)

        toolbar.addStretch()
        main_layout.addLayout(toolbar)

        # Toolbar 2 - Email actions
        mail_toolbar = QHBoxLayout()
        mail_toolbar.setSpacing(SPACING_NORMAL)

        self.btn_reply = create_primary_button("↩️  Odpowiedz")
        self.btn_reply.clicked.connect(self.on_reply)
        mail_toolbar.addWidget(self.btn_reply)

        self.btn_forward = create_primary_button("↪️  Przekaż")
        self.btn_forward.clicked.connect(self.on_forward)
        mail_toolbar.addWidget(self.btn_forward)

        self.btn_archive = create_secondary_button("📦 Archiwizuj")
        self.btn_archive.clicked.connect(self.on_archive)
        mail_toolbar.addWidget(self.btn_archive)

        self.btn_spam = create_secondary_button("🚫 Niechciane")
        self.btn_spam.clicked.connect(self.on_spam)
        mail_toolbar.addWidget(self.btn_spam)

        self.btn_delete = create_danger_button("🗑️  Usuń")
        self.btn_delete.clicked.connect(self.on_delete)
        mail_toolbar.addWidget(self.btn_delete)

        mail_toolbar.addStretch()
        main_layout.addLayout(mail_toolbar)

        # Content area with splitter
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: folder list
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Foldery:"))
        
        self.folder_list = QListWidget()
        self.folder_list.itemClicked.connect(self.on_folder_selected)
        left_layout.addWidget(self.folder_list)

        # Right: message list and content
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("Wiadomości:"))
        
        self.message_list = QListWidget()
        self.message_list.itemClicked.connect(self.on_message_selected)
        right_layout.addWidget(self.message_list)

        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        main_layout.addWidget(splitter)

    def on_manual_sync(self):
        """Handle manual sync button."""
        try:
            logger.info("Manual sync started")
            sync_all_emails(self.current_account["id"])
            self.refresh_all()
            QMessageBox.information(self, "OK", "Synchronizacja zakończona")
        except Exception as e:
            logger.error(f"Sync failed: {e}")
            QMessageBox.critical(self, "Błąd", f"Synchronizacja nie powiodła się: {str(e)}")

    def refresh_all(self):
        """Refresh all views."""
        logger.debug("Refreshing email views")
        self.load_folders_from_db()

    def load_folders_from_db(self):
        """Load folders from database."""
        try:
            if not self.current_account:
                return
            
            folders = get_folders_for_account(self.current_account["id"])
            self.folder_list.clear()
            
            for folder in folders:
                item = QListWidgetItem(folder["nazwa"])
                item.setData(Qt.ItemDataRole.UserRole, folder["id"])
                self.folder_list.addItem(item)
            
            logger.debug(f"Loaded {len(folders)} folders")
        except Exception as e:
            logger.error(f"Failed to load folders: {e}")

    def on_folder_selected(self, item: QListWidgetItem):
        """Handle folder selection."""
        self.current_folder_local = item.text()
        logger.debug(f"Selected folder: {self.current_folder_local}")
        self.load_messages_for_folder()

    def load_messages_for_folder(self):
        """Load messages from selected folder."""
        try:
            if not self.current_folder_local:
                return
            
            messages = search_messages(self.current_account["id"], self.current_folder_local)
            self.message_list.clear()
            
            for msg in messages:
                subject = msg.get("temat", "(bez tematu)")
                sender = msg.get("nadawca", "(nieznany)")
                item = QListWidgetItem(f"{sender} - {subject}")
                item.setData(Qt.ItemDataRole.UserRole, msg["uid"])
                self.message_list.addItem(item)
            
            logger.debug(f"Loaded {len(messages)} messages")
        except Exception as e:
            logger.error(f"Failed to load messages: {e}")

    def on_message_selected(self, item: QListWidgetItem):
        """Handle message selection."""
        self.current_uid = item.data(Qt.ItemDataRole.UserRole)
        logger.debug(f"Selected message UID: {self.current_uid}")

    def on_reply(self):
        """Handle reply action."""
        if not self.current_uid:
            QMessageBox.warning(self, "Błąd", "Wybierz wiadomość")
            return
        logger.info(f"Reply to UID: {self.current_uid}")

    def on_forward(self):
        """Handle forward action."""
        if not self.current_uid:
            QMessageBox.warning(self, "Błąd", "Wybierz wiadomość")
            return
        logger.info(f"Forward UID: {self.current_uid}")

    def on_archive(self):
        """Handle archive action."""
        if not self.current_uid:
            QMessageBox.warning(self, "Błąd", "Wybierz wiadomość")
            return
        try:
            move_email_and_get_sender(self.current_account["id"], self.current_uid, ARCHIVE_FOLDER)
            self.load_messages_for_folder()
            logger.info(f"Archived UID: {self.current_uid}")
        except Exception as e:
            logger.error(f"Archive failed: {e}")
            QMessageBox.critical(self, "Błąd", f"Archiwizacja nie powiodła się: {str(e)}")

    def on_spam(self):
        """Handle spam action."""
        if not self.current_uid:
            QMessageBox.warning(self, "Błąd", "Wybierz wiadomość")
            return
        try:
            move_email_and_get_sender(self.current_account["id"], self.current_uid, SPAM_FOLDER)
            self.load_messages_for_folder()
            logger.info(f"Marked as spam UID: {self.current_uid}")
        except Exception as e:
            logger.error(f"Spam action failed: {e}")
            QMessageBox.critical(self, "Błąd", f"Operacja nie powiodła się: {str(e)}")

    def on_delete(self):
        """Handle delete action."""
        if not self.current_uid:
            QMessageBox.warning(self, "Błąd", "Wybierz wiadomość")
            return
        
        reply = QMessageBox.question(self, "Potwierdzenie", "Czy na pewno usunąć wiadomość?")
        if reply == QMessageBox.StandardButton.Yes:
            try:
                delete_email_remote(self.current_account["id"], self.current_uid)
                self.load_messages_for_folder()
                logger.info(f"Deleted UID: {self.current_uid}")
            except Exception as e:
                logger.error(f"Delete failed: {e}")
                QMessageBox.critical(self, "Błąd", f"Usunięcie nie powiodło się: {str(e)}")
