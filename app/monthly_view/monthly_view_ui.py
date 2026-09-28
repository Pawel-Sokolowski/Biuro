from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QIntValidator, QIcon
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QLineEdit,
    QMenu,
    QDialog,
    QTextEdit,
    QFormLayout,
    QMessageBox,
)

from app.monthly_view.monthly_view_logic import MonthlyViewLogic

COMPANY_STAGE_OPTIONS = ["Brak", "W trakcie", "Zakończone"]
COMPANY_STATUS_FILTER = ["Aktywne", "Zawieszone", "Archiwalne", "Wszystkie"]


class HoverScrollTextEdit(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMinimumHeight(40)
        self.setMaximumHeight(80)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setStyleSheet("QTextEdit { background: #f9f9f9; }")

    def set_text(self, text: str):
        self.setPlainText(text or "")


class InlineNotesWindow(QDialog):
    def __init__(self, firma_id: int, parent=None, pracownik_id: Optional[int] = None):
        super().__init__(parent)
        self.firma_id = firma_id
        self.pracownik_id = pracownik_id

        self.setWindowTitle("Notatki firmy")
        self.resize(800, 600)

        layout = QVBoxLayout(self)
        self.text = QTextEdit()
        layout.addWidget(self.text)

        btn_layout = QHBoxLayout()
        self.btn_save = QPushButton("Zapisz")
        self.btn_cancel = QPushButton("Zamknij")
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_save)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

        self.btn_save.clicked.connect(self._on_save)
        self.btn_cancel.clicked.connect(self.reject)

        self._load_notes()

    def _load_notes(self):
        # podłącz swoją logikę pobierania notatek
        pass

    def _on_save(self):
        # podłącz swoją logikę zapisu notatek
        self.accept()


class InlineExtraServiceWindow(QDialog):
    def __init__(self, firma_id: int, miesiac: str, parent=None):
        super().__init__(parent)
        self.firma_id = firma_id
        self.miesiac = miesiac

        self.setWindowTitle("Usługi dodatkowe")
        self.resize(700, 450)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.input_nazwa = QLineEdit()
        self.input_godziny = QLineEdit()
        form.addRow("Nazwa usługi:", self.input_nazwa)
        form.addRow("Godziny:", self.input_godziny)

        layout.addLayout(form)

        btn_layout = QHBoxLayout()
        self.btn_add = QPushButton("Dodaj")
        self.btn_cancel = QPushButton("Zamknij")
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_add)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

        self.btn_add.clicked.connect(self._on_add)
        self.btn_cancel.clicked.connect(self.reject)

    def _on_add(self):
        # podłącz logikę dodawania usługi
        self.accept()


class ActivityDialog(QDialog):
    def __init__(self, firma_id: int, miesiac: str, parent=None):
        super().__init__(parent)
        self.firma_id = firma_id
        self.miesiac = miesiac

        self.setWindowTitle("Aktywności firmy")
        self.resize(800, 500)

        layout = QVBoxLayout(self)
        self.text = QTextEdit()
        self.text.setReadOnly(True)
        layout.addWidget(self.text)

        btn_layout = QHBoxLayout()
        self.btn_close = QPushButton("Zamknij")
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_close)
        layout.addLayout(btn_layout)

        self.btn_close.clicked.connect(self.reject)

        self._load_activities()

    def _load_activities(self):
        # podłącz logikę pobierania listy aktywności
        self.text.setPlainText("Tu będzie lista aktywności dla firmy.")


class MonthlyViewPage(QWidget):
    def __init__(self, pracownik_id: int, parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self.logic = MonthlyViewLogic(pracownik_id)

        self._rows: List[Dict[str, Any]] = []

        self._build_ui()
        self._load_initial_data()

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

        main_layout.addWidget(QLabel("Księgowość"))
        self.table_ks = QTableWidget()
        self.table_ks.setColumnCount(9)
        self.table_ks.setHorizontalHeaderLabels(
            [
                "Firma",
                "Etap",
                "Ilość dok.",
                "Czas",
                "Stoper",
                "Notatki",
                "Usługa",
                "Edytuj",
                "⋯",
            ]
        )
        self.table_ks.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_ks.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_ks.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        main_layout.addWidget(self.table_ks)

        main_layout.addWidget(QLabel("Kadry"))
        self.table_kadry = QTableWidget()
        self.table_kadry.setColumnCount(10)
        self.table_kadry.setHorizontalHeaderLabels(
            [
                "Firma",
                "Etap",
                "UoP",
                "UZ",
                "UoD",
                "Czas",
                "Stoper",
                "Notatki",
                "Usługa",
                "Edytuj",
            ]
        )
        self.table_kadry.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_kadry.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_kadry.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        main_layout.addWidget(self.table_kadry)

        self.month_combo.currentTextChanged.connect(self._on_filters_changed)
        self.employee_combo.currentIndexChanged.connect(self._on_filters_changed)
        self.status_combo.currentIndexChanged.connect(self._on_filters_changed)
        self.btn_refresh.clicked.connect(self._reload_rows)

    def _populate_months(self):
        now = datetime.datetime.now()
        months = []
        for i in range(-6, 7):
            m = now + datetime.timedelta(days=30 * i)
            label = m.strftime("%Y-%m")
            months.append(label)
        self.month_combo.addItems(months)
        self.month_combo.setCurrentText(self.logic.aktualny_miesiac)

    def _load_initial_data(self):
        self._load_employee_filter()
        self._reload_rows()

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

        self._rows = self.logic.get_monthly_rows(month, emp_id, status)
        self._render_tables()

    def _render_tables(self):
        self._render_table_ks()
        self._render_table_kadry()

    def _row_background_color(self, status: str, etap: str) -> str:
        if status == "Zawieszone":
            return "#dddddd"
        if status == "Archiwalne":
            return "rgba(200, 200, 200, 120)"
        if etap == "W trakcie":
            return "#fff8c4"
        if etap == "Zakończone":
            return "#d9ffd9"
        return "#ffffff"

    def _apply_row_style(self, table: QTableWidget, row_idx: int, status: str, etap: str):
        color = self._row_background_color(status, etap)
        for col in range(table.columnCount()):
            item = table.item(row_idx, col)
            if item:
                item.setBackground(Qt.GlobalColor.transparent)
                item.setBackground(Qt.GlobalColor.white)
        table.setRowHeight(row_idx, 32)
        table.setStyleSheet(
            "QTableWidget::item:selected { background-color: #cde4ff; }"
        )

    def _render_table_ks(self):
        self.table_ks.setRowCount(0)
        self.logic.status_widgets.clear()

        for row_idx, r in enumerate(self._rows):
            self.table_ks.insertRow(row_idx)

            firma_id = r["id"]
            nazwa = r.get("nazwa", "")
            etap = r.get("etap", "Brak")
            ilosc_dok = r.get("ilosc_dok", 0)
            sekundy_ks = r.get("sekundy_ksiegowosc", 0)
            note_snippet = r.get("note_snippet", "")
            status = r.get("status", "Aktywne")

            item_firma = QTableWidgetItem(nazwa)
            item_firma.setData(Qt.ItemDataRole.UserRole, firma_id)
            item_firma.setToolTip(f"Firma: {nazwa}\nStatus: {status}\nEtap: {etap}")
            self.table_ks.setItem(row_idx, 0, item_firma)

            etap_combo = QComboBox()
            etap_combo.addItems(COMPANY_STAGE_OPTIONS)
            if etap in COMPANY_STAGE_OPTIONS:
                etap_combo.setCurrentText(etap)
            etap_combo.setToolTip("Etap pracy nad firmą w danym miesiącu.")
            etap_combo.currentTextChanged.connect(
                lambda _t, fid=firma_id: self._on_etap_changed(fid)
            )
            self.table_ks.setCellWidget(row_idx, 1, etap_combo)

            docs_edit = QLineEdit(str(ilosc_dok))
            docs_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
            docs_edit.setValidator(QIntValidator(0, 9999, docs_edit))
            docs_edit.setToolTip("Ilość dokumentów księgowych w miesiącu.")
            docs_edit.textChanged.connect(
                lambda _t, fid=firma_id: self._on_docs_changed(fid)
            )
            self.table_ks.setCellWidget(row_idx, 2, docs_edit)

            czas_str = self.logic.format_seconds(sekundy_ks)
            item_czas = QTableWidgetItem(czas_str)
            item_czas.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_czas.setToolTip(f"Czas pracy księgowości: {czas_str}")
            self.table_ks.setItem(row_idx, 3, item_czas)

            btn_stoper = QPushButton("▶")
            btn_stoper.setMaximumWidth(40)
            btn_stoper.setToolTip("Stoper księgowości (start/stop).")
            btn_stoper.clicked.connect(
                lambda _checked, fid=firma_id: self._on_toggle_stoper_ks(fid)
            )
            self.table_ks.setCellWidget(row_idx, 4, btn_stoper)

            note_widget = HoverScrollTextEdit()
            note_widget.set_text(note_snippet)
            note_widget.setToolTip("Podwójne kliknięcie: pełne okno notatek.")
            note_widget.mouseDoubleClickEvent = self._make_notes_double_click_handler(
                firma_id
            )
            self.table_ks.setCellWidget(row_idx, 5, note_widget)

            btn_extra = QPushButton("➕")
            btn_extra.setMaximumWidth(40)
            btn_extra.setToolTip("Dodaj usługę dodatkową.")
            btn_extra.clicked.connect(
                lambda _checked, fid=firma_id: self._on_extra_service(fid)
            )
            self.table_ks.setCellWidget(row_idx, 6, btn_extra)

            btn_edit = QPushButton("✏️")
            btn_edit.setToolTip("Edytuj dane firmy.")
            btn_edit.clicked.connect(
                lambda _checked, fid=firma_id: self._on_edit_company(fid)
            )
            self.table_ks.setCellWidget(row_idx, 7, btn_edit)

            btn_more = QPushButton("⋯")
            btn_more.setMaximumWidth(40)
            btn_more.setToolTip("Więcej opcji dla firmy.")
            btn_more.clicked.connect(
                lambda _checked, fid=firma_id, btn=btn_more: self._on_more_menu(fid, btn)
            )
            self.table_ks.setCellWidget(row_idx, 8, btn_more)

            self.logic.status_widgets[firma_id] = (docs_edit, etap_combo)

            self._apply_row_style(self.table_ks, row_idx, status, etap)

    def _render_table_kadry(self):
        self.table_kadry.setRowCount(0)

        for row_idx, r in enumerate(self._rows):
            self.table_kadry.insertRow(row_idx)

            firma_id = r["id"]
            nazwa = r.get("nazwa", "")
            etap = r.get("etap", "Brak")
            sekundy_kadry = r.get("sekundy_kadry", 0)
            note_snippet = r.get("note_snippet", "")
            status = r.get("status", "Aktywne")

            item_firma = QTableWidgetItem(nazwa)
            item_firma.setData(Qt.ItemDataRole.UserRole, firma_id)
            item_firma.setToolTip(f"Firma: {nazwa}\nStatus: {status}\nEtap: {etap}")
            self.table_kadry.setItem(row_idx, 0, item_firma)

            etap_combo = QComboBox()
            etap_combo.addItems(COMPANY_STAGE_OPTIONS)
            if etap in COMPANY_STAGE_OPTIONS:
                etap_combo.setCurrentText(etap)
            etap_combo.setToolTip("Etap pracy kadr w danym miesiącu.")
            etap_combo.currentTextChanged.connect(
                lambda _t, fid=firma_id: self._on_etap_changed(fid)
            )
            self.table_kadry.setCellWidget(row_idx, 1, etap_combo)

            uop_edit = QLineEdit("0")
            uz_edit = QLineEdit("0")
            uod_edit = QLineEdit("0")
            for label, w, col in [
                ("UoP", uop_edit, 2),
                ("UZ", uz_edit, 3),
                ("UoD", uod_edit, 4),
            ]:
                w.setAlignment(Qt.AlignmentFlag.AlignCenter)
                w.setValidator(QIntValidator(0, 9999, w))
                w.setToolTip(f"Liczba umów {label} w miesiącu.")
                w.textChanged.connect(
                    lambda _t, fid=firma_id: self._on_kadry_changed(fid)
                )
                self.table_kadry.setCellWidget(row_idx, col, w)

            czas_str = self.logic.format_seconds(sekundy_kadry)
            item_czas = QTableWidgetItem(czas_str)
            item_czas.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item_czas.setToolTip(f"Czas pracy kadr: {czas_str}")
            self.table_kadry.setItem(row_idx, 5, item_czas)

            btn_stoper = QPushButton("▶")
            btn_stoper.setMaximumWidth(40)
            btn_stoper.setToolTip("Stoper kadr (start/stop).")
            btn_stoper.clicked.connect(
                lambda _checked, fid=firma_id: self._on_toggle_stoper_kadry(fid)
            )
            self.table_kadry.setCellWidget(row_idx, 6, btn_stoper)

            note_widget = HoverScrollTextEdit()
            note_widget.set_text(note_snippet)
            note_widget.setToolTip("Podwójne kliknięcie: pełne okno notatek.")
            note_widget.mouseDoubleClickEvent = self._make_notes_double_click_handler(
                firma_id
            )
            self.table_kadry.setCellWidget(row_idx, 7, note_widget)

            btn_extra = QPushButton("➕")
            btn_extra.setMaximumWidth(40)
            btn_extra.setToolTip("Dodaj usługę dodatkową.")
            btn_extra.clicked.connect(
                lambda _checked, fid=firma_id: self._on_extra_service(fid)
            )
            self.table_kadry.setCellWidget(row_idx, 8, btn_extra)

            btn_edit = QPushButton("✏️")
            btn_edit.setToolTip("Edytuj dane firmy.")
            btn_edit.clicked.connect(
                lambda _checked, fid=firma_id: self._on_edit_company(fid)
            )
            self.table_kadry.setCellWidget(row_idx, 9, btn_edit)

            self._apply_row_style(self.table_kadry, row_idx, status, etap)

    def _make_notes_double_click_handler(self, firma_id: int):
        def handler(event):
            dlg = InlineNotesWindow(firma_id, parent=self, pracownik_id=self.pracownik_id)
            dlg.exec()

        return handler

    def _on_etap_changed(self, firma_id: int):
        self.logic.schedule_status_autosave(firma_id)

    def _on_docs_changed(self, firma_id: int):
        self.logic.schedule_status_autosave(firma_id)

    def _on_kadry_changed(self, firma_id: int):
        # tu możesz podłączyć autosave dla UoP/UZ/UoD (np. osobna tabela w logic)
        self.logic.schedule_kadry_autosave(firma_id)

    def _on_toggle_stoper_ks(self, firma_id: int):
        self.logic.toggle_stoper(firma_id, "ksiegowosc")
        self._reload_rows()

    def _on_toggle_stoper_kadry(self, firma_id: int):
        self.logic.toggle_stoper(firma_id, "kadry")
        self._reload_rows()

    def _on_extra_service(self, firma_id: int):
        dlg = InlineExtraServiceWindow(firma_id, self._current_month(), parent=self)
        dlg.exec()

    def _on_edit_company(self, firma_id: int):
        self.logic.open_company_edit_window(firma_id)
        self._reload_rows()

    def _on_more_menu(self, firma_id: int, button: QPushButton):
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

        pos = button.mapToGlobal(button.rect().bottomLeft())
        menu.exec(pos)

    def _on_mark_suspended(self, firma_id: int):
        self.logic.mark_company_suspended_manual(firma_id)
        QMessageBox.information(self, "Zawieszenie", "Firma oznaczona jako zawieszona.")
        self._reload_rows()

    def _on_move_archive(self, firma_id: int):
        self.logic.move_company_to_archive(firma_id)
        QMessageBox.information(self, "Archiwum", "Firma przeniesiona do archiwum.")
        self._reload_rows()

    def _on_show_activity(self, firma_id: int):
        dlg = ActivityDialog(firma_id, self._current_month(), parent=self)
        dlg.exec()
