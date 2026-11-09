"""Systemowe powiadomienia (toast Windows) o nowych e-mailach, jak w Thunderbirdzie."""
import logging
from email.utils import parseaddr

from PyQt6.QtWidgets import QApplication, QStyle, QSystemTrayIcon

from app.email.email_storage import get_new_unread_messages
from app.windows_toasts import native_toasts

logger = logging.getLogger(__name__)
SHOW_MS = 6000
MAX_SEPARATE = 3


def _compact(value, limit=100):
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _sender_name(raw, *, include_address=False) -> str:
    name, address = parseaddr(str(raw or ""))
    if include_address and name and address:
        return _compact(f"{name} <{address}>")
    return _compact(name or address or raw or "(nieznany nadawca)")


def _account_label(account):
    address = account.get("email") or "(nieznane konto)"
    name = account.get("display_name")
    return f"{name} <{address}>" if name else address


def _notification_text(messages):
    """Keep native tray notifications within the Windows balloon text budget."""
    if len(messages) == 1:
        message = messages[0]
        account = message["_account"]
        title = _compact("Nowy e-mail — " + str(account.get("email") or _account_label(account)), 63)
        body = f"Od: {_compact(_sender_name(message.get('nadawca'), include_address=True), 80)}\nTemat: {_compact(message.get('temat') or '(bez tematu)', 145)}"
        return title, body
    shown = messages[:3]
    lines = []
    for message in shown:
        account = message["_account"]
        lines.append(f"{_compact(account.get('email') or _account_label(account), 22)} · "
                     f"{_compact(_sender_name(message.get('nadawca')), 20)} — "
                     f"{_compact(message.get('temat') or '(bez tematu)', 26)}")
    if len(messages) > len(shown):
        lines.append(f"Pozostałe: {len(messages) - len(shown)}")
    return f"Nowe wiadomości e-mail: {len(messages)}", "\n".join(lines)


class MailToaster:
    """Wywoływany cyklicznie; pierwsze wywołanie tylko zapamiętuje stan skrzynek."""

    def __init__(self, window, on_click):
        self._last_ids = {}
        self._on_click = on_click
        self._click_target = None
        self._native = native_toasts(window, on_click)
        self._tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            icon = window.windowIcon()
            if icon.isNull():
                icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation)
            self._tray = QSystemTrayIcon(icon, window)
            self._tray.setToolTip("Biuro")
            self._tray.messageClicked.connect(lambda: self._on_click(self._click_target))
            self._tray.show()

    def check(self, accounts, hidden_folders):
        self.apply_check(*self.collect(accounts, hidden_folders, dict(self._last_ids)))

    @staticmethod
    def collect(accounts, hidden_folders, last_ids):
        """Database-only phase, safe to run in the bounded read pool."""
        new = []
        for account in accounts:
            account_id = account['id']
            try:
                max_id, found = get_new_unread_messages(account_id, last_ids.get(account_id),
                                                      hidden_folders.get(str(account_id), set()))
            except Exception:
                logger.debug('mail_toast_read failed', exc_info=True)
                continue
            last_ids[account_id] = max_id
            new.extend({**message, '_account': dict(account)} for message in found)
        return last_ids, new

    def apply_check(self, last_ids, new):
        self._last_ids = last_ids
        if self._tray is None and getattr(self, '_native', None) is None:
            return
        if not new:
            return
        if len(new) > MAX_SEPARATE:
            title, body = _notification_text(new)
            self._show(title, body, None)
            return
        for message in new:
            title, body = _notification_text([message])
            target = {'kind': 'email', 'account_id': message['_account']['id'],
                      'row_id': message.get('id'), 'message_id': message.get('message_id')}
            self._show(title, body, target)

    def _show(self, title, body, target):
        if getattr(self, '_native', None) is not None:
            try:
                self._native.show(title, body, target)
                return
            except Exception:
                logger.warning('Nie można wyświetlić powiadomienia Windows', exc_info=True)
        self._click_target = target
        if self._tray is not None:
            self._tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, SHOW_MS)

    def show_notice(self, title, body):
        self._show(title, body, None)

    def hide(self):
        if getattr(self, '_native', None) is not None:
            self._native.close()
        if self._tray is not None:
            self._tray.hide()
