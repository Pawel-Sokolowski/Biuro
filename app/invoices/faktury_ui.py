import logging
import math
from datetime import datetime

from PyQt6.QtCore import Qt, QUrl, pyqtSignal, QModelIndex
from PyQt6.QtGui import QColor, QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMessageBox, QPushButton, QSpinBox,
    QStyledItemDelegate, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from app.db.dao_conn import get_conn
from app.db.dao_invoices import (
    InvoiceLockedError, can_access_invoice, create_correction, delete_draft_invoice,
    delete_issued_invoice, get_invoice_by_id, get_invoice_positions, list_invoices, mark_exported, set_ksef_number,
    update_invoice_draft,
)
from app.invoices.positions import normalize_position
from app.invoices.faktury_logic import FakturyLogic
from app.invoices.invoice_editor import InvoiceEditorDialog
from app.invoices.manual_invoice_dialog import ManualInvoiceDialog, KontrahenciDialog
from app.invoices.ksef import ksef_url
from app.invoices.ksef_client import KsefError
from app.invoices.ksef_sync import is_configured as ksef_configured, sync_month
from app.security.authorization import can, is_administrator
from app.utils.saldeo_exporter import export_saldeo_xlsx

logger = logging.getLogger(__name__)

MONTHS = [
    ("01", "Styczeń"), ("02", "Luty"), ("03", "Marzec"), ("04", "Kwiecień"),
    ("05", "Maj"), ("06", "Czerwiec"), ("07", "Lipiec"), ("08", "Sierpień"),
    ("09", "Wrzesień"), ("10", "Październik"), ("11", "Listopad"), ("12", "Grudzień"),
]
KSEF_LINK_COLOR = QColor("#1c70b7")


class MonthYearDialog(QDialog):
    def __init__(self, parent=None, initial_month=None, initial_year=None):
        super().__init__(parent)
        self.setWindowTitle("Wybierz miesiąc i rok")
        self.setModal(True)
        layout = QFormLayout(self)

        self.month_cb = QComboBox()
        for code, name in MONTHS:
            self.month_cb.addItem(f"{code} - {name}", code)

        self.year_sb = QSpinBox()
        self.year_sb.setRange(2000, 2100)
        self.year_sb.setValue(initial_year or datetime.now().year)

        if initial_month and "-" in initial_month:
            try:
                first, second = initial_month.split("-")
                year, month = (first, second) if len(first) == 4 else (second, first)
                self.year_sb.setValue(int(year))
                self.month_cb.setCurrentIndex(int(month) - 1)
            except ValueError:
                logger.warning("Niepoprawny miesiąc: %s", initial_month, exc_info=True)

        layout.addRow("Miesiąc:", self.month_cb)
        layout.addRow("Rok:", self.year_sb)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def selected(self):
        return f"{self.year_sb.value()}-{self.month_cb.currentData()}"


def _make_table(headers, multi=False):
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(
        QAbstractItemView.SelectionMode.ExtendedSelection if multi
        else QAbstractItemView.SelectionMode.SingleSelection
    )
    return table


def _cell(text, align_right=False):
    item = QTableWidgetItem("" if text is None else str(text))
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if align_right:
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return item


def _kind_label(invoice):
    return "Korekta" if invoice.get("rodzaj") == "korekta" else "Faktura"


class QuantityDelegate(QStyledItemDelegate):
    committed = pyqtSignal(QModelIndex)

    def setModelData(self, editor, model, index):
        super().setModelData(editor, model, index)
        self.committed.emit(index)


class FakturyPage(QWidget):
    DRAFT_HEADERS = ["ID", "Rodzaj", "Nabywca", "Miesiąc", "Numer", "Kwota netto", "Kwota brutto",
                     "Data wystawienia", "Termin płatności"]
    ISSUED_HEADERS = ["ID", "Rodzaj", "Nabywca", "Miesiąc", "Numer", "Kwota netto", "Kwota brutto",
                      "Data eksportu", "Numer KSeF"]
    ISSUED_KSEF_COLUMN = 8

    def __init__(self, pracownik_id, parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self.logic = FakturyLogic()

        layout = QVBoxLayout(self)
        layout.addLayout(self._build_filters())

        self.tabs = QTabWidget()
        self.tab_robocze = QWidget()
        self.tab_wystawione = QWidget()
        self.tabs.addTab(self.tab_robocze, "Faktury robocze")
        self.tabs.addTab(self.tab_wystawione, "Faktury wystawione")
        layout.addWidget(self.tabs)

        self._build_tab_robocze()
        self._build_tab_wystawione()
        self.refresh()

    # ---------------- budowa ekranu ----------------
    def _build_filters(self):
        controls = QHBoxLayout()
        self.firma_combo = QComboBox()
        self.firma_combo.addItem("Wszystkie firmy", None)
        conn = get_conn()
        try:
            rows = conn.execute("SELECT id, nazwa FROM firmy ORDER BY nazwa").fetchall()
        finally:
            conn.close()
        for row in rows:
            self.firma_combo.addItem(row[1], row[0])
        self.firma_combo.currentIndexChanged.connect(self.refresh)
        controls.addWidget(QLabel("Firma:"))
        controls.addWidget(self.firma_combo)

        self.month_button = QPushButton(datetime.now().strftime("%m-%Y"))
        self.month_button.clicked.connect(self._open_month_dialog)
        controls.addWidget(QLabel("Miesiąc (faktury robocze):"))
        controls.addWidget(self.month_button)

        self.btn_manual = QPushButton("Wystaw faktur\u0119")
        self.btn_manual.clicked.connect(self._new_manual_invoice)
        controls.addWidget(self.btn_manual)
        self.btn_contractors = QPushButton("Kontrahenci")
        self.btn_contractors.clicked.connect(self._manage_contractors)
        controls.addWidget(self.btn_contractors)
        self.btn_generate_all = QPushButton("Generuj faktury")
        self.btn_generate_all.clicked.connect(self._on_generate_clicked)
        controls.addWidget(self.btn_generate_all)
        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self.refresh)
        controls.addWidget(btn_refresh)
        controls.addStretch()
        return controls

    def _build_tab_robocze(self):
        layout = QVBoxLayout(self.tab_robocze)
        buttons = QHBoxLayout()
        self.btn_edit = QPushButton("Edytuj pozycje")
        self.btn_edit.clicked.connect(self._edit_selected_draft)
        self.btn_export = QPushButton("Eksportuj zaznaczone")
        self.btn_export.clicked.connect(self._export_selected_drafts)
        self.btn_delete = QPushButton("Usuń roboczą")
        self.btn_delete.clicked.connect(self._delete_selected_draft)
        for button in (self.btn_edit, self.btn_export, self.btn_delete):
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)

        self.draft_table = _make_table(self.DRAFT_HEADERS, multi=True)
        self.draft_table.itemSelectionChanged.connect(self._show_draft_positions)
        self.draft_table.cellDoubleClicked.connect(lambda *_: self._edit_selected_draft())
        layout.addWidget(self.draft_table)

        layout.addWidget(QLabel("Pozycje faktury"))
        self.positions_table = _make_table(["Nazwa", "Ilość", "Jednostka", "Netto", "VAT", "Kategoria"])
        quantity_delegate = QuantityDelegate(self.positions_table)
        quantity_delegate.committed.connect(self._on_draft_position_changed)
        self.positions_table.setItemDelegateForColumn(1, quantity_delegate)
        self.positions_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self._shown_draft_id = None
        layout.addWidget(self.positions_table)

    def _build_tab_wystawione(self):
        layout = QVBoxLayout(self.tab_wystawione)
        buttons = QHBoxLayout()
        self.btn_view = QPushButton("Podgląd pozycji")
        self.btn_view.clicked.connect(self._view_selected_issued)
        self.btn_reexport = QPushButton("Eksportuj ponownie")
        self.btn_reexport.clicked.connect(self._reexport_selected)
        self.btn_correction = QPushButton("Wystaw korektę")
        self.btn_correction.clicked.connect(self._create_correction)
        self.btn_ksef = QPushButton("Numer KSeF...")
        self.btn_ksef.clicked.connect(self._edit_ksef_number)
        self.btn_ksef_download = QPushButton("Pobierz z KSeF")
        self.btn_ksef_download.clicked.connect(self._download_from_ksef)
        self.btn_delete_issued = QPushButton("Usuń wystawioną")
        self.btn_delete_issued.clicked.connect(self._delete_selected_issued)
        for button in (self.btn_view, self.btn_reexport, self.btn_correction, self.btn_ksef,
                       self.btn_ksef_download, self.btn_delete_issued):
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)

        self.issued_table = _make_table(self.ISSUED_HEADERS, multi=True)
        self.issued_table.cellClicked.connect(self._on_issued_cell_clicked)
        self.issued_table.cellDoubleClicked.connect(self._on_issued_double_click)
        layout.addWidget(self.issued_table)

    def _download_from_ksef(self):
        if not ksef_configured():
            QMessageBox.warning(self, "KSeF", "KSeF nie jest skonfigurowany (NIP i token w ustawieniach).")
            return
        month = self._current_month()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = sync_month(month)
        except KsefError as exc:
            QMessageBox.critical(self, "KSeF", str(exc))
            return
        except Exception:
            logger.exception("Błąd pobierania z KSeF")
            QMessageBox.critical(self, "KSeF", "Nie udało się pobrać faktur z KSeF. Szczegóły w logach.")
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.refresh()
        message = f"Miesiąc {month}\nDopasowano: {len(result['dopasowane'])}\nNiedopasowano: {len(result['niedopasowane'])}"
        if result["niedopasowane"]:
            message += "\n\nNiedopasowane faktury:\n" + "\n".join(result["niedopasowane"][:15])
        QMessageBox.information(self, "KSeF", message)

    # ---------------- filtry ----------------
    def _current_month(self):
        try:
            mm, yyyy = self.month_button.text().strip().split("-")
            return f"{int(yyyy)}-{int(mm):02d}"
        except ValueError:
            return datetime.now().strftime("%Y-%m")

    def _open_month_dialog(self):
        dialog = MonthYearDialog(self, initial_month=self.month_button.text().strip())
        if dialog.exec() == QDialog.DialogCode.Accepted:
            year, month = dialog.selected().split("-")
            self.month_button.setText(f"{month}-{year}")
            self.refresh()

    # ---------------- generowanie ----------------
    def _on_generate_clicked(self):
        miesiac = self._current_month()
        if QMessageBox.question(
            self, "Generowanie faktur",
            f"Czy na pewno chcesz wygenerować faktury dla wszystkich firm za {miesiac}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            result = self.logic.generate_and_save_invoices_for_month(miesiac, only_active=True)
        except Exception as exc:
            logger.exception("Błąd generowania faktur")
            QMessageBox.critical(self, "Błąd", f"Wyjątek podczas generowania: {exc}")
            return
        message = (
            f"Przetworzono: {result.get('processed', 0)} firm.\n"
            f"Utworzono: {result.get('created', 0)} faktur.\n"
            f"Zaktualizowano: {result.get('updated', 0)} faktur."
        )
        if result.get("skipped"):
            message += f"\n\nPominięto {result['skipped']} faktur:"
            details = result.get("skipped_details") or []
            if details:
                message += "\n" + "\n".join(details[:30])
                if len(details) > 30:
                    message += f"\n… i {len(details) - 30} więcej."
        if result.get("errors"):
            errors = result["errors"]
            message += f"\n\nBłędy: {len(errors)}:\n" + "\n".join(errors[:15])
            if len(errors) > 15:
                message += f"\n… i {len(errors) - 15} więcej (szczegóły w logach)."
            QMessageBox.warning(self, "Generowanie zbiorcze", message)
            self.refresh()
            return
        QMessageBox.information(self, "Generowanie zbiorcze", message)
        self.refresh()

    # ---------------- odświeżanie ----------------
    def _new_manual_invoice(self):
        dialog = ManualInvoiceDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _manage_contractors(self):
        KontrahenciDialog(self).exec()

    def refresh(self):
        firma_id = self.firma_combo.currentData()
        self._fill_drafts(list_invoices(self.pracownik_id, False, self._current_month(), firma_id))
        self._fill_issued(list_invoices(self.pracownik_id, True, None, firma_id))

    def _fill_drafts(self, invoices):
        self.draft_table.setRowCount(0)
        self.positions_table.setRowCount(0)
        for inv in invoices:
            row = self.draft_table.rowCount()
            self.draft_table.insertRow(row)
            values = [inv["id"], _kind_label(inv), inv["firma_nazwa"], inv["miesiac"], inv["numer"],
                      f"{inv['kwota_netto'] or 0:.2f}", f"{inv['kwota_brutto'] or 0:.2f}",
                      inv["data_wystawienia"], inv["termin_platnosci"]]
            for column, value in enumerate(values):
                self.draft_table.setItem(row, column, _cell(value, column in (5, 6)))

    def _fill_issued(self, invoices):
        self.issued_table.setRowCount(0)
        for inv in invoices:
            row = self.issued_table.rowCount()
            self.issued_table.insertRow(row)
            values = [inv["id"], _kind_label(inv), inv["firma_nazwa"], inv["miesiac"], inv["numer"],
                      f"{inv['kwota_netto'] or 0:.2f}", f"{inv['kwota_brutto'] or 0:.2f}",
                      inv["data_eksportu"], inv["ksef_numer"]]
            for column, value in enumerate(values):
                self.issued_table.setItem(row, column, _cell(value, column in (5, 6)))
            ksef_item = self.issued_table.item(row, self.ISSUED_KSEF_COLUMN)
            if inv["ksef_numer"]:
                font = ksef_item.font()
                font.setUnderline(True)
                ksef_item.setFont(font)
                ksef_item.setForeground(KSEF_LINK_COLOR)
                ksef_item.setToolTip("Kliknij, aby otworzyć KSeF (numer zostanie skopiowany do schowka)")

    @staticmethod
    def _selected_ids(table):
        rows = sorted({index.row() for index in table.selectedIndexes()})
        return [int(table.item(row, 0).text()) for row in rows]

    # ---------------- faktury robocze ----------------
    def _show_draft_positions(self):
        self._shown_draft_id = None
        self.positions_table.blockSignals(True)
        self.positions_table.setRowCount(0)
        ids = self._selected_ids(self.draft_table)
        if len(ids) != 1:
            self.positions_table.blockSignals(False)
            return
        invoice = get_invoice_by_id(ids[0])
        if not invoice or invoice.get("eksportowana"):
            self.positions_table.blockSignals(False)
            return
        self._shown_draft_id = ids[0]
        for position in get_invoice_positions(ids[0]):
            row = self.positions_table.rowCount()
            self.positions_table.insertRow(row)
            netto = position.get("netto") or position.get("wartosc_netto") or 0
            try:
                netto = f"{float(netto):.2f}"
            except (TypeError, ValueError):
                netto = str(netto)
            values = [position.get("nazwa") or position.get("opis", ""), position.get("ilosc", ""),
                      position.get("jednostka", ""), netto,
                      position.get("vat") or position.get("stawka_vat", ""), position.get("kategoria", "")]
            for column, value in enumerate(values):
                item = _cell(value)
                if column == 1:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                self.positions_table.setItem(row, column, item)
        self.positions_table.blockSignals(False)

    def _on_draft_position_changed(self, index):
        if index.column() != 1 or self._shown_draft_id is None:
            return
        invoice_id = self._shown_draft_id
        invoice = get_invoice_by_id(invoice_id)
        if not invoice or invoice.get("eksportowana") or not can_access_invoice(
            self.pracownik_id, invoice["firma_id"]
        ):
            self._show_draft_positions()
            return
        try:
            quantity = float(self.positions_table.item(index.row(), index.column()).text().replace(",", "."))
            if not math.isfinite(quantity):
                raise ValueError("Ilość musi być skończoną liczbą.")
            positions = get_invoice_positions(invoice_id)
            if index.row() >= len(positions):
                raise ValueError("Nie znaleziono edytowanej pozycji.")
            positions[index.row()] = normalize_position(positions[index.row()])
            positions[index.row()]["ilosc"] = quantity
            update_invoice_draft(
                invoice_id,
                positions,
                numer=invoice.get("numer"),
                data_wystawienia=invoice.get("data_wystawienia"),
                data_sprzedazy=invoice.get("data_sprzedazy"),
                termin_platnosci=invoice.get("termin_platnosci"),
                przyczyna_korekty=invoice.get("przyczyna_korekty"),
            )
        except (TypeError, ValueError, InvoiceLockedError) as exc:
            QMessageBox.warning(self, "Pozycja faktury", f"Nie udało się zapisać ilości: {exc}")
            self._show_draft_positions()
            return
        self.refresh()
        self._select_draft(invoice_id)

    def _select_draft(self, invoice_id):
        for row in range(self.draft_table.rowCount()):
            item = self.draft_table.item(row, 0)
            if item and item.text() == str(invoice_id):
                self.draft_table.selectRow(row)
                return

    def _edit_selected_draft(self):
        ids = self._selected_ids(self.draft_table)
        if len(ids) != 1:
            QMessageBox.information(self, "Faktura", "Zaznacz jedną fakturę do edycji.")
            return
        self._open_editor(ids[0])

    def _open_editor(self, invoice_id):
        invoice = get_invoice_by_id(invoice_id)
        if not invoice or not can_access_invoice(self.pracownik_id, invoice["firma_id"]):
            QMessageBox.warning(self, "Brak uprawnień", "Nie masz dostępu do tej faktury.")
            return
        if InvoiceEditorDialog(invoice, self).exec() == QDialog.DialogCode.Accepted:
            self.refresh()

    def _delete_selected_draft(self):
        ids = self._selected_ids(self.draft_table)
        if not ids:
            QMessageBox.information(self, "Faktura", "Zaznacz fakturę roboczą do usunięcia.")
            return
        if QMessageBox.question(
            self, "Usuwanie", f"Usunąć zaznaczone faktury robocze ({len(ids)})?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        for invoice_id in ids:
            invoice = get_invoice_by_id(invoice_id)
            if invoice and can_access_invoice(self.pracownik_id, invoice["firma_id"]):
                try:
                    delete_draft_invoice(invoice_id)
                except InvoiceLockedError as exc:
                    QMessageBox.warning(self, "Faktura", str(exc))
        self.refresh()

    def _delete_selected_issued(self):
        ids = self._selected_ids(self.issued_table)
        if not ids:
            QMessageBox.information(self, "Faktura", "Zaznacz wystawioną fakturę do usunięcia.")
            return
        if not self._can_export():
            return
        if QMessageBox.question(
            self, "Usuwanie",
            f"Trwale usunąć zaznaczone wystawione faktury ({len(ids)})?\n"
            "Tej operacji nie można cofnąć.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        for invoice_id in ids:
            invoice = get_invoice_by_id(invoice_id)
            if invoice and can_access_invoice(self.pracownik_id, invoice["firma_id"]):
                delete_issued_invoice(invoice_id)
        self.refresh()

    # ---------------- eksport ----------------
    def _can_export(self):
        if is_administrator(self.pracownik_id) or can(self.pracownik_id, "can_export_data"):
            return True
        QMessageBox.warning(self, "Brak uprawnień", "Nie masz uprawnień do eksportowania danych.")
        return False

    def _export_selected_drafts(self):
        self._export_ids(self._selected_ids(self.draft_table), mark=True)

    def _reexport_selected(self):
        self._export_ids(self._selected_ids(self.issued_table), mark=False)

    def _export_ids(self, ids, mark):
        if not self._can_export():
            return
        if not ids:
            QMessageBox.information(self, "Eksport", "Zaznacz faktury do eksportu.")
            return
        invoices = []
        for invoice_id in ids:
            invoice = get_invoice_by_id(invoice_id)
            if invoice and can_access_invoice(self.pracownik_id, invoice["firma_id"]):
                company_name = self._company_name(invoice["firma_id"]) if invoice["firma_id"] else ""
                invoices.append(dict(
                    invoice,
                    firma_nazwa=company_name or invoice.get("nabywca_pelna") or invoice.get("nabywca_skrocona") or "",
                    firma_nip=invoice.get("nabywca_nip") or "",
                    firma_regon=invoice.get("nabywca_regon") or "",
                    firma_miasto=invoice.get("nabywca_miasto") or "",
                    firma_kod=invoice.get("nabywca_kod") or "",
                    firma_ulica=invoice.get("nabywca_adres") or "",
                    firma_email=invoice.get("nabywca_email") or "",
                ))
        if not invoices:
            QMessageBox.warning(self, "Eksport", "Brak faktur dostępnych do eksportu.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Zapisz XLSX", "", "Excel (*.xlsx)")
        if not path:
            return
        try:
            export_saldeo_xlsx(invoices, path)
            if mark:
                mark_exported([inv["id"] for inv in invoices], self.pracownik_id)
            QMessageBox.information(self, "OK", "Faktury wyeksportowano.")
        except Exception as exc:
            logger.exception("Błąd eksportu faktur")
            QMessageBox.critical(self, "Błąd", f"Nie udało się wyeksportować faktur: {exc}")
        self.refresh()

    @staticmethod
    def _company_name(firma_id):
        conn = get_conn()
        try:
            row = conn.execute("SELECT nazwa FROM firmy WHERE id = %s", (firma_id,)).fetchone()
            return row[0] if row else ""
        finally:
            conn.close()

    # ---------------- faktury wystawione ----------------
    def _view_selected_issued(self):
        ids = self._selected_ids(self.issued_table)
        if len(ids) != 1:
            QMessageBox.information(self, "Faktura", "Zaznacz jedną fakturę.")
            return
        self._open_editor(ids[0])

    def _on_issued_double_click(self, row, column):
        if column != self.ISSUED_KSEF_COLUMN:
            self._open_editor(int(self.issued_table.item(row, 0).text()))

    def _create_correction(self):
        ids = self._selected_ids(self.issued_table)
        if len(ids) != 1:
            QMessageBox.information(self, "Korekta", "Zaznacz jedną wystawioną fakturę.")
            return
        invoice = get_invoice_by_id(ids[0])
        if not invoice or not can_access_invoice(self.pracownik_id, invoice["firma_id"]):
            QMessageBox.warning(self, "Brak uprawnień", "Nie masz dostępu do tej faktury.")
            return
        reason, ok = QInputDialog.getText(self, "Korekta", "Przyczyna korekty:")
        if not ok:
            return
        try:
            correction_id = create_correction(ids[0], reason.strip())
        except ValueError as exc:
            QMessageBox.warning(self, "Korekta", str(exc))
            return
        self.tabs.setCurrentWidget(self.tab_robocze)
        self.month_button.setText(self._to_button_month(invoice["miesiac"]))
        self.refresh()
        self._open_editor(correction_id)

    @staticmethod
    def _to_button_month(miesiac):
        year, month = miesiac.split("-")
        return f"{month}-{year}"

    def _edit_ksef_number(self):
        ids = self._selected_ids(self.issued_table)
        if len(ids) != 1:
            QMessageBox.information(self, "KSeF", "Zaznacz jedną fakturę.")
            return
        invoice = get_invoice_by_id(ids[0])
        if not invoice or not can_access_invoice(self.pracownik_id, invoice["firma_id"]):
            return
        value, ok = QInputDialog.getText(
            self, "Numer KSeF", "Numer KSeF faktury (puste = usuń):", text=invoice.get("ksef_numer") or ""
        )
        if ok:
            set_ksef_number(ids[0], value)
            self.refresh()

    def _on_issued_cell_clicked(self, row, column):
        if column != self.ISSUED_KSEF_COLUMN:
            return
        numer = self.issued_table.item(row, column).text().strip()
        if not numer:
            return
        QGuiApplication.clipboard().setText(numer)
        QDesktopServices.openUrl(QUrl(ksef_url(numer)))
