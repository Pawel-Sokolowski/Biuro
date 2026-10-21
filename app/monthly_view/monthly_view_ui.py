from __future__ import annotations

import os
import datetime
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, QModelIndex, QTimer, QEvent, QDate
from PyQt6.QtGui import (
    QAction,
    QIntValidator,
    QCursor,
    QIcon,
    QColor,
    QPainter,
    QFontMetrics,
)
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
    QStyledItemDelegate, QStyle, QTableWidget, QTableWidgetItem,
    QCalendarWidget, QDialogButtonBox, QStyleOptionViewItem,
)

from app.monthly_view.monthly_view_logic import MonthlyViewLogic
from app.admin.global_settings_logic import GlobalSettingsLogic
from app.db.dao_invoices import add_invoice_item_for_service
from app.db.dao_services import add_company_extra_service
from app.db.dao_tasks import list_tasks_for_timer
from app.utils.timer import TimerUtils
from app.monthly_view.monthly_view_model import MonthlyViewModel
from app.monthly_view.notes_window import NotesWindow

COMPANY_STATUS_FILTER = ["Aktywne", "Zawieszone", "Archiwalne", "Wszystkie"]
ETAP_CHOICES = ["Brak", "Zatwierdzone", "Zaksięgowane", "Wysłane wyniki"]


class EtapComboDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.addItems(ETAP_CHOICES)
        return combo

    def setEditorData(self, editor, index):
        value = index.data(Qt.ItemDataRole.EditRole) or "Brak"
        pos = editor.findText(value)
        editor.setCurrentIndex(pos if pos >= 0 else 0)

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentText(), Qt.ItemDataRole.EditRole)


class DeliveredDateDelegate(QStyledItemDelegate):
    """Checkbox z datą dostarczenia; kliknięcie w datę pozwala ją zmienić."""

    def editorEvent(self, event, model, option, index):
        if (
            event.type() == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
            and index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
        ):
            opt = QStyleOptionViewItem(option)
            self.initStyleOption(opt, index)
            style = opt.widget.style() if opt.widget else None
            check_rect = (
                style.subElementRect(QStyle.SubElement.SE_ItemViewItemCheckIndicator, opt, opt.widget)
                if style else None
            )
            if check_rect is not None and not check_rect.contains(event.position().toPoint()):
                self.choose_date(model, index, opt.widget)
                return True
        return super().editorEvent(event, model, option, index)

    @staticmethod
    def choose_date(model, index, parent=None) -> None:
        row = model.get_row(index.row()) or {}
        try:
            current = datetime.date.fromisoformat(str(row.get("data_dostarczenia") or "")[:10])
        except ValueError:
            current = datetime.date.today()
        dlg = QDialog(parent)
        dlg.setWindowTitle("Data dostarczenia dokumentów")
        lay = QVBoxLayout(dlg)
        cal = QCalendarWidget(dlg)
        cal.setSelectedDate(QDate(current.year, current.month, current.day))
        lay.addWidget(cal)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(dlg.accept)
        box.rejected.connect(dlg.reject)
        lay.addWidget(box)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            model.setData(index, cal.selectedDate().toString("yyyy-MM-dd"), model.DATE_ROLE)


def icon_path(name: str) -> str:
    return os.path.join(os.path.dirname(__file__), "..", "icons", name)

class IconButtonDelegate(QStyledItemDelegate):
    def __init__(self, parent, icon_name: str, role: str):
        super().__init__(parent)
        self.icon = QIcon(icon_path(icon_name))
        self.role = role

    def paint(self, painter, option, index):
        painter.save()

        rect = option.rect.adjusted(4, 4, -4, -4)

        bg = QColor("#e6e6e6")
        if option.state & QStyle.StateFlag.State_MouseOver:
            bg = QColor("#d0d0d0")

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(bg)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(rect, 4, 4)

        self.icon.paint(
            painter,
            rect,
            Qt.AlignmentFlag.AlignCenter,
        )

        painter.restore()

    def editorEvent(self, event, model, option, index):
        return False

class ScrollableNotesDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._offsets: Dict[tuple, int] = {}

    def paint(self, painter, option, index):
        painter.save()
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        rect = option.rect.adjusted(4, 4, -4, -4)
        key = self._key(index)
        line_height = painter.fontMetrics().lineSpacing()
        content_height = painter.fontMetrics().boundingRect(
            rect.width(),
            0,
            rect.width(),
            100000,
            Qt.TextFlag.TextWordWrap,
            text,
        ).height()
        max_offset = max(0, content_height - rect.height() + line_height)
        offset = min(self._offsets.get(key, 0), max_offset)
        self._offsets[key] = offset

        painter.setClipRect(rect)
        painter.translate(0, -offset)
        painter.drawText(
            rect.x(),
            rect.y(),
            rect.width(),
            content_height,
            Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignLeft,
            text,
        )
        painter.restore()

    def _key(self, index: QModelIndex) -> tuple:
        row = index.model().data(index, Qt.ItemDataRole.UserRole) or {}
        return id(index.model()), row.get("id", index.row())

    def scroll(self, index: QModelIndex, delta: int, width: int, height: int) -> bool:
        text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        rect_width = max(1, width - 8)
        rect_height = max(1, height - 8)
        content_height = QFontMetrics(index.model().parent().font()).boundingRect(
            rect_width,
            0,
            rect_width,
            100000,
            Qt.TextFlag.TextWordWrap,
            text,
        ).height()
        line_height = QFontMetrics(index.model().parent().font()).lineSpacing()
        max_offset = max(0, content_height - rect_height)
        if max_offset > 0:
            max_offset += line_height
        if max_offset == 0:
            return False

        key = self._key(index)
        old_offset = self._offsets.get(key, 0)
        new_offset = max(0, min(max_offset, old_offset - delta))
        if new_offset == old_offset:
            return False
        self._offsets[key] = new_offset
        return True

    def editorEvent(self, event, model, option, index):
        return False


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


class ActivityDialog(QDialog):
    """Lista zarejestrowanych czasów pracy firmy w danym miesiącu."""

    def __init__(self, firma_id: int, miesiac: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Aktywności — {miesiac}")
        self.resize(760, 420)
        layout = QVBoxLayout(self)
        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(["Pracownik", "Typ", "Początek", "Koniec", "Czas"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)
        for row in self._load(firma_id, miesiac):
            r = table.rowCount()
            table.insertRow(r)
            secs = int(row["sekundy"] or 0)
            values = [
                row["pracownik"] or "", row["typ"] or "",
                row["rozpoczecie"] or "", row["zakonczenie"] or "",
                f"{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}",
            ]
            for col, value in enumerate(values):
                table.setItem(r, col, QTableWidgetItem(value))
        btn = QPushButton("Zamknij")
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)

    @staticmethod
    def _load(firma_id: int, miesiac: str):
        from app.db.dao_conn import get_conn
        conn = get_conn()
        try:
            cur = conn.execute(
                "SELECT c.typ, c.rozpoczecie, c.zakonczenie, c.sekundy, "
                "p.imie || ' ' || p.nazwisko AS pracownik "
                "FROM czasomierze c LEFT JOIN pracownicy p ON p.id = c.pracownik_id "
                "WHERE c.firma_id = %s AND c.miesiac = %s ORDER BY c.rozpoczecie DESC",
                (firma_id, miesiac),
            )
            return [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
        finally:
            conn.close()


class ActivitySelectionDialog(QDialog):
    def __init__(
        self,
        mode: str,
        firma_id: int,
        pracownik_id: int,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Wybór czynności")
        self.resize(500, 300)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.cb_typ = QComboBox()
        self.cb_typ.addItems(
            ["Księgowość", "Usługi dodatkowe"]
            if mode == "ksiegowosc"
            else ["Kadry", "Usługi dodatkowe"]
        )

        self.cb_usluga = QComboBox()
        self._load_extra_services()

        self.cb_task = QComboBox()
        self.cb_task.addItem("Brak zadania", None)
        for task in list_tasks_for_timer(pracownik_id, firma_id):
            self.cb_task.addItem(task["tytul"], task["id"])

        self.input_cena = QLineEdit()
        self.input_cena.setPlaceholderText("Cena netto")
        self.input_cena.setEnabled(True)

        self.chk_fakturowac = QCheckBox("Fakturować?")

        form.addRow("Typ pracy:", self.cb_typ)
        form.addRow("Usługa dodatkowa:", self.cb_usluga)
        form.addRow("Zadanie:", self.cb_task)
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
        self.cb_typ.currentIndexChanged.connect(self._update_visibility)
        self._update_visibility()


    def _load_extra_services(self):
        logic = GlobalSettingsLogic()
        rows = logic.get_extra_services()
        self.cb_usluga.clear()
        for r in rows:
            self.cb_usluga.addItem(r["nazwa"], r["id"])

    def _update_visibility(self):
        typ = self.cb_typ.currentText()
        is_extra = typ == "Usługi dodatkowe"

        self.cb_usluga.setVisible(is_extra)
        self.input_cena.setVisible(is_extra)
        self.chk_fakturowac.setVisible(is_extra)

        if not is_extra:
            self.input_cena.clear()



    def _on_service_changed(self, idx: int):
        logic = GlobalSettingsLogic()
        rows = logic.get_extra_services()
        if idx < 0 or idx >= len(rows):
            self.input_cena.setText("")
            return
        cena = rows[idx]["cena_netto"]
        try:
            cena = float(cena)
        except:
            cena = 0.0
        self.input_cena.setText(str(cena))

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
            "task_id": self.cb_task.currentData(),
            "task_title": self.cb_task.currentText() if self.cb_task.currentData() else None,
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

        self.input_cena = QLineEdit()
        self.input_cena.setPlaceholderText("Cena netto")
        self.input_cena.setEnabled(True)

        self._update_price()

        self.chk_fakturowac = QCheckBox("Fakturować?")

        form.addRow("Usługa:", self.cb_usluga)
        form.addRow("Jednostka:", self.cb_jednostka)
        form.addRow("Ilość:", self.input_ilosc)
        form.addRow("Cena netto:", self.input_cena)
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

        self.cb_usluga.currentIndexChanged.connect(self._update_price)

    def _update_price(self):
        logic = GlobalSettingsLogic()
        rows = logic.get_extra_services()
        idx = self.cb_usluga.currentIndex()
        if idx < 0 or idx >= len(rows):
            self.input_cena.setText("")
            return
        cena = rows[idx]["cena_netto"]
        try:
            cena = float(cena)
        except:
            cena = 0.0
        self.input_cena.setText(str(cena))
    def _load_extra_services(self):
        logic = GlobalSettingsLogic()
        rows = logic.get_extra_services()
        self.cb_usluga.clear()
        for r in rows:
            self.cb_usluga.addItem(r["nazwa"], r["id"])

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

        try:
            cena = float(self.input_cena.text().replace(",", ".") or "0")
        except ValueError:
            cena = 0.0

        add_company_extra_service(
            self.firma_id,
            self.miesiac,
            usluga_id,
            ilosc,
            cena,
            self.chk_fakturowac.isChecked(),
        )

        if self.chk_fakturowac.isChecked():
            add_invoice_item_for_service(
                self.firma_id,
                usluga_id,
                ilosc,
                self.miesiac,
                cena_netto=cena,
            )
            QMessageBox.information(self, "OK", "Dodano pozycję do faktury.")
        self.accept()


class MonthlyViewPage(QWidget):
    NOTE_COL_KS = 6
    NOTE_COL_KADRY = 8

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

        self.table_ks.setItemDelegateForColumn(5, IconButtonDelegate(self.table_ks, "start.png", "stoper_ks"))
        self.table_ks.setItemDelegateForColumn(7, IconButtonDelegate(self.table_ks, "add.png", "extra_ks"))
        self.table_ks.setItemDelegateForColumn(8, IconButtonDelegate(self.table_ks, "edit.png", "edit_ks"))
        self.table_ks.setItemDelegateForColumn(9, IconButtonDelegate(self.table_ks, "more.png", "more_ks"))
        self.notes_delegate_ks = ScrollableNotesDelegate(self.table_ks)
        self.table_ks.setItemDelegateForColumn(self.NOTE_COL_KS, self.notes_delegate_ks)

        self.table_kadry.setItemDelegateForColumn(7, IconButtonDelegate(self.table_kadry, "start.png", "stoper_kadry"))
        self.table_kadry.setItemDelegateForColumn(9, IconButtonDelegate(self.table_kadry, "add.png", "extra_kadry"))
        self.table_kadry.setItemDelegateForColumn(10, IconButtonDelegate(self.table_kadry, "edit.png", "edit_kadry"))
        self.notes_delegate_kadry = ScrollableNotesDelegate(self.table_kadry)
        self.table_kadry.setItemDelegateForColumn(self.NOTE_COL_KADRY, self.notes_delegate_kadry)
        self.docs_delegate_ks = DeliveredDateDelegate(self.table_ks)
        self.docs_delegate_kadry = DeliveredDateDelegate(self.table_kadry)
        self.table_ks.setItemDelegateForColumn(1, self.docs_delegate_ks)
        self.table_kadry.setItemDelegateForColumn(1, self.docs_delegate_kadry)
        self.etap_delegate_ks = EtapComboDelegate(self.table_ks)
        self.etap_delegate_kadry = EtapComboDelegate(self.table_kadry)
        self.table_ks.setItemDelegateForColumn(2, self.etap_delegate_ks)
        self.table_kadry.setItemDelegateForColumn(2, self.etap_delegate_kadry)

        self.table_ks.viewport().installEventFilter(self)
        self.table_kadry.viewport().installEventFilter(self)
        self.table_ks.doubleClicked.connect(self._on_table_double_click)
        self.table_kadry.doubleClicked.connect(self._on_table_double_click)

        self._setup_column_widths()

        self.month_combo.currentTextChanged.connect(self._on_filters_changed)
        self.employee_combo.currentIndexChanged.connect(self._on_filters_changed)
        self.status_combo.currentIndexChanged.connect(self._on_filters_changed)
        self.btn_refresh.clicked.connect(self._reload_rows)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Wheel:
            table = self.table_ks if obj is self.table_ks.viewport() else self.table_kadry
            index = table.indexAt(event.position().toPoint())
            note_col = self.NOTE_COL_KS if table is self.table_ks else self.NOTE_COL_KADRY
            if index.isValid() and index.column() == note_col:
                delegate = (
                    self.notes_delegate_ks
                    if table is self.table_ks
                    else self.notes_delegate_kadry
                )
                line = QFontMetrics(table.font()).lineSpacing()
                delta = event.pixelDelta().y() or round(event.angleDelta().y() / 120 * line)
                if delegate.scroll(
                    index,
                    delta,
                    table.columnWidth(note_col),
                    table.rowHeight(index.row()),
                ):
                    table.viewport().update(table.visualRect(index))
                    event.accept()
                    return True
        if event.type() == QEvent.Type.MouseButtonRelease:
            if obj in (self.table_ks.viewport(), self.table_kadry.viewport()):
                table = self.table_ks if obj is self.table_ks.viewport() else self.table_kadry
                index = table.indexAt(event.pos())
                if not index.isValid():
                    return super().eventFilter(obj, event)
                delegate = table.itemDelegateForColumn(index.column())
                if not hasattr(delegate, "role"):
                    return super().eventFilter(obj, event)

                self.handle_button_click(index, delegate.role)
                return True
        return super().eventFilter(obj, event)

    def _on_table_double_click(self, index: QModelIndex):
        table = self.sender()
        note_col = self.NOTE_COL_KS if table is self.table_ks else self.NOTE_COL_KADRY
        if index.column() == note_col:
            row = index.model().get_row(index.row())
            if row:
                self._open_notes(row["id"])

    def _setup_column_widths(self):
        for table, note_col in (
            (self.table_ks, self.NOTE_COL_KS),
            (self.table_kadry, self.NOTE_COL_KADRY),
        ):
            header = table.horizontalHeader()
            for col in range(table.model().columnCount()):
                header.setSectionResizeMode(
                    col,
                    QHeaderView.ResizeMode.Stretch
                    if col in (0, note_col)
                    else QHeaderView.ResizeMode.ResizeToContents,
                )
            vat_col = table.model()._vat_cols()[0]
            etap_col = table.model().COL_ETAP
            metrics = header.fontMetrics()
            etap_width = max(metrics.horizontalAdvance(text) for text in ETAP_CHOICES) + 40
            header.setSectionResizeMode(etap_col, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(etap_col, etap_width)
            header.moveSection(header.visualIndex(vat_col), 1)
            header.moveSection(header.visualIndex(vat_col + 1), 2)

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
        own = self.employee_combo.findData(self.logic.pracownik_id)
        if own >= 0:
            self.employee_combo.setCurrentIndex(own)

    def _current_month(self) -> str:
        return self.month_combo.currentText()

    def _current_employee_id(self) -> Optional[int]:
        idx = self.employee_combo.currentIndex()
        if idx < 0:
            return None
        val = self.employee_combo.itemData(idx)
        return int(val) if val is not None else None

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
        rows_prepared: List[Dict[str, Any]] = []
        for r in self._rows:
            rr = dict(r)
            sekundy_ks = rr.get("sekundy_ksiegowosc", 0)
            sekundy_kadry = rr.get("sekundy_kadry", 0)
            rr["czas_ks_str"] = self.logic.format_seconds(
                self.logic.get_timer_total_seconds(rr["id"], "ksiegowosc", sekundy_ks)
            )
            rr["czas_kadry_str"] = self.logic.format_seconds(
                self.logic.get_timer_total_seconds(rr["id"], "kadry", sekundy_kadry)
            )
            rr.setdefault("uop", 0)
            rr.setdefault("uz", 0)
            rr.setdefault("uod", 0)
            rows_prepared.append(rr)

        self.model_ks.set_rows(rows_prepared)
        self.model_kadry.set_rows(rows_prepared)

    def _update_running_timer(self):
        if self.logic.aktywna_firma_stoper_ks and self.logic.czas_start_ks:
            fid = self.logic.aktywna_firma_stoper_ks
            for row_idx in range(self.model_ks.rowCount()):
                row = self.model_ks.get_row(row_idx)
                if row and row.get("id") == fid:
                    delta = self.logic.get_timer_total_seconds(
                        fid, "ksiegowosc", row.get("sekundy_ksiegowosc", 0)
                    )
                    row["czas_ks_str"] = TimerUtils.format_seconds(delta)
                    idx = self.model_ks.index(row_idx, 4)
                    self.model_ks.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DisplayRole])
                    break

        if self.logic.aktywna_firma_stoper_kadry and self.logic.czas_start_kadry:
            fid = self.logic.aktywna_firma_stoper_kadry
            for row_idx in range(self.model_kadry.rowCount()):
                row = self.model_kadry.get_row(row_idx)
                if row and row.get("id") == fid:
                    delta = self.logic.get_timer_total_seconds(
                        fid, "kadry", row.get("sekundy_kadry", 0)
                    )
                    row["czas_kadry_str"] = TimerUtils.format_seconds(delta)
                    idx = self.model_kadry.index(row_idx, 6)
                    self.model_kadry.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DisplayRole])
                    break

    def handle_button_click(self, index: QModelIndex, role: str):
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
        self._reload_rows()

    def _on_etap_changed(self, firma_id: int):
        self.logic.schedule_status_autosave(firma_id)

    def _on_docs_changed(self, firma_id: int):
        self.logic.schedule_status_autosave(firma_id)

    def _on_kadry_changed(self, firma_id: int):
        self.logic.schedule_status_autosave(firma_id)

    def _on_toggle_stoper_ks(self, firma_id: int):
        if self.logic.aktywna_firma_stoper_ks == firma_id:
            self.logic.toggle_stoper(
                firma_id,
                "ksiegowosc",
                None,
            )
        else:
            dlg = ActivitySelectionDialog(
                "ksiegowosc",
                firma_id,
                self.pracownik_id,
                self,
            )

            if dlg.exec() != QDialog.DialogCode.Accepted:
                return

            payload = dlg.get_payload()

            self.logic.toggle_stoper(
                firma_id,
                "ksiegowosc",
                payload,
            )

        self._reload_rows()
        self.table_ks.viewport().update()

    def _on_toggle_stoper_kadry(self, firma_id: int):
        if self.logic.aktywna_firma_stoper_kadry == firma_id:
            self.logic.toggle_stoper(
                firma_id,
                "kadry",
                None,
            )
        else:
            dlg = ActivitySelectionDialog(
                "kadry",
                firma_id,
                self.pracownik_id,
                self,
            )

            if dlg.exec() != QDialog.DialogCode.Accepted:
                return

            payload = dlg.get_payload()

            self.logic.toggle_stoper(
                firma_id,
                "kadry",
                payload,
            )

        self._reload_rows()
        self.table_kadry.viewport().update()

    def _on_extra_service(self, firma_id: int):
        dlg = InlineExtraServiceWindow(
            firma_id,
            self._current_month(),
            parent=self,
        )
        dlg.exec()

    def _on_edit_company(self, firma_id: int):
        # zatrzymaj timer aby UI nie odświeżał się co sekundę
        if hasattr(self, "_timer"):
            self._timer.stop()

        try:
            self.logic.open_company_edit_window(firma_id)
        except PermissionError as exc:
            QMessageBox.warning(self, "Brak uprawnień", str(exc))

        # po zamknięciu okna edycji wznów timer
        if hasattr(self, "_timer"):
            self._timer.start(1000)

        self._reload_rows()

    def _on_more_menu(self, firma_id: int):
        menu = QMenu(self)

        from app.db.dao_companies import get_company
        firma = get_company(firma_id) or {}
        services_off = bool(firma.get("uslugi_zawieszone"))
        act_suspend = QAction("Wznów wykonywanie usług" if services_off else "Zawieś wykonywanie usług", self)
        act_archive = QAction("Przenieś do archiwum", self)
        act_activity = QAction("Pokaż aktywności", self)

        menu.addAction(act_suspend)
        menu.addAction(act_archive)
        act_restore = QAction("Przywróć do aktywnych", self)
        menu.addAction(act_restore)
        act_restore.triggered.connect(lambda: self._on_restore_active(firma_id))
        menu.addSeparator()
        act_vat = QAction("Sprawdź status VAT", self)
        act_ceidg = QAction("Zawieszenie firmy", self)
        menu.addAction(act_vat)
        menu.addAction(act_ceidg)
        act_vat.triggered.connect(lambda: self._on_check_vat(firma_id))
        act_ceidg.triggered.connect(lambda: self._on_check_ceidg(firma_id))
        menu.addSeparator()
        menu.addAction(act_activity)

        act_suspend.triggered.connect(lambda: self._on_toggle_services(firma_id, not services_off))
        act_archive.triggered.connect(lambda: self._on_move_archive(firma_id))
        act_activity.triggered.connect(lambda: self._on_show_activity(firma_id))

        menu.exec(QCursor.pos())

    def _on_check_vat(self, firma_id: int):
        try:
            res = self.logic.check_vat_status(firma_id)
        except PermissionError as exc:
            QMessageBox.warning(self, "Brak uprawnień", str(exc))
            return
        names = {"czynny": "czynny", "zwolniony": "nieaktywny", "nieznany": "nieznany status", None: "bez zmian"}
        text = f"VAT (Biała Lista): {names[res['vat']]}\nVAT UE (VIES): {names[res['vat_ue']]}"
        if res["errors"]:
            text += "\n\n" + "\n".join(res["errors"])
        QMessageBox.information(self, "Status VAT", text)
        self._reload_rows()

    def _on_check_ceidg(self, firma_id: int):
        try:
            res = self.logic.check_ceidg_suspension(firma_id)
        except PermissionError as exc:
            QMessageBox.warning(self, "Brak uprawnień", str(exc))
            return
        if res["error"]:
            QMessageBox.warning(self, "Zawieszenie firmy", res["error"])
            return
        text = "CEIDG: działalność " + res["state"] + "."
        if res["change"] == "suspended":
            text += "\nFirma przeniesiona do zawieszonych, pracownicy dostali powiadomienie."
        elif res["change"] == "resumed":
            text += "\nFirma przywrócona do aktywnych, pracownicy dostali powiadomienie."
        QMessageBox.information(self, "Zawieszenie firmy", text)
        self._reload_rows()

    def _on_toggle_services(self, firma_id: int, suspended: bool):
        try:
            self.logic.toggle_services_suspended(firma_id, suspended)
        except PermissionError as exc:
            QMessageBox.warning(self, "Brak uprawnień", str(exc))
            return
        QMessageBox.information(
            self,
            "Usługi",
            "Usługi zawieszone. Pracownicy dostali powiadomienie." if suspended
            else "Usługi wznowione. Pracownicy dostali powiadomienie.",
        )
        self._reload_rows()

    def _on_move_archive(self, firma_id: int):
        try:
            self.logic.move_company_to_archive(firma_id)
        except PermissionError as exc:
            QMessageBox.warning(self, "Brak uprawnień", str(exc))
            return
        QMessageBox.information(self, "Archiwum", "Firma przeniesiona do archiwum.")
        self._reload_rows()

    def _on_restore_active(self, firma_id: int):
        from app.db.dao_companies import set_company_status
        set_company_status(firma_id, "Aktywne")
        self._reload_rows()

    def _on_show_activity(self, firma_id: int):
        dlg = ActivityDialog(
            firma_id,
            self._current_month(),
            parent=self,
        )
        dlg.exec()
