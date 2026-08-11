import sys
import sqlite3
import datetime, time
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QListWidget, QStackedWidget, QLabel,
                             QTableWidget, QTableWidgetItem, QHeaderView,
                             QPushButton, QLineEdit, QFormLayout, QGridLayout,
                             QSplitter, QComboBox, QMessageBox, QFileDialog,
                             QDialog, QDialogButtonBox, QProgressDialog, QTextEdit,
                             QTabWidget, QCheckBox, QSpinBox, QListWidgetItem,
                             QGroupBox, QAbstractItemView, QScrollArea)
from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QFont
import openpyxl
from PyQt6.QtCore import QRunnable, QThreadPool

from app.companies.companies_ui import LoginWindow, OfficeManagerApp
from app.db.schema import init_db

DB_NAME = "biuro.db"


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