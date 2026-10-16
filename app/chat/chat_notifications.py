# app/chat/chat_notifications.py
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from typing import Optional
from app.chat.chat_model import get_new_messages, is_participant

class ChatNotifier(QObject):
    new_message = pyqtSignal(int)  # konwersacja_id

    def __init__(self, konwersacja_id: int, pracownik_id: Optional[int] = None):
        super().__init__()
        self.konwersacja_id = konwersacja_id
        self.pracownik_id = pracownik_id
        self.last_timestamp = "1970-01-01 00:00:00"
        self.timer: Optional[QTimer] = None

    def start(self, interval_ms: int = 2000):
        if self.timer is None:
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.check_updates)
            self.timer.start(interval_ms)

    def stop(self):
        if self.timer:
            self.timer.stop()
            self.timer = None

    def check_updates(self):
        try:
            if self.pracownik_id is not None and not is_participant(self.konwersacja_id, self.pracownik_id):
                return
            rows = get_new_messages(self.konwersacja_id, self.last_timestamp)
            if rows:
                # rows are ordered ascending; update last_timestamp to newest
                newest = rows[-1].get("data_wysylki") or self.last_timestamp
                self.last_timestamp = newest
                self.new_message.emit(self.konwersacja_id)
        except Exception:
            # silently ignore transient DB errors; notifier will retry on next tick
            pass
