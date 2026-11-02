"""Systemowe powiadomienia (toast Windows) o nowych e-mailach, jak w Thunderbirdzie."""
import logging
import re

from PyQt6.QtWidgets import QApplication, QStyle, QSystemTrayIcon

from app.email.email_storage import get_new_unread_messages

logger = logging.getLogger(__name__)
SHOW_MS = 6000
MAX_SEPARATE = 3


def _sender_name(raw) -> str:
    text = str(raw or "").strip()
    match = re.match(r'\s*"?([^"<]+?)"?\s*<', text)
    return (match.group(1) if match else text) or "(nieznany nadawca)"


class MailToaster:
    """Wywoływany cyklicznie; pierwsze wywołanie tylko zapamiętuje stan skrzynek."""

    def __init__(self, window, on_click):
        self._last_ids = {}
        self._on_click = on_click
        self._tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            icon = window.windowIcon()
            if icon.isNull():
                icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation)
            self._tray = QSystemTrayIcon(icon, window)
            self._tray.setToolTip("Biuro")
            self._tray.messageClicked.connect(self._on_click)
            self._tray.show()

    def check(self, accounts, hidden_folders):
        if self._tray is None:
            return
        new = []
        for account in accounts:
            konto_id = account["id"]
            try:
                max_id, found = get_new_unread_messages(
                    konto_id, self._last_ids.get(konto_id),
                    hidden_folders.get(str(konto_id), set()))
            except Exception:
                logger.debug("Powiadomienia o mailach: błąd odczytu", exc_info=True)
                continue
            self._last_ids[konto_id] = max_id
            new.extend(found)
        if not new:
            return
        if len(new) > MAX_SEPARATE:
            self._tray.showMessage("Nowe wiadomości e-mail", f"Liczba nowych wiadomości: {len(new)}",
                                   QSystemTrayIcon.MessageIcon.Information, SHOW_MS)
            return
        for message in new:
            self._tray.showMessage(_sender_name(message["nadawca"]), str(message["temat"] or "(bez tematu)"),
                                   QSystemTrayIcon.MessageIcon.Information, SHOW_MS)

    def hide(self):
        if self._tray is not None:
            self._tray.hide()
