from dotenv import load_dotenv
load_dotenv()


from PyQt6.QtWidgets import (QApplication, QDialog)


import sys, os
import traceback

DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
from app.main_ui import LoginWindow
from app.main_ui import OfficeManagerApp
from app.db.schema import init_db

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