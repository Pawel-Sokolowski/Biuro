import os
import sys

from app.utils.env_config import load_environment, ensure_encryption_key

load_environment()
ensure_encryption_key()

from PyQt6.QtCore import Qt
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
    if "--migrate" in sys.argv:
        # Biuro.exe --migrate: aktualizacja schematu bazy (bez okna logowania)
        from app.db.schema import init_db, schema_is_current
        from PyQt6.QtWidgets import QMessageBox
        app = QApplication(sys.argv)
        try:
            init_db()
            ok = schema_is_current()
            msg = "Baza danych jest aktualna." if ok else "Aktualizacja bazy nie powiodła się."
        except Exception as exc:
            ok, msg = False, f"Błąd aktualizacji bazy:\n{exc}"
        (QMessageBox.information if ok else QMessageBox.critical)(None, "Aktualizacja bazy", msg)
        sys.exit(0 if ok else 1)

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
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