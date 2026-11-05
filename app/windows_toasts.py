"""Native Windows toasts with an activation callback for each notification."""
import sys
from xml.sax.saxutils import escape

from PyQt6.QtCore import QObject, pyqtSignal

APP_ID = 'Biuro.OfficeManager'


class WindowsToasts(QObject):
    activated = pyqtSignal(object)

    def __init__(self, parent, on_click):
        super().__init__(parent)
        import ctypes
        import winreg
        from winrt.windows.ui.notifications import ToastNotificationManager

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf'Software\Classes\AppUserModelId\{APP_ID}') as key:
            winreg.SetValueEx(key, 'DisplayName', 0, winreg.REG_SZ, 'Biuro')
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        self._notifier = ToastNotificationManager.create_toast_notifier(APP_ID)
        self._notifications = []
        self.activated.connect(on_click)

    def show(self, title, body, target):
        from winrt.windows.data.xml.dom import XmlDocument
        from winrt.windows.ui.notifications import ToastNotification

        document = XmlDocument()
        document.load_xml('<toast><visual><binding template="ToastGeneric">'
                          f'<text>{escape(title)}</text><text>{escape(body)}</text>'
                          '</binding></visual></toast>')
        notification = ToastNotification(document)
        # WinRT invokes handlers off the Qt thread. The signal delivers the
        # captured row identity on the UI thread, even for an older toast.
        token = notification.add_activated(lambda *_args, value=target: self.activated.emit(value))
        self._notifier.show(notification)
        self._notifications.append((notification, token))

    def close(self):
        for notification, token in self._notifications:
            notification.remove_activated(token)
        self._notifications.clear()


def native_toasts(parent, on_click):
    if sys.platform != 'win32':
        return None
    try:
        return WindowsToasts(parent, on_click)
    except Exception:
        import logging
        logging.getLogger(__name__).warning('Nie można uruchomić powiadomień Windows', exc_info=True)
        return None
