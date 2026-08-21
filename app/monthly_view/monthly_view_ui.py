# app/ui/monthly_view_ui.py
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QTabWidget, QTableWidget, QHeaderView, QTableWidgetItem, QLineEdit
)
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt
from datetime import datetime, timedelta

# LOGIKA (Twój drugi plik)
from app.monthly_view.monthly_view_logic import MonthlyViewLogic

# Stałe UI
COMPANY_STAGE_OPTIONS = ["Brak", "Start", "W trakcie", "Zakończone"]


class MonthlyViewPage(QWidget):
    def __init__(self, pracownik_id):
        super().__init__()
        self.pracownik_id = pracownik_id
        self.logic = MonthlyViewLogic(pracownik_id)

        # elementy UI
        self.month_selector = None
        self.month_employee_filter = None
        self.month_tabs = None
        self.table_monthly = None

        # stoper
        self.aktywna_firma_stoper = None
        self.aktywna_firma_stoper_nazwa = ""
        self.czas_start = None

        self._build_ui()

    # ============================================================
    # UI
    # ============================================================
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ============================
        # HEADER
        # ============================
        header_row = QWidget()
        header_layout = QHBoxLayout(header_row)

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
        self.month_selector.setCurrentText(today.strftime("%Y-%m"))
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
        header_layout.addWidget(btn_refresh)

        layout.addWidget(header_row)

        # ============================
        # TABS
        # ============================
        self.month_tabs = QTabWidget()

        # --- KSIĘGOWOŚĆ ---
        tab_ks = QWidget()
        ks_layout = QVBoxLayout(tab_ks)

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
        self.table_monthly.hideColumn(0)

        ks_layout.addWidget(self.table_monthly)

        # --- KADRY ---
        tab_kadry = QWidget()
        kadry_layout = QVBoxLayout(tab_kadry)
        kadry_layout.addWidget(QLabel("Widok Kadrowy — w przygotowaniu"))

        self.month_tabs.addTab(tab_ks, "Księgowość")
        self.month_tabs.addTab(tab_kadry, "Kadry")

        layout.addWidget(self.month_tabs)

        # Initial load
        self.refresh_monthly_filters()
        self.refresh_monthly_table()

    # ============================================================
    # UI → LOGIKA
    # ============================================================

    def refresh_monthly_filters(self):
        """UI wrapper — logika w osobnym pliku."""
        employees = self.logic.get_employee_filter_list()

        self.month_employee_filter.blockSignals(True)
        self.month_employee_filter.clear()
        self.month_employee_filter.addItem("Wszyscy", None)

        for emp_id, label in employees:
            self.month_employee_filter.addItem(label, emp_id)

        self.month_employee_filter.blockSignals(False)

    def _change_month(self, new_month: str):
        """UI wrapper."""
        self.logic.set_month(new_month)
        self.refresh_monthly_table()

    def refresh_monthly_table(self):
        """UI wrapper — pobiera dane z logiki i renderuje tabelę."""
        rows = self.logic.get_monthly_rows(
            selected_month=self.month_selector.currentText(),
            selected_employee=self.month_employee_filter.currentData()
        )

        self.table_monthly.setRowCount(0)
        self.logic.status_widgets = {}

        for row_idx, row in enumerate(rows):
            f_id = row["id"]
            nazwa = row["nazwa"]
            etap = row["etap"]
            ilosc_dok = row["ilosc_dok"]
            sekundy = row["sekundy"]

            self.table_monthly.insertRow(row_idx)

            # 0: ID
            self.table_monthly.setItem(row_idx, 0, QTableWidgetItem(str(f_id)))

            # 1: Nazwa
            self.table_monthly.setItem(row_idx, 1, QTableWidgetItem(nazwa))

            # 2: Etap
            cb_etap = QComboBox()
            cb_etap.addItems(COMPANY_STAGE_OPTIONS)
            cb_etap.setCurrentText(etap)
            cb_etap.currentTextChanged.connect(
                lambda _txt, fid=f_id: self.logic.schedule_status_autosave(fid)
            )
            self.table_monthly.setCellWidget(row_idx, 2, cb_etap)

            # 3: Ilość dokumentów
            txt_docs = QLineEdit(str(ilosc_dok))
            txt_docs.setAlignment(Qt.AlignmentFlag.AlignCenter)
            txt_docs.setFixedWidth(80)
            txt_docs.textChanged.connect(
                lambda _txt, fid=f_id: self.logic.schedule_status_autosave(fid)
            )
            self.table_monthly.setCellWidget(row_idx, 3, txt_docs)

            # 4: Czas pracy
            czas_format = self.logic.format_seconds(sekundy)
            lbl_time = QLabel(czas_format)
            lbl_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table_monthly.setCellWidget(row_idx, 4, lbl_time)

            # 5: Stoper
            btn_stoper = QPushButton("▶" if self.logic.aktywna_firma_stoper != f_id else "⏸")
            btn_stoper.setFixedSize(36, 28)
            btn_stoper.clicked.connect(
                lambda checked, fid=f_id, btn=btn_stoper: self.logic.toggle_stoper(fid, btn)
            )
            self.table_monthly.setCellWidget(row_idx, 5, btn_stoper)

            # 6: Notatka
            lbl_note = QLabel(row["note_snippet"])
            lbl_note.setWordWrap(True)
            lbl_note.mouseDoubleClickEvent = lambda event, fid=f_id: self.logic.open_notes_window(fid)
            self.table_monthly.setCellWidget(row_idx, 6, lbl_note)

            # 7: Usługa dodatkowa
            btn_usluga = QPushButton("Usługa")
            btn_usluga.clicked.connect(lambda checked, fid=f_id: self.logic.open_extra_service_window(fid))
            self.table_monthly.setCellWidget(row_idx, 7, btn_usluga)

            # 8: Edytuj firmę
            btn_edit = QPushButton("Edytuj")
            btn_edit.clicked.connect(lambda checked, fid=f_id: self.logic.open_company_edit_window(fid))
            self.table_monthly.setCellWidget(row_idx, 8, btn_edit)

            self.logic.status_widgets[f_id] = (txt_docs, cb_etap)
