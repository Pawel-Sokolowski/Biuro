# app/ui/monthly_view_ui.py
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QTabWidget, QTableWidget, QHeaderView
)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt
from datetime import datetime, timedelta


class MonthlyViewPage(QWidget):
    def __init__(self, pracownik_id):
        super().__init__()
        self.pracownik_id = pracownik_id

        # elementy używane później
        self.month_selector = None
        self.month_employee_filter = None
        self.month_tabs = None
        self.table_monthly = None

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ============================
        # HEADER
        # ============================
        header_row = QWidget()
        header_layout = QHBoxLayout(header_row)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(12)

        lbl_month = QLabel("Miesiąc rozliczeniowy:")
        lbl_month.setFont(QFont("Arial", 12))
        header_layout.addWidget(lbl_month)

        today = datetime.today()
        self.month_selector = QComboBox()
        months = []

        for i in range(0, 24):
            d = (today.replace(day=1) - timedelta(days=30 * i))
            months.append(d.strftime("%Y-%m"))

        months = list(dict.fromkeys(months))
        months.sort(reverse=True)

        self.month_selector.addItems(months)
        cur_month = today.strftime("%Y-%m")
        if cur_month in months:
            self.month_selector.setCurrentText(cur_month)
        else:
            self.month_selector.setCurrentIndex(0)

        self.month_selector.currentTextChanged.connect(self._change_month)
        header_layout.addWidget(self.month_selector)

        lbl_emp = QLabel("Filtr pracownika:")
        lbl_emp.setFont(QFont("Arial", 12))
        header_layout.addWidget(lbl_emp)

        self.month_employee_filter = QComboBox()
        self.month_employee_filter.addItem("Wszyscy", None)
        self.month_employee_filter.currentIndexChanged.connect(
            lambda _: self.refresh_monthly_table()
        )
        header_layout.addWidget(self.month_employee_filter)

        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self.refresh_monthly_table)
        header_layout.addWidget(btn_refresh, alignment=Qt.AlignmentFlag.AlignRight)

        layout.addWidget(header_row)

        # ============================
        # TABS
        # ============================
        self.month_tabs = QTabWidget()

        # --- KSIĘGOWOŚĆ ---
        tab_ks = QWidget()
        ks_layout = QVBoxLayout(tab_ks)
        ks_layout.setContentsMargins(0, 0, 0, 0)

        self.table_monthly = QTableWidget()
        self.table_monthly.setColumnCount(9)
        self.table_monthly.setHorizontalHeaderLabels([
            "ID",
            "Nazwa",
            "Etap",
            "Ilość dokumentów",
            "Czas pracy (suma)",
            "Stoper",
            "Notatka",
            "Usługa",
            "Edytuj"
        ])
        self.table_monthly.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )

        try:
            self.table_monthly.hideColumn(0)
        except Exception:
            pass

        ks_layout.addWidget(self.table_monthly)

        # --- KADRY ---
        tab_kadry = QWidget()
        kadry_layout = QVBoxLayout(tab_kadry)
        kadry_layout.setContentsMargins(0, 0, 0, 0)
        kadry_layout.addWidget(QLabel("Widok Kadrowy — w przygotowaniu"))

        self.month_tabs.addTab(tab_ks, "Księgowość")
        self.month_tabs.addTab(tab_kadry, "Kadry")

        layout.addWidget(self.month_tabs)

        try:
            self.refresh_monthly_filters()
        except Exception:
            pass

        try:
            self.refresh_monthly_table()
        except Exception:
            pass
