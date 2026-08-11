# app/email/email_actions.py
from PyQt6.QtCore import QRunnable, QObject, pyqtSignal

class SyncWorkerSignals(QObject):
    """Sygnały emitowane przez worker synchronizacji emaili."""
    finished = pyqtSignal(bool)        # True = sukces, False = niepowodzenie
    error = pyqtSignal(str)
    progress = pyqtSignal(int)         # opcjonalnie: procent lub etapy

class SyncRunnable(QRunnable):
    """
    Worker uruchamiany w tle (QThreadPool), który wywołuje synchronizację emaili.
    Emituje sygnały finished/error/progress.
    """
    def __init__(self, parent):
        super().__init__()
        self.parent = parent
        self.signals = SyncWorkerSignals()
        self.setAutoDelete(True)

    def run(self):
        try:
            if not hasattr(self.parent, "sync_all_emails") or not callable(self.parent.sync_all_emails):
                raise RuntimeError("Brak metody sync_all_emails() w obiekcie parent")

            # Jeśli metoda sync_all_emails potrafi raportować postęp, niech to robi przez callbacky.
            result = self.parent.sync_all_emails()
            success = bool(result) if result is not None else True
            self.signals.finished.emit(success)
        except Exception as e:
            self.signals.error.emit(str(e))
            self.signals.finished.emit(False)
