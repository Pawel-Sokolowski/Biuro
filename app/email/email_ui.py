"""Punkt wejścia strony E-mail (`from app.email.email_ui import EmailPage`).

Kod strony jest w `email_page`; ten plik istnieje dla dotychczasowych importów
(`app.main_ui`, testy, skrypty), żeby podział na moduły nie zmieniał API.
"""
from __future__ import annotations

from app.email import email_page as _page_module
from app.email.email_page import (  # noqa: F401 — re-eksport
    AUTO_SYNC_INTERVAL_MS,
    DEFAULT_COLUMN_WIDTHS,
    MAX_NUMBER_KEYS,
    ROLE_ACCOUNT,
    ROLE_DATA,
    ROLE_FOLDER,
    ROLE_FOLDER_IDS,
    ROLE_FOLDER_LABEL,
    ROLE_MSGID,
    ROLE_REFS,
    ROLE_ROW_ID,
    EmailPage,
    FolderTree,
    MailArea,
    MailBrowser,
    _MailWebPage,
)

# Testy i skrypty podmieniają te nazwy na module `email_ui`; kod strony czyta je
# przez `email_page`, więc re-eksportujemy je tutaj (podmiana działa w obie strony).
from app.email.email_storage import (  # noqa: F401,E402 — re-eksport dla testów
    count_unread_assigned_by_employee,
    get_email_accounts_for_employee,
)
from PyQt6.QtCore import QTimer  # noqa: F401,E402
from PyQt6.QtWidgets import QFileDialog, QMessageBox  # noqa: F401,E402
from PyQt6.QtGui import QDesktopServices  # noqa: F401,E402

# Dawniej ten moduł był jednym plikiem; testy i skrypty sięgają po te nazwy
# bezpośrednio, więc zostają tu dostępne tak jak wcześniej.
from app.email.email_actions import open_attachment  # noqa: F401,E402
import tempfile  # noqa: F401,E402
import threading  # noqa: F401,E402
from urllib.parse import quote, unquote  # noqa: F401,E402
from html import escape as html_escape  # noqa: F401,E402
from app.email.email_helpers import (  # noqa: F401,E402
    SPECIAL_FOLDER_LABELS,
    SPECIAL_FOLDER_ORDER,
    attachment_name,
    attachment_save_path,
    decode_mailbox_utf7,
    extract_html,
    extract_text,
    folder_role,
    inline_resources,
    is_risky_attachment,
    iter_message_attachments,
    safe_attachment_name,
    safe_header,
    viewable_attachment_extension,
)
from app.email.email_labels import label_color  # noqa: F401,E402
from app.email.email_storage import (  # noqa: F401,E402
    filter_visible_unread_counts,
    find_special_folder,
    get_mail_page,
)

from app.email.mail_helpers import (  # noqa: F401,E402
    build_folder_tree,
    clean_folder_label,
    display_names,
    employee_label,
    employee_name,
    employee_tag,
    format_list_date,
    group_threads,
    thread_key,
)
from app.email.mail_model import MailItem, MailTreeView  # noqa: F401,E402
from app.email.mail_view import _MailWebPage, _RemoteContentBlocker  # noqa: F401,E402
from app.email.compose_dialog import ComposeDialog  # noqa: F401,E402
from app.utils.background import CommandRunner, LatestRead, TaskRunner  # noqa: F401,E402
from app.utils.view_state import ItemViewState  # noqa: F401,E402
from PyQt6.QtWidgets import (  # noqa: F401,E402
    QAbstractItemView, QApplication, QButtonGroup, QDialog, QDialogButtonBox, QFrame,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSplitter, QStyle, QTextEdit, QToolButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)
from PyQt6.QtGui import QAction, QColor, QFont, QKeySequence  # noqa: F401,E402
from PyQt6.QtCore import (  # noqa: F401,E402
    QCoreApplication, QItemSelectionModel, QUrl, pyqtSignal,
)
from PyQt6.QtWebEngineCore import (  # noqa: F401,E402
    QWebEnginePage, QWebEngineProfile, QWebEngineSettings, QWebEngineUrlRequestInterceptor,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401,E402

__all__ = ['EmailPage', 'FolderTree', 'MailArea', 'MailBrowser', 'ROLE_ROW_ID']
