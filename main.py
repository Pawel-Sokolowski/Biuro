from PyQt6.QtWidgets import (QApplication, QDialog)

from app.companies.companies_ui import LoginWindow, OfficeManagerApp
from app.db.schema import init_db
import sys
import traceback

def excepthook(type, value, tb):
    print("=== PYQT EXCEPTION CAUGHT ===")
    traceback.print_exception(type, value, tb)
    sys.__excepthook__(type, value, tb)

sys.excepthook = excepthook

if __name__ == "__main__":
    app = QApplication(sys.argv)

    # KROK 1: Wymuszenie natychmiastowej inicjalizacji bazy danych
    # zanim okno logowania w ogóle zapyta o użytkowników!
    init_db()

    # Pokaż okno logowania
    login_window = LoginWindow()
    if login_window.exec() == QDialog.DialogCode.Accepted:
        pracownik_id = login_window.pracownik_id
        window = OfficeManagerApp(pracownik_id)
        window.show()
        sys.exit(app.exec())
    else:
        sys.exit()