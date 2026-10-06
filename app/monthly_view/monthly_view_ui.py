from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, QModelIndex, QTimer
from PyQt6.QtGui import QAction, QIntValidator, QCursor
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QTabWidget,
    QTableView,
    QHeaderView,
    QAbstractItemView,
    QLineEdit,
    QMenu,
    QDialog,
    QTextEdit,
    QFormLayout,
    QMessageBox,
    QCheckBox,
)

from app.monthly_view.monthly_view_logic import MonthlyViewLogic
from app.db.dao_notes import (
    get_notes_for_company,
    save_company_note,
    update_company_note,
    delete_company_note,
)
from app.admin.global_settings_logic import GlobalSettingsLogic
from app.db.dao_invoices import add_invoice_item_for_service
from app.utils.timer import TimerUtils
from app.monthly_view.monthly_view_model import MonthlyViewModel
from app.monthly_view.monthly_view_delegates import ButtonDelegate, ScrollableNotesDelegate

from app.monthly_view.notes_window import NotesWindow
from app.monthly_view.note_edit_dialog import NoteEditDialog

COMPANY_STAGE_OPTIONS = ["Brak", "W trakcie", "Zakończone"]
COMPANY_STATUS_FILTER = ["Aktywne", "Zawieszone", "Archiwalne", "Wszystkie"]


class HoverScrollTextEdit(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMinimumHeight(40)
        self.setMaximumHeight(120)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setStyleSheet("QTextEdit { background: #f9f9f9; }")

    def set_text(self, text: str):
        self.setPlainText(text or "")


class ActivitySelectionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Wybór czynności")
        self.resize(500, 300)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.cb_typ = QComboBox()
        self.cb_typ.addItems(["Księgowość", "Kadry", "Czynności dodatkowe"])

        self.cb_usluga = QComboBox()
        self._load_extra_services()

        self.input_cena = QLineEdit()
        self.input_cena.setReadOnly(True)

        self.chk_fakturowac = QCheckBox("Fakturować?")

        form.addRow("Typ pracy:", self.cb_typ)
        form.addRow("Usługa dodatkowa:", self.cb_usluga)
        form.addRow("Cena domyślna netto:", self.input_cena)
        form.addRow(self.chk_fakturowac)

        layout.addLayout(form)

        btns = QHBoxLayout()
        btn_start = QPushButton("Start")
        btn_cancel = QPushButton("Anuluj")
        btn_start.clicked.connect(self.accept)
        btn_cancel.clicked.connect(self.reject)
        btns.addStretch()
        btns.addWidget(btn_start)
        btns.addWidget(btn_cancel)
        layout.addLayout(btns)

        self.cb_usluga.currentIndexChanged.connect(self._on_service_changed)

    def _load_extra_services(self):
        logic = GlobalSettingsLogic()
        rows = logic.get_extra_services()
        self.cb_usluga.clear()
        for r in rows:
            self.cb_usluga.addItem(r.get("nazwa", ""), r.get("id"))

    def _on_service_changed(self, idx: int):
        logic = GlobalSettingsLogic()
        rows = logic.get_extra_services()
        if idx < 0 or idx >= len(rows):
            self.input_cena.setText("")
            return
        cena = float(rows[idx].get("cena_netto") or 0.0)
        self.input_cena.setText(f"{cena:.2f}")

    def get_payload(self) -> Dict[str, Any]:
        typ_txt = self.cb_typ.currentText()
        if typ_txt == "Księgowość":
            typ = "ksiegowosc"
        elif typ_txt == "Kadry":
            typ = "kadry"
        else:
            typ = "extra"

        usluga_id = self.cb_usluga.currentData() if typ == "extra" else None
        return {
            "typ_czynnosci": typ,
            "usluga_id": usluga_id,
            "fakturowac": self.chk_fakturowac.isChecked(),
        }


class InlineExtraServiceWindow(QDialog):
    def __init__(self, firma_id: int, miesiac: str, parent=None):
        super().__init__(parent)
        self.firma_id = firma_id
        self.miesiac = miesiac

        self.setWindowTitle("Usługi dodatkowe")
        self.resize(500, 250)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.cb_usluga = QComboBox()
        self._load_extra_services()

        self.cb_jednostka = QComboBox()
        self.cb_jednostka.addItems(["godz.", "szt.", "usługa"])

        self.input_ilosc = QLineEdit("1")
        self.input_ilosc.setValidator(QIntValidator(0, 9999, self))

        self.chk_fakturowac = QCheckBox("Fakturować?")

        form.addRow("Usługa:", self.cb_usluga)
        form.addRow("Jednostka:", self.cb_jednostka)
        form.addRow("Ilość:", self.input_ilosc)
        form.addRow(self.chk_fakturowac)

        layout.addLayout(form)

        btn_layout = QHBoxLayout()
        self.btn_add = QPushButton("Dodaj")
        self.btn_cancel = QPushButton("Anuluj")
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_add)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

        self.btn_add.clicked.connect(self._on_add)
        self.btn_cancel.clicked.connect(self.reject)

    def _load_extra_services(self):
        logic = GlobalSettingsLogic()
        rows = logic.get_extra_services()
        self.cb_usluga.clear()
        for r in rows:
            self.cb_usluga.addItem(r.get("nazwa", ""), r.get("id"))

    def _on_add(self):
        usluga_id = self.cb_usluga.currentData()
        if not usluga_id:
            QMessageBox.warning(self, "Błąd", "Wybierz usługę.")
            return
        try:
            ilosc = float(self.input_ilosc.text().replace(",", ".") or "0")
        except Exception:
            ilosc = 0.0
        if ilosc <= 0:
            QMessageBox.warning(self, "Błąd", "Ilość musi być większa od zera.")
            return

        if self.chk_fakturowac.isChecked():
            add_invoice_item_for_service(self.firma_id, usluga_id, ilosc, self.miesiac, None)
            QMessageBox.information(self, "OK", "Dodano pozycję do faktury.")
        self.accept()


class MonthlyViewPage(QWidget):
    def __init__(self, pracownik_id: int, parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self.logic = MonthlyViewLogic(pracownik_id)

        self._rows: List[Dict[str, Any]] = []

        self.model_ks = MonthlyViewModel([], "ksiegowosc", self)
        self.model_kadry = MonthlyViewModel([], "kadry", self)

        self._build_ui()
        self._load_initial_data()
        self._setup_timer()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)

        filters_layout = QHBoxLayout()

        self.month_combo = QComboBox()
        self._populate_months()
        filters_layout.addWidget(QLabel("Miesiąc:"))
        filters_layout.addWidget(self.month_combo)

        self.employee_combo = QComboBox()
        filters_layout.addWidget(QLabel("Pracownik:"))
        filters_layout.addWidget(self.employee_combo)

        self.status_combo = QComboBox()
        self.status_combo.addItems(COMPANY_STATUS_FILTER)
        filters_layout.addWidget(QLabel("Status:"))
        filters_layout.addWidget(self.status_combo)

        filters_layout.addStretch()

        self.btn_refresh = QPushButton("Odśwież")
        filters_layout.addWidget(self.btn_refresh)

        main_layout.addLayout(filters_layout)

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        self.table_ks = QTableView()
        self.table_ks.setModel(self.model_ks)
        self.table_ks.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_ks.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.table_ks.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.tabs.addTab(self.table_ks, "Księgowość")

        self.table_kadry = QTableView()
        self.table_kadry.setModel(self.model_kadry)
        self.table_kadry.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_kadry.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self.table_kadry.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.tabs.addTab(self.table_kadry, "Kadry")

        # delegaty – przyciski i notatki (ScrollableNotesDelegate dla kolumny notatek)
        self.table_ks.setItemDelegateForColumn(4, ButtonDelegate(self.table_ks, "▶", "stoper_ks"))
        self.table_ks.setItemDelegateForColumn(6, ButtonDelegate(self.table_ks, "➕", "extra_ks"))
        self.table_ks.setItemDelegateForColumn(7, ButtonDelegate(self.table_ks, "✏️", "edit_ks"))
        self.table_ks.setItemDelegateForColumn(8, ButtonDelegate(self.table_ks, "⋯", "more_ks"))
        self.table_ks.setItemDelegateForColumn(5, ScrollableNotesDelegate(self.table_ks))

        self.table_kadry.setItemDelegateForColumn(6, ButtonDelegate(self.table_kadry, "▶", "stoper_kadry"))
        self.table_kadry.setItemDelegateForColumn(8, ButtonDelegate(self.table_kadry, "➕", "extra_kadry"))
        self.table_kadry.setItemDelegateForColumn(9, ButtonDelegate(self.table_kadry, "✏️", "edit_kadry"))
        self.table_kadry.setItemDelegateForColumn(7, ScrollableNotesDelegate(self.table_kadry))

        self._setup_column_widths()

        self.month_combo.currentTextChanged.connect(self._on_filters_changed)
        self.employee_combo.currentIndexChanged.connect(self._on_filters_changed)
        self.status_combo.currentIndexChanged.connect(self._on_filters_changed)
        self.btn_refresh.clicked.connect(self._reload_rows)

    def _setup_column_widths(self):
        header_ks = self.table_ks.horizontalHeader()
        header_ks.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header_ks.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header_ks.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header_ks.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header_ks.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header_ks.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        header_ks.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        header_ks.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        header_ks.setSectionResizeMode(8, QHeaderView.ResizeMode.ResizeToContents)

        header_kadry = self.table_kadry.horizontalHeader()
        header_kadry.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header_kadry.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header_kadry.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header_kadry.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header_kadry.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header_kadry.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header_kadry.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        header_kadry.setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        header_kadry.setSectionResizeMode(8, QHeaderView.ResizeMode.ResizeToContents)
        header_kadry.setSectionResizeMode(9, QHeaderView.ResizeMode.ResizeToContents)

    def _populate_months(self):
        now = datetime.datetime.now()
        months = []
        for i in range(-6, 7):
            m = now + datetime.timedelta(days=30 * i)
            months.append(m.strftime("%Y-%m"))
        self.month_combo.addItems(months)
        self.month_combo.setCurrentText(self.logic.aktualny_miesiac)

    def _load_initial_data(self):
        self._load_employee_filter()
        self._reload_rows()

    def _setup_timer(self):
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_running_timer)
        self._timer.start(1000)

    def _load_employee_filter(self):
        self.employee_combo.clear()
        employees = self.logic.get_employee_filter_list()
        for emp_id, label in employees:
            self.employee_combo.addItem(label, emp_id)

    def _current_month(self) -> str:
        return self.month_combo.currentText()

    def _current_employee_id(self) -> Optional[int]:
        idx = self.employee_combo.currentIndex()
        if idx < 0:
            return None
        val = self.employee_combo.itemData(idx)
        return int(val) if val else None

    def _current_status_filter(self) -> str:
        return self.status_combo.currentText() or "Wszystkie"

    def _on_filters_changed(self):
        self.logic.set_month(self._current_month())
        self._reload_rows()

    def _reload_rows(self):
        month = self._current_month()
        emp_id = self._current_employee_id()
        status = self._current_status_filter()

        # full reload only on explicit refresh or filter change
        self._rows = self.logic.get_monthly_rows(month, emp_id, status)
        self._render_tables()

    def _render_tables(self):
        rows_prepared: List[Dict[str, Any]] = []
        for r in self._rows:
            rr = dict(r)
            sekundy_ks = rr.get("sekundy_ksiegowosc", 0)
            sekundy_kadry = rr.get("sekundy_kadry", 0)
            rr["czas_ks_str"] = self.logic.format_seconds(sekundy_ks)
            rr["czas_kadry_str"] = self.logic.format_seconds(sekundy_kadry)
            rr.setdefault("uop", 0)
            rr.setdefault("uz", 0)
            rr.setdefault("uod", 0)
            rows_prepared.append(rr)

        # set_rows resets model once per reload (explicit)
        self.model_ks.set_rows(rows_prepared)
        self.model_kadry.set_rows(rows_prepared)

    def _update_running_timer(self):
        now = datetime.datetime.now()

        # update only the single cell for active księgowość timer
        if self.logic.aktywna_firma_stoper_ks and self.logic.czas_start_ks:
            fid = self.logic.aktywna_firma_stoper_ks
            delta = int((now - self.logic.czas_start_ks).total_seconds())
            for row_idx in range(self.model_ks.rowCount()):
                row = self.model_ks.get_row(row_idx)
                if row and row.get("id") == fid:
                    row["czas_ks_str"] = TimerUtils.format_seconds(delta)
                    idx = self.model_ks.index(row_idx, 3)
                    self.model_ks.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DisplayRole])
                    break

        # update only the single cell for active kadry timer
        if self.logic.aktywna_firma_stoper_kadry and self.logic.czas_start_kadry:
            fid = self.logic.aktywna_firma_stoper_kadry
            delta = int((now - self.logic.czas_start_kadry).total_seconds())
            for row_idx in range(self.model_kadry.rowCount()):
                row = self.model_kadry.get_row(row_idx)
                if row and row.get("id") == fid:
                    row["czas_kadry_str"] = TimerUtils.format_seconds(delta)
                    idx = self.model_kadry.index(row_idx, 5)
                    self.model_kadry.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DisplayRole])
                    break

    def handle_button_click(self, index: QModelIndex, role: str):
        print("DEBUG: handle_button_click", role, index.row())
        row = index.model().get_row(index.row())
        if not row:
            return
        firma_id = row["id"]

        if role == "stoper_ks":
            self._on_toggle_stoper_ks(firma_id)
        elif role == "stoper_kadry":
            self._on_toggle_stoper_kadry(firma_id)
        elif role in ("extra_ks", "extra_kadry"):
            self._on_extra_service(firma_id)
        elif role in ("edit_ks", "edit_kadry"):
            self._on_edit_company(firma_id)
        elif role == "more_ks":
            self._on_more_menu(firma_id)

    def handle_notes_dblclick(self, index: QModelIndex):
        row = index.model().get_row(index.row())
        if not row:
            return
        firma_id = row["id"]
        self._open_notes(firma_id)

    def _open_notes(self, firma_id: int):
        dlg = NotesWindow(firma_id, self.pracownik_id, parent=self)
        dlg.exec()

    def _on_etap_changed(self, firma_id: int):
        self.logic.schedule_status_autosave(firma_id)

    def _on_docs_changed(self, firma_id: int):
        self.logic.schedule_status_autosave(firma_id)

    def _on_kadry_changed(self, firma_id: int):
        self.logic.schedule_status_autosave(firma_id)

    def _on_toggle_stoper_ks(self, firma_id: int):
        if self.logic.aktywna_firma_stoper_ks == firma_id:
            # stopping — persist and reload to reflect saved seconds
            self.logic.toggle_stoper(firma_id, "ksiegowosc", None)
            self._reload_rows()
            return

        dlg = ActivitySelectionDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            payload = dlg.get_payload()
            # starting — do not reload; timer will update the single cell
            self.logic.toggle_stoper(firma_id, "ksiegowosc", payload)

    def _on_toggle_stoper_kadry(self, firma_id: int):
        if self.logic.aktywna_firma_stoper_kadry == firma_id:
            self.logic.toggle_stoper(firma_id, "kadry", None)
            self._reload_rows()
            return

        dlg = ActivitySelectionDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            payload = dlg.get_payload()
            self.logic.toggle_stoper(firma_id, "kadry", payload)

    def _on_extra_service(self, firma_id: int):
        dlg = InlineExtraServiceWindow(
            firma_id,
            self._current_month(),
            parent=self,
        )
        dlg.exec()

    def _on_edit_company(self, firma_id: int):
        self.logic.open_company_edit_window(firma_id)
        self._reload_rows()

    def _on_more_menu(self, firma_id: int):
        menu = QMenu(self)

        act_suspend = QAction("Oznacz jako zawieszoną", self)
        act_archive = QAction("Przenieś do archiwum", self)
        act_activity = QAction("Pokaż aktywności", self)

        menu.addAction(act_suspend)
        menu.addAction(act_archive)
        menu.addSeparator()
        menu.addAction(act_activity)

        act_suspend.triggered.connect(lambda: self._on_mark_suspended(firma_id))
        act_archive.triggered.connect(lambda: self._on_move_archive(firma_id))
        act_activity.triggered.connect(lambda: self._on_show_activity(firma_id))

        menu.exec(QCursor.pos())

    def _on_mark_suspended(self, firma_id: int):
        self.logic.mark_company_suspended_manual(firma_id)
        QMessageBox.information(self, "Zawieszenie", "Firma oznaczona jako zawieszona.")
        self._reload_rows()

    def _on_move_archive(self, firma_id: int):
        self.logic.move_company_to_archive(firma_id)
        QMessageBox.information(self, "Archiwum", "Firma przeniesiona do archiwum.")
        self._reload_rows()

    def _on_show_activity(self, firma_id: int):
        dlg = ActivityDialog(
            firma_id,
            self._current_month(),
            parent=self,
        )
        dlg.exec()
