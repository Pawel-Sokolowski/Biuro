# app/ui/monthly_view_ui.py
from app.utils.ui_imports import *
from PyQt6.QtGui import QFont
from PyQt6.QtCore import Qt
from datetime import datetime, timedelta
from app.monthly_view.monthly_view_logic import MonthlyViewLogic

COMPANY_STAGE_OPTIONS = ["Brak", "Komplet dokumentów", "Sprawdzone", "Zaksięgowane", "Wysłane wyniki"]


class MonthlyViewPage(QWidget):
    def __init__(self, pracownik_id):
        super().__init__()
        self.pracownik_id = pracownik_id
        self.logic = MonthlyViewLogic(pracownik_id)

        # UI elements
        self.month_selector = None
        self.month_employee_filter = None

        # tables
        self.table_ksiegowosc = None
        self.table_kadry = None

        self._build_ui()

    # ============================================================
    # BUILD UI
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
            lambda _: self.refresh_tables()
        )
        header_layout.addWidget(self.month_employee_filter)

        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self.refresh_tables)
        header_layout.addWidget(btn_refresh)

        layout.addWidget(header_row)

        # ============================
        # TABS
        # ============================
        self.month_tabs = QTabWidget()

        # --- KSIĘGOWOŚĆ ---
        tab_ks = QWidget()
        ks_layout = QVBoxLayout(tab_ks)

        self.table_ksiegowosc = QTableWidget()
        self.table_ksiegowosc.setColumnCount(9)
        self.table_ksiegowosc.setHorizontalHeaderLabels([
            "ID",
            "Nazwa",
            "Etap",
            "Ilość dokumentów",
            "Czas Księgowość",
            "Stoper",
            "Notatka",
            "Usługa",
            "Edytuj"
        ])
        self.table_ksiegowosc.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.table_ksiegowosc.hideColumn(0)
        ks_layout.addWidget(self.table_ksiegowosc)

        # --- KADRY ---
        tab_kadry = QWidget()
        kadry_layout = QVBoxLayout(tab_kadry)

        self.table_kadry = QTableWidget()
        self.table_kadry.setColumnCount(9)
        self.table_kadry.setHorizontalHeaderLabels([
            "ID",
            "Nazwa",
            "Etap",
            "Ilość dokumentów",
            "Czas Kadry",
            "Stoper",
            "Notatka",
            "Usługa",
            "Edytuj"
        ])
        self.table_kadry.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.table_kadry.hideColumn(0)
        kadry_layout.addWidget(self.table_kadry)

        self.month_tabs.addTab(tab_ks, "Księgowość")
        self.month_tabs.addTab(tab_kadry, "Kadry")

        layout.addWidget(self.month_tabs)

        # Initial load
        self.refresh_monthly_filters()
        self.refresh_tables()

    # ============================================================
    # FILTERS
    # ============================================================
    def refresh_monthly_filters(self):
        employees = self.logic.get_employee_filter_list()

        self.month_employee_filter.blockSignals(True)
        self.month_employee_filter.clear()
        self.month_employee_filter.addItem("Wszyscy", None)

        for emp_id, label in employees:
            self.month_employee_filter.addItem(label, emp_id)

        self.month_employee_filter.blockSignals(False)

    def _change_month(self, new_month: str):
        self.logic.set_month(new_month)
        self.refresh_tables()

    # ============================================================
    # REFRESH BOTH TABLES
    # ============================================================
    def refresh_tables(self):
        rows = self.logic.get_monthly_rows(
            selected_month=self.month_selector.currentText(),
            selected_employee=self.month_employee_filter.currentData()
        )

        self._render_table_ksiegowosc(rows)
        self._render_table_kadry(rows)

    # ============================================================
    # RENDER KSIĘGOWOŚĆ
    # ============================================================
    def _render_table_ksiegowosc(self, rows):
        self.table_ksiegowosc.setRowCount(0)
        self.logic.status_widgets = {}

        for row_idx, row in enumerate(rows):
            f_id = row["id"]
            nazwa = row["nazwa"]
            etap = row["etap"]
            ilosc_dok = row["ilosc_dok"]
            sekundy = row["sekundy_ksiegowosc"]

            self.table_ksiegowosc.insertRow(row_idx)

            # 0: ID
            self.table_ksiegowosc.setItem(row_idx, 0, QTableWidgetItem(str(f_id)))

            # 1: Nazwa
            self.table_ksiegowosc.setItem(row_idx, 1, QTableWidgetItem(nazwa))

            # 2: Etap
            cb_etap = QComboBox()
            cb_etap.addItems(COMPANY_STAGE_OPTIONS)
            cb_etap.setCurrentText(etap)
            cb_etap.currentTextChanged.connect(
                lambda _txt, fid=f_id: self.logic.schedule_status_autosave(fid)
            )
            self.table_ksiegowosc.setCellWidget(row_idx, 2, cb_etap)

            # 3: Ilość dokumentów
            txt_docs = QLineEdit(str(ilosc_dok))
            txt_docs.setAlignment(Qt.AlignmentFlag.AlignCenter)
            txt_docs.setFixedWidth(80)
            txt_docs.textChanged.connect(
                lambda _txt, fid=f_id: self.logic.schedule_status_autosave(fid)
            )
            self.table_ksiegowosc.setCellWidget(row_idx, 3, txt_docs)

            # 4: Czas Księgowość
            czas_format = self.logic.format_seconds(sekundy)
            lbl_time = QLabel(czas_format)
            lbl_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table_ksiegowosc.setCellWidget(row_idx, 4, lbl_time)

            # 5: Stoper
            btn_stoper = QPushButton(
                "▶" if self.logic.aktywna_firma_stoper_ks != f_id else "⏸"
            )
            btn_stoper.setFixedSize(36, 28)
            btn_stoper.clicked.connect(
                lambda checked, fid=f_id, btn=btn_stoper:
                    self._toggle_stoper(fid, btn, "ksiegowosc")
            )
            self.table_ksiegowosc.setCellWidget(row_idx, 5, btn_stoper)

            # 6: Notatka
            lbl_note = QLabel(row["note_snippet"])
            lbl_note.setWordWrap(True)
            lbl_note.mouseDoubleClickEvent = lambda event, fid=f_id: self.logic.open_notes_window(fid)
            self.table_ksiegowosc.setCellWidget(row_idx, 6, lbl_note)

            # 7: Usługa dodatkowa
            btn_usluga = QPushButton("Usługa")
            btn_usluga.clicked.connect(lambda checked, fid=f_id: self.logic.open_extra_service_window(fid))
            self.table_ksiegowosc.setCellWidget(row_idx, 7, btn_usluga)

            # 8: Edytuj firmę
            btn_edit = QPushButton("Edytuj")
            btn_edit.clicked.connect(lambda checked, fid=f_id: self.logic.open_company_edit_window(fid))
            self.table_ksiegowosc.setCellWidget(row_idx, 8, btn_edit)

            self.logic.status_widgets[f_id] = (txt_docs, cb_etap)

    # ============================================================
    # RENDER KADRY
    # ============================================================
    def _render_table_kadry(self, rows):
        self.table_kadry.setRowCount(0)

        for row_idx, row in enumerate(rows):
            f_id = row["id"]
            nazwa = row["nazwa"]
            etap = row["etap"]
            ilosc_dok = row["ilosc_dok"]
            sekundy = row["sekundy_kadry"]

            self.table_kadry.insertRow(row_idx)

            # 0: ID
            self.table_kadry.setItem(row_idx, 0, QTableWidgetItem(str(f_id)))

            # 1: Nazwa
            self.table_kadry.setItem(row_idx, 1, QTableWidgetItem(nazwa))

            # 2: Etap
            cb_etap = QComboBox()
            cb_etap.addItems(COMPANY_STAGE_OPTIONS)
            cb_etap.setCurrentText(etap)
            cb_etap.currentTextChanged.connect(
                lambda _txt, fid=f_id: self.logic.schedule_status_autosave(fid)
            )
            self.table_kadry.setCellWidget(row_idx, 2, cb_etap)

            # 3: Ilość dokumentów
            txt_docs = QLineEdit(str(ilosc_dok))
            txt_docs.setAlignment(Qt.AlignmentFlag.AlignCenter)
            txt_docs.setFixedWidth(80)
            txt_docs.textChanged.connect(
                lambda _txt, fid=f_id: self.logic.schedule_status_autosave(fid)
            )
            self.table_kadry.setCellWidget(row_idx, 3, txt_docs)

            # 4: Czas Kadry
            czas_format = self.logic.format_seconds(sekundy)
            lbl_time = QLabel(czas_format)
            lbl_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table_kadry.setCellWidget(row_idx, 4, lbl_time)

            # 5: Stoper
            btn_stoper = QPushButton(
                "▶" if self.logic.aktywna_firma_stoper_kadry != f_id else "⏸"
            )
            btn_stoper.setFixedSize(36, 28)
            btn_stoper.clicked.connect(
                lambda checked, fid=f_id, btn=btn_stoper:
                    self._toggle_stoper(fid, btn, "kadry")
            )
            self.table_kadry.setCellWidget(row_idx, 5, btn_stoper)

            # 6: Notatka
            lbl_note = QLabel(row["note_snippet"])
            lbl_note.setWordWrap(True)
            lbl_note.mouseDoubleClickEvent = lambda event, fid=f_id: self.logic.open_notes_window(fid)
            self.table_kadry.setCellWidget(row_idx, 6, lbl_note)

            # 7: Usługa dodatkowa
            btn_usluga = QPushButton("Usługa")
            btn_usluga.clicked.connect(lambda checked, fid=f_id: self.logic.open_extra_service_window(fid))
            self.table_kadry.setCellWidget(row_idx, 7, btn_usluga)

            # 8: Edytuj firmę
            btn_edit = QPushButton("Edytuj")
            btn_edit.clicked.connect(lambda checked, fid=f_id: self.logic.open_company_edit_window(fid))
            self.table_kadry.setCellWidget(row_idx, 8, btn_edit)

    # ============================================================
    # STOPER BUTTON HANDLER
    # ============================================================
    def _toggle_stoper(self, firma_id: int, btn: QPushButton, typ: str):
        self.logic.toggle_stoper(firma_id, typ)

        if typ == "ksiegowosc":
            active = self.logic.aktywna_firma_stoper_ks
        else:
            active = self.logic.aktywna_firma_stoper_kadry

        btn.setText("⏸" if active == firma_id else "▶")

        self.refresh_tables()

# ============================================================
#  INLINE NOTES WINDOW (bez osobnego pliku)
# ============================================================
class InlineNotesWindow(QDialog):
    def __init__(self, firma_id: int):
        super().__init__()
        self.firma_id = firma_id

        self.setWindowTitle("Notatki firmy")
        self.resize(600, 400)

        layout = QVBoxLayout(self)

        title = QLabel("Notatki")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        layout.addWidget(title)

        self.text_area = QTextEdit()
        layout.addWidget(self.text_area)

        btn_save = QPushButton("Zapisz notatkę")
        btn_save.clicked.connect(self.save_note)
        layout.addWidget(btn_save)

        self.load_notes()

    def load_notes(self):
        import sqlite3
        from app.config import DB_NAME

        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("""
            SELECT tresc
            FROM company_notes
            WHERE firma_id = ?
            ORDER BY data_utworzenia DESC
            LIMIT 1
        """, (self.firma_id,))
        row = cur.fetchone()
        conn.close()

        if row:
            self.text_area.setText(row[0])

    def save_note(self):
        text = self.text_area.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Błąd", "Notatka nie może być pusta")
            return

        import sqlite3
        from app.config import DB_NAME

        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO company_notes (firma_id, tresc)
            VALUES (?, ?)
        """, (self.firma_id, text))
        conn.commit()
        conn.close()

        QMessageBox.information(self, "Sukces", "Notatka została zapisana")
        self.accept()


# ============================================================
#  INLINE EXTRA SERVICE WINDOW (bez osobnego pliku)
# ============================================================
class InlineExtraServiceWindow(QDialog):
    def __init__(self, firma_id: int, miesiac: str):
        super().__init__()
        self.firma_id = firma_id
        self.miesiac = miesiac

        self.setWindowTitle("Usługi dodatkowe")
        self.resize(500, 300)

        layout = QVBoxLayout(self)

        title = QLabel("Dodaj usługę dodatkową")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        layout.addWidget(title)

        self.combo = QComboBox()
        self._load_services()
        layout.addWidget(self.combo)

        self.spin = QSpinBox()
        self.spin.setMinimum(1)
        self.spin.setMaximum(999)
        layout.addWidget(self.spin)

        btn_add = QPushButton("Dodaj usługę")
        btn_add.clicked.connect(self.add_service)
        layout.addWidget(btn_add)

    def _load_services(self):
        import sqlite3
        from app.config import DB_NAME

        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("SELECT id, nazwa, cena_netto FROM uslugi_dodatkowe ORDER BY nazwa")
        for uid, nazwa, cena in cur.fetchall():
            self.combo.addItem(f"{nazwa} — {cena} zł", uid)
        conn.close()

    def add_service(self):
        usluga_id = self.combo.currentData()
        ilosc = self.spin.value()

        import sqlite3
        from app.config import DB_NAME

        conn = sqlite3.connect(DB_NAME)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO uslugi_dodatkowe_firma (firma_id, miesiac, usluga_id, ilosc, cena_netto)
            SELECT ?, ?, id, ?, cena_netto
            FROM uslugi_dodatkowe
            WHERE id = ?
        """, (self.firma_id, self.miesiac, ilosc, usluga_id))
        conn.commit()
        conn.close()

        QMessageBox.information(self, "Sukces", "Usługa została dodana")
        self.accept()
