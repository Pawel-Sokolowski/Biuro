import os
import sys

from app.utils.env_config import load_environment, ensure_encryption_key

load_environment()
ensure_encryption_key()

from PyQt6.QtWidgets import (QApplication, QDialog)
import traceback

from app.main_ui import LoginWindow
from app.utils.i18n import install_polish_translations
from app.main_ui import OfficeManagerApp

def excepthook(type, value, tb):
    print("=== PYQT EXCEPTION CAUGHT ===")
    traceback.print_exception(type, value, tb)
    sys.__excepthook__(type, value, tb)

sys.excepthook = excepthook

if __name__ == "__main__":

    app = QApplication(sys.argv)
    install_polish_translations(app)
    login_window = LoginWindow()
    if login_window.exec() == QDialog.DialogCode.Accepted:
        pracownik_id = login_window.pracownik_id
        window = OfficeManagerApp(pracownik_id)
        window.show()
        sys.exit(app.exec())
    else:
        sys.exit()