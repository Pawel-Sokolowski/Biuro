# app/monthly_view/monthly_view_ui.py
import sqlite3

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit, QTabWidget,
    QDialog, QTextEdit, QSpinBox, QMessageBox, QListWidget,
    QListWidgetItem, QInputDialog, QMenu, QSizePolicy, QAbstractItemView
)
from PyQt6.QtGui import QFont, QBrush, QColor
from PyQt6.QtCore import Qt, QTimer, QEvent
from datetime import datetime, timedelta
from typing import Optional

from app.monthly_view.monthly_view_logic import MonthlyViewLogic
from app.db.dao_conn import get_conn

from app.db.dao_notes import get_notes_for_company, save_company_note, update_company_note, delete_company_note

COMPANY_STAGE_OPTIONS = ["Brak", "Komplet dokumentów", "Sprawdzone", "Zaksięgowane", "Wysłane wyniki"]
COMPANY_STATUS_FILTER = ["Aktywne", "Zawieszone", "Archiwalne", "Wszystkie"]

# -------------------------
# Helper: HoverScrollTextEdit
# -------------------------
class HoverScrollTextEdit(QTextEdit):
    """
    QTextEdit z ukrytym paskiem przewijania, który pojawia się tylko przy najechaniu.
    Pozwala przewijać kółkiem tylko gdy kursor jest nad widgetem.
    Nie rozciąga wiersza (fixed height).
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setAcceptRichText(False)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        try:
            self.verticalScrollBar().setStyleSheet("""
                QScrollBar:vertical { width: 10px; background: transparent; margin: 0px; }
                QScrollBar::handle:vertical { background: rgba(0,0,0,0.25); border-radius: 4px; min-height: 20px; }
            """)
        except Exception:
            pass
        self._hover = False
        self.setMouseTracking(True)
        self.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Enter:
            self._hover = True
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        elif event.type() == QEvent.Type.Leave:
            self._hover = False
            self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return super().eventFilter(obj, event)

    def wheelEvent(self, event):
        if self._hover or self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


# -------------------------
# Activity dialog (unchanged)
# -------------------------
class ActivityDialog(QDialog):
    def __init__(self, parent=None, prefer_typ=None):
        super().__init__(parent)
        self.setWindowTitle("Wybierz czynność")
        self.setModal(True)
        self.resize(460, 240)
        self.result_payload = None

        layout = QVBoxLayout(self)

        lbl = QLabel("Rodzaj czynności:")
        layout.addWidget(lbl)

        self.combo_typ = QComboBox()
        self.combo_typ.addItem("Księgowanie", "ksiegowosc")
        self.combo_typ.addItem("Kadry", "kadry")
        self.combo_typ.addItem("Czynności dodatkowe", "extra")
        if prefer_typ == "kadry":
            self.combo_typ.setCurrentIndex(1)
        layout.addWidget(self.combo_typ)

        # extra container
        self.extra_container = QWidget()
        extra_layout = QVBoxLayout(self.extra_container)
        extra_layout.setContentsMargins(0, 0, 0, 0)

        lbl_extra = QLabel("Wybierz usługę dodatkową:")
        extra_layout.addWidget(lbl_extra)

        self.combo_uslugi = QComboBox()
        extra_layout.addWidget(self.combo_uslugi)

        self.chk_fakturowac = QComboBox()
        self.chk_fakturowac.addItem("Nie fakturować", False)
        self.chk_fakturowac.addItem("Fakturować", True)
        extra_layout.addWidget(self.chk_fakturowac)

        layout.addWidget(self.extra_container)

        row = QHBoxLayout()
        row.addStretch()
        btn_ok = QPushButton("OK")
        btn_cancel = QPushButton("Anuluj")
        row.addWidget(btn_ok)
        row.addWidget(btn_cancel)
        layout.addLayout(row)

        btn_ok.clicked.connect(self._on_ok)
        btn_cancel.clicked.connect(self.reject)
        self.combo_typ.currentIndexChanged.connect(self._on_typ_change)

        self._load_services()
        self._on_typ_change(self.combo_typ.currentIndex())

    def _load_services(self):
        conn = get_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT id, nazwa, cena_netto FROM uslugi_dodatkowe ORDER BY nazwa")
        rows = cur.fetchall()
        conn.close()
        self.combo_uslugi.clear()
        for r in rows:
            self.combo_uslugi.addItem(f"{r['nazwa']} — {r['cena_netto']} zł", r["id"])

    def _on_typ_change(self, idx):
        typ = self.combo_typ.currentData()
        self.extra_container.setVisible(typ == "extra")

    def _on_ok(self):
        typ = self.combo_typ.currentData()
        payload = {"typ_czynnosci": typ, "usluga_id": None, "fakturowac": False, "nazwa": None}
        if typ == "extra":
            payload["usluga_id"] = self.combo_uslugi.currentData()
            payload["fakturowac"] = bool(self.chk_fakturowac.currentData())
            payload["nazwa"] = self.combo_uslugi.currentText()
        self.result_payload = payload
        self.accept()

    def get_payload(self):
        return self.result_payload


# -------------------------
# Notes dialog (shared)
# -------------------------
class InlineNotesWindow(QDialog):
    def __init__(self, firma_id: int, parent=None, pracownik_id: Optional[int] = None):
        super().__init__(parent)
        self.firma_id = firma_id
        self.pracownik_id = pracownik_id
        self.setWindowTitle("Notatki firmy")
        self.resize(700, 500)

        layout = QVBoxLayout(self)
        title = QLabel("Notatki")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        layout.addWidget(title)

        # lista notatek
        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)

        # pole do dodania nowej notatki
        self.new_note = QTextEdit()
        self.new_note.setPlaceholderText("Dodaj notatkę...")
        self.new_note.setFixedHeight(120)
        layout.addWidget(self.new_note)

        btn_row = QHBoxLayout()
        self.btn_save = QPushButton("Zapisz")
        self.btn_close = QPushButton("Zamknij")
        btn_row.addWidget(self.btn_save)
        btn_row.addWidget(self.btn_close)
        layout.addLayout(btn_row)

        self.btn_save.clicked.connect(self._save_note)
        self.btn_close.clicked.connect(self.reject)

        # kontekstowe menu dla listy (edycja/usuwanie)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._on_item_context_menu)

        self._refresh_list()

    def _refresh_list(self):
        self.list_widget.clear()
        notes = get_notes_for_company(self.firma_id)
        for n in notes:
            text = f"{n['data_utworzenia']}  {n['autor_nazwa']}\n{n['tresc']}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, n)
            self.list_widget.addItem(item)
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)

    def _save_note(self):
        text = self.new_note.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Błąd", "Notatka nie może być pusta")
            return
        save_company_note(self.firma_id, text, self.pracownik_id)
        self.new_note.clear()
        self._refresh_list()
        QMessageBox.information(self, "Sukces", "Notatka została zapisana")

    def _on_item_context_menu(self, point):
        item = self.list_widget.itemAt(point)
        if not item:
            return
        n = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        if n.get("autor_id") == self.pracownik_id:
            act_edit = menu.addAction("Edytuj")
            act_delete = menu.addAction("Usuń")
            action = menu.exec(self.list_widget.mapToGlobal(point))
            if action == act_edit:
                text, ok = QInputDialog.getMultiLineText(self, "Edytuj notatkę", "Treść:", n["tresc"])
                if ok and text.strip():
                    update_company_note(n["id"], text.strip())
                    self._refresh_list()
                    QMessageBox.information(self, "Sukces", "Notatka została zaktualizowana")
            elif action == act_delete:
                if QMessageBox.question(self, "Usuń", "Na pewno usunąć notatkę?") == QMessageBox.StandardButton.Yes:
                    delete_company_note(n["id"])
                    self._refresh_list()
                    QMessageBox.information(self, "Sukces", "Notatka została usunięta")
        else:
            pass


# -------------------------
# Extra service dialog (unchanged)
# -------------------------
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
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT id, nazwa, cena_netto FROM uslugi_dodatkowe ORDER BY nazwa")
        for uid, nazwa, cena in cur.fetchall():
            self.combo.addItem(f"{nazwa} — {cena} zł", uid)
        conn.close()

    def add_service(self):
        usluga_id = self.combo.currentData()
        ilosc = self.spin.value()
        conn = get_conn()
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


# -------------------------
# Monthly view page
# -------------------------
class MonthlyViewPage(QWidget):
    def __init__(self, pracownik_id):
        super().__init__()
        self.pracownik_id = pracownik_id
        self.logic = MonthlyViewLogic(pracownik_id)

        self.month_selector = None
        self.month_employee_filter = None
        self.status_filter = None

        self.table_ksiegowosc = None
        self.table_kadry = None

        # live labels maps
        self._live_time_labels_ks = {}
        self._live_time_labels_kadry = {}

        # header timer for page-level live updates (if needed)
        self._live_timer = QTimer(self)
        self._live_timer.setInterval(1000)
        self._live_timer.timeout.connect(self._update_live_timers)
        self._live_timer.start()

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

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
        self.month_employee_filter.currentIndexChanged.connect(lambda _: self.refresh_tables())
        header_layout.addWidget(self.month_employee_filter)

        lbl_status = QLabel("Status firmy:")
        lbl_status.setFont(QFont("Arial", 12))
        header_layout.addWidget(lbl_status)

        self.status_filter = QComboBox()
        self.status_filter.addItems(COMPANY_STATUS_FILTER)
        self.status_filter.setCurrentText("Aktywne")
        self.status_filter.currentIndexChanged.connect(lambda _: self.refresh_tables())
        header_layout.addWidget(self.status_filter)

        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self.refresh_tables)
        header_layout.addWidget(btn_refresh)

        layout.addWidget(header_row)

        self.month_tabs = QTabWidget()

        # KSIĘGOWOŚĆ
        tab_ks = QWidget()
        ks_layout = QVBoxLayout(tab_ks)
        lbl_ks_header = QLabel("Księgowość – miesięczny status firm")
        lbl_ks_header.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        ks_layout.addWidget(lbl_ks_header)
        self.table_ksiegowosc = QTableWidget()
        # nowa struktura kolumn: Nazwa, Etap, Ilość dokumentów, Czas, Stoper, Notatka, Usługa, Edytuj, ⋯
        self.table_ksiegowosc.setColumnCount(9)
        self.table_ksiegowosc.setHorizontalHeaderLabels([
            "Nazwa", "Etap", "Ilość dokumentów", "Czas Księgowość",
            "Stoper", "Notatka", "Usługa", "Edytuj", "⋯"
        ])
        self.table_ksiegowosc.setAlternatingRowColors(True)
        ks_layout.addWidget(self.table_ksiegowosc)

        # KADRY
        tab_kadry = QWidget()
        kadry_layout = QVBoxLayout(tab_kadry)
        lbl_kadry_header = QLabel("Kadry – miesięczny status firm")
        lbl_kadry_header.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        kadry_layout.addWidget(lbl_kadry_header)
        self.table_kadry = QTableWidget()
        self.table_kadry.setColumnCount(9)
        self.table_kadry.setHorizontalHeaderLabels([
            "Nazwa", "Etap", "Ilość dokumentów", "Czas Kadry",
            "Stoper", "Notatka", "Usługa", "Edytuj", "⋯"
        ])
        self.table_kadry.setAlternatingRowColors(True)
        kadry_layout.addWidget(self.table_kadry)

        self.month_tabs.addTab(tab_ks, "Księgowość")
        self.month_tabs.addTab(tab_kadry, "Kadry")
        layout.addWidget(self.month_tabs)

        # ustawienia nagłówków i rozciągania kolumn
        hdr_ks = self.table_ksiegowosc.horizontalHeader()
        hdr_ks.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)   # Nazwa
        hdr_ks.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)  # Etap
        hdr_ks.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)  # Ilość dokumentów (fixed)
        hdr_ks.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # Czas
        hdr_ks.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)  # Stoper (fixed)
        hdr_ks.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)  # Notatka
        hdr_ks.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)  # Usługa
        hdr_ks.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)  # Edytuj
        hdr_ks.setSectionResizeMode(8, QHeaderView.ResizeMode.Fixed)  # ⋯ (fixed)
        hdr_ks.setMinimumSectionSize(36)
        hdr_ks.setStretchLastSection(False)
        self.table_ksiegowosc.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

        hdr_kad = self.table_kadry.horizontalHeader()
        hdr_kad.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr_kad.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr_kad.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        hdr_kad.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr_kad.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        hdr_kad.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        hdr_kad.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        hdr_kad.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        hdr_kad.setSectionResizeMode(8, QHeaderView.ResizeMode.Fixed)
        hdr_kad.setMinimumSectionSize(36)
        hdr_kad.setStretchLastSection(False)
        self.table_kadry.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)

        # ustaw domyślne szerokości dla fixed columns (dostosuj wartości jeśli trzeba)
        self.table_ksiegowosc.setColumnWidth(2, 120)   # Ilość dokumentów (szeroka kratka)
        self.table_ksiegowosc.setColumnWidth(4, 44)    # Stoper
        self.table_ksiegowosc.setColumnWidth(8, 36)    # ⋯

        self.table_kadry.setColumnWidth(2, 120)
        self.table_kadry.setColumnWidth(4, 44)
        self.table_kadry.setColumnWidth(8, 36)

        self.refresh_monthly_filters()
        self.refresh_tables()

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

    def refresh_tables(self):
        rows = self.logic.get_monthly_rows(
            selected_month=self.month_selector.currentText(),
            selected_employee=self.month_employee_filter.currentData(),
            selected_status=self.status_filter.currentText()
        )
        self._render_table_ksiegowosc(rows)
        self._render_table_kadry(rows)

    def _render_table_ksiegowosc(self, rows):
        self.table_ksiegowosc.setRowCount(0)
        self.logic.status_widgets = {}
        self._live_time_labels_ks = {}

        ROW_HEIGHT = 56  # stała wysokość wiersza, zapobiega rozciąganiu przez notatkę

        for row_idx, row in enumerate(rows):
            f_id = row["id"]
            nazwa = row["nazwa"]
            etap = row["etap"]
            ilosc_dok = row["ilosc_dok"]
            sekundy = row["sekundy_ksiegowosc"]

            self.table_ksiegowosc.insertRow(row_idx)

            # Nazwa
            item_nazwa = QTableWidgetItem(nazwa)
            self.table_ksiegowosc.setItem(row_idx, 0, item_nazwa)

            # Etap (combo)
            cb_etap = QComboBox()
            cb_etap.addItems(COMPANY_STAGE_OPTIONS)
            cb_etap.setCurrentText(etap)
            cb_etap.currentTextChanged.connect(lambda _txt, fid=f_id: self.logic.schedule_status_autosave(fid))
            self.table_ksiegowosc.setCellWidget(row_idx, 1, cb_etap)

            # Ilość dokumentów (wypełnia całą kolumnę)
            txt_docs = QLineEdit(str(ilosc_dok))
            txt_docs.setAlignment(Qt.AlignmentFlag.AlignCenter)
            txt_docs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            txt_docs.textChanged.connect(lambda _txt, fid=f_id: self.logic.schedule_status_autosave(fid))
            self.table_ksiegowosc.setCellWidget(row_idx, 2, txt_docs)

            # Czas (label)
            czas_format = self.logic.format_seconds(sekundy)
            lbl_time = QLabel(czas_format)
            lbl_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_time.setProperty("base_seconds", int(sekundy or 0))
            lbl_time.setProperty("firma_id", f_id)
            lbl_time.setProperty("typ", "ksiegowosc")
            self.table_ksiegowosc.setCellWidget(row_idx, 3, lbl_time)
            self._live_time_labels_ks[f_id] = lbl_time

            # Stoper (wąski przycisk)
            btn_stoper = QPushButton("▶" if self.logic.aktywna_firma_stoper_ks != f_id else "⏸")
            btn_stoper.setFixedSize(36, 28)
            btn_stoper.clicked.connect(lambda checked, fid=f_id, btn=btn_stoper: self._toggle_stoper(fid, btn, "ksiegowosc"))
            self.table_ksiegowosc.setCellWidget(row_idx, 4, btn_stoper)

            # Notatka (HoverScrollTextEdit) - stała wysokość, nie rozciąga wiersza
            note_widget = HoverScrollTextEdit()
            try:
                note_widget.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
            except Exception:
                note_widget.setLineWrapMode(QTextEdit.LineWrapMode.FixedPixelWidth)
                note_widget.setLineWrapColumnOrWidth(200)
            note_widget.setFixedHeight(48)
            note_widget.setText(row.get("note_snippet", ""))
            def _on_double_click(event, fid=f_id):
                dlg = InlineNotesWindow(fid, parent=self, pracownik_id=self.pracownik_id)
                dlg.exec()
                self.refresh_tables()
            note_widget.mouseDoubleClickEvent = _on_double_click
            self.table_ksiegowosc.setCellWidget(row_idx, 5, note_widget)

            # Usługa
            btn_usluga = QPushButton("Usługa")
            btn_usluga.clicked.connect(lambda checked, fid=f_id: self.logic.open_extra_service_window(fid))
            self.table_ksiegowosc.setCellWidget(row_idx, 6, btn_usluga)

            # Edytuj
            btn_edit = QPushButton("Edytuj")
            btn_edit.clicked.connect(lambda checked, fid=f_id: self.logic.open_company_edit_window(fid))
            self.table_ksiegowosc.setCellWidget(row_idx, 7, btn_edit)

            # More
            btn_more = QPushButton("⋯")
            btn_more.setFixedSize(36, 28)
            btn_more.clicked.connect(lambda checked, fid=f_id, btn=btn_more: self._open_more_menu(fid, btn))
            self.table_ksiegowosc.setCellWidget(row_idx, 8, btn_more)

            # store widgets for autosave
            self.logic.status_widgets[f_id] = (txt_docs, cb_etap)

            # ustaw stałą wysokość wiersza, żeby notatka nie rozpychała
            self.table_ksiegowosc.setRowHeight(row_idx, ROW_HEIGHT)

    def _render_table_kadry(self, rows):
        self.table_kadry.setRowCount(0)
        self._live_time_labels_kadry = {}

        ROW_HEIGHT = 56

        for row_idx, row in enumerate(rows):
            f_id = row["id"]
            nazwa = row["nazwa"]
            etap = row["etap"]
            ilosc_dok = row["ilosc_dok"]
            sekundy = row["sekundy_kadry"]

            self.table_kadry.insertRow(row_idx)
            self.table_kadry.setItem(row_idx, 0, QTableWidgetItem(nazwa))

            cb_etap = QComboBox()
            cb_etap.addItems(COMPANY_STAGE_OPTIONS)
            cb_etap.setCurrentText(etap)
            cb_etap.currentTextChanged.connect(lambda _txt, fid=f_id: self.logic.schedule_status_autosave(fid))
            self.table_kadry.setCellWidget(row_idx, 1, cb_etap)

            txt_docs = QLineEdit(str(ilosc_dok))
            txt_docs.setAlignment(Qt.AlignmentFlag.AlignCenter)
            txt_docs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            txt_docs.textChanged.connect(lambda _txt, fid=f_id: self.logic.schedule_status_autosave(fid))
            self.table_kadry.setCellWidget(row_idx, 2, txt_docs)

            czas_format = self.logic.format_seconds(sekundy)
            lbl_time = QLabel(czas_format)
            lbl_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_time.setProperty("base_seconds", int(sekundy or 0))
            lbl_time.setProperty("firma_id", f_id)
            lbl_time.setProperty("typ", "kadry")
            self.table_kadry.setCellWidget(row_idx, 3, lbl_time)
            self._live_time_labels_kadry[f_id] = lbl_time

            btn_stoper = QPushButton("▶" if self.logic.aktywna_firma_stoper_kadry != f_id else "⏸")
            btn_stoper.setFixedSize(36, 28)
            btn_stoper.clicked.connect(lambda checked, fid=f_id, btn=btn_stoper: self._toggle_stoper(fid, btn, "kadry"))
            self.table_kadry.setCellWidget(row_idx, 4, btn_stoper)

            note_widget = HoverScrollTextEdit()
            try:
                note_widget.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
            except Exception:
                note_widget.setLineWrapMode(QTextEdit.LineWrapMode.FixedPixelWidth)
                note_widget.setLineWrapColumnOrWidth(200)
            note_widget.setFixedHeight(48)
            note_widget.setText(row.get("note_snippet", ""))
            def _on_double_click_kadry(event, fid=f_id):
                dlg = InlineNotesWindow(fid, parent=self, pracownik_id=self.pracownik_id)
                dlg.exec()
                self.refresh_tables()
            note_widget.mouseDoubleClickEvent = _on_double_click_kadry
            self.table_kadry.setCellWidget(row_idx, 5, note_widget)

            btn_usluga = QPushButton("Usługa")
            btn_usluga.clicked.connect(lambda checked, fid=f_id: self.logic.open_extra_service_window(fid))
            self.table_kadry.setCellWidget(row_idx, 6, btn_usluga)

            btn_edit = QPushButton("Edytuj")
            btn_edit.clicked.connect(lambda checked, fid=f_id: self.logic.open_company_edit_window(fid))
            self.table_kadry.setCellWidget(row_idx, 7, btn_edit)

            btn_more = QPushButton("⋯")
            btn_more.setFixedSize(36, 28)
            btn_more.clicked.connect(lambda checked, fid=f_id, btn=btn_more: self._open_more_menu(fid, btn))
            self.table_kadry.setCellWidget(row_idx, 8, btn_more)

            self.table_kadry.setRowHeight(row_idx, ROW_HEIGHT)

    def _toggle_stoper(self, firma_id: int, btn: QPushButton, typ: str):
        logic = self.logic
        active = logic.aktywna_firma_stoper_ks if typ == "ksiegowosc" else logic.aktywna_firma_stoper_kadry

        if active is None:
            dlg = ActivityDialog(self, prefer_typ=("kadry" if typ == "kadry" else "ksiegowosc"))
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            payload = dlg.get_payload()
            logic.toggle_stoper(firma_id, typ, activity_payload=payload)
        else:
            logic.toggle_stoper(firma_id, typ, activity_payload=None)

        if typ == "ksiegowosc":
            active_now = logic.aktywna_firma_stoper_ks
        else:
            active_now = logic.aktywna_firma_stoper_kadry

        btn.setText("⏸" if active_now == firma_id else "▶")
        self.refresh_tables()

    def _open_more_menu(self, firma_id: int, btn: QPushButton):
        menu = QMenu(btn)
        act_zawies = menu.addAction("Dodaj zawieszenie")
        act_arch = menu.addAction("Przenieś do archiwalnych")
        menu.addSeparator()
        act_opt1 = menu.addAction("Opcja 1")
        act_opt2 = menu.addAction("Opcja 2")
        act_opt3 = menu.addAction("Opcja 3")

        action = menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))
        if action == act_zawies:
            self.logic.mark_company_suspended_manual(firma_id)
        elif action == act_arch:
            self.logic.move_company_to_archive(firma_id)
        elif action == act_opt1:
            self.logic.more_option_1(firma_id)
        elif action == act_opt2:
            self.logic.more_option_2(firma_id)
        elif action == act_opt3:
            self.logic.more_option_3(firma_id)

    def _update_live_timers(self):
        now = datetime.now()

        def update_label(lbl):
            try:
                base = int(lbl.property("base_seconds") or 0)
                fid = lbl.property("firma_id")
                typ = lbl.property("typ")
            except Exception:
                return

            delta = 0
            if typ == "ksiegowosc":
                if self.logic.aktywna_firma_stoper_ks == fid and self.logic.czas_start_ks:
                    delta = int((now - self.logic.czas_start_ks).total_seconds())
            else:
                if self.logic.aktywna_firma_stoper_kadry == fid and self.logic.czas_start_kadry:
                    delta = int((now - self.logic.czas_start_kadry).total_seconds())

            total = base + delta
            lbl.setText(self.logic.format_seconds(total))

        for lbl in list(self._live_time_labels_ks.values()):
            update_label(lbl)
        for lbl in list(self._live_time_labels_kadry.values()):
            update_label(lbl)
