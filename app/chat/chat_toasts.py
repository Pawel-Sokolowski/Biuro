"""Desktop chat notifications independent of the visible tab or window state."""
import logging
from PyQt6.QtCore import QTimer
from app.chat.chat_model import get_chat_notification_messages
from app.mail_toasts import MailToaster, _compact
from app.utils.background import TaskRunner

logger = logging.getLogger(__name__)


class ChatToaster(MailToaster):
    def __init__(self, window, actor_id, on_click):
        super().__init__(window, on_click)
        self.actor_id = actor_id
        self._last_id = None
        self._busy = False
        self._closed = False
        self._runner = TaskRunner(window)
        self.timer = QTimer(window)
        self.timer.setInterval(3000)
        self.timer.timeout.connect(self.check)
        self.timer.start()
        self.check()

    def check(self):
        if self._busy or self._closed:
            return
        self._busy = True
        self._runner.submit(lambda: get_chat_notification_messages(self.actor_id, self._last_id),
                            self._loaded, self._failed)

    def _loaded(self, result):
        self._busy = False
        if self._closed:
            return
        self._last_id, messages = result
        grouped = {}
        for message in messages:
            grouped.setdefault(message['konwersacja_id'], []).append(message)
        for conversation_id, rows in grouped.items():
            message = rows[-1]
            sender = ' '.join(filter(None, (message.get('imie'), message.get('nazwisko')))) or 'Pracownik'
            name = message.get('nazwa') if message.get('typ') != 'prywatna' else sender
            title = _compact(f"Czat — {name or 'Nowa wiadomość'}", 63)
            text = _compact(message.get('tresc') or 'Przesłano załącznik', 170)
            suffix = f"\nNowych wiadomości: {len(rows)}" if len(rows) > 1 else ''
            self._show(title, f'{_compact(sender, 60)}: {text}{suffix}',
                       {'kind': 'chat', 'conversation_id': conversation_id, 'message_id': message['id']})

    def _failed(self, exc):
        self._busy = False
        logger.debug('Powiadomienia czatu: %s', exc)

    def hide(self):
        self._closed = True
        self.timer.stop()
        self._runner.invalidate()
        super().hide()
