# app/chat/chat_notifications.py
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from app.chat.chat_model import get_new_messages

class ChatNotifier(QObject):
    new_message = pyqtSignal(int)  # konwersacja_id

    def __init__(self, konwersacja_id):
        super().__init__()
        self.konwersacja_id = konwersacja_id
        self.last_timestamp = "1970-01-01 00:00:00"

    def start(self):
        self.timer = QTimer()
        self.timer.timeout.connect(self.check_updates)
        self.timer.start(2000)

    def check_updates(self):
        rows = get_new_messages(self.konwersacja_id, self.last_timestamp)
        if rows:
            self.last_timestamp = rows[-1][4]  # data_wysylki
            self.new_message.emit(self.konwersacja_id)
