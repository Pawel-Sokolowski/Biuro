from PyQt6.QtWidgets import (QApplication, QDialog)

from app.companies.companies_ui import LoginWindow
from app.main_ui import OfficeManagerApp
from app.db.schema import init_db
import sys
import traceback

def excepthook(type, value, tb):
    print("=== PYQT EXCEPTION CAUGHT ===")
    traceback.print_exception(type, value, tb)
    sys.__excepthook__(type, value, tb)

sys.excepthook = excepthook

if __name__ == "__main__":

    init_db()
    app = QApplication(sys.argv)

    login_window = LoginWindow()
    if login_window.exec() == QDialog.DialogCode.Accepted:
        pracownik_id = login_window.pracownik_id
        window = OfficeManagerApp(pracownik_id)
        window.show()
        sys.exit(app.exec())
    else:
        sys.exit()