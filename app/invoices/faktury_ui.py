import logging
import math
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from time import perf_counter

from PyQt6.QtCore import QDate, Qt, QUrl, pyqtSignal, QModelIndex, QObject, QThread, QTimer, QSettings, QRunnable, QThreadPool
from PyQt6.QtGui import QColor, QDesktopServices, QFontDatabase, QFontMetrics, QGuiApplication
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QCompleter, QDialog, QDialogButtonBox, QFileDialog,
    QDateEdit, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QProgressDialog, QPushButton,
    QCheckBox, QDoubleSpinBox, QStyledItemDelegate, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from app.db.dao_conn import get_conn
from app.db.dao_invoices import (
    InvoiceLockedError, InvoiceConflictError, can_access_invoice, create_correction, delete_draft_invoice,
    delete_issued_invoice, get_invoice_by_id, get_invoice_positions, list_invoices, mark_exported, set_ksef_number,
    update_invoice_draft,
    validate_invoice_extra_services_ready,
)
from app.invoices.positions import normalize_position, totals
from app.invoices.vat_summary import VatSummary
from app.invoices.table_state import preserve_table_view
from app.invoices.invoice_delivery import review_delivery, queue_deliveries
from app.db.dao_global_settings import get_extra_services
from app.invoices.faktury_logic import FakturyLogic
from app.invoices.invoice_editor import InvoiceEditorDialog
from app.invoices.manual_invoice_dialog import ManualInvoiceDialog, KontrahenciDialog
from app.invoices.ksef import ksef_url
from app.invoices.invoice_preview import InvoicePreviewDialog
from app.invoices.ksef_client import KsefError
from app.invoices.ksef_sync import assign_downloaded_invoice, is_configured as ksef_configured, sync_month
from app.invoices.ksef_submission import process_invoice_batch
from app.invoices.payments_ui import BankPaymentReview, record_cash_in_dialog, record_payment_in_dialog
from app.db.dao_payments import list_settlements, payments_for_invoices, revert_settlement
from app.monthly_view.month_picker import MonthPicker
from app.security.authorization import can, is_administrator
from app.utils.saldeo_exporter import (
    SaldeoCorrectionExportError, SaldeoExportError, export_saldeo_xlsx, validate_saldeo_invoices,
)

logger = logging.getLogger(__name__)

KSEF_LINK_COLOR = QColor("#1c70b7")


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


def _fmt_date(value):
    if value is None or value == "":
        return ""
    try:
        return value.strftime("%d-%m-%Y")
    except AttributeError:
        text = str(value)[:10]
        parts = text.split("-")
        return "-".join(reversed(parts)) if len(parts) == 3 else text


def _kind_label(invoice):
    return {"korekta": "Korekta", "zaliczkowa": "Zaliczkowa", "proforma": "Proforma"}.get(
        invoice.get("rodzaj"), "Faktura")


class QuantityDelegate(QStyledItemDelegate):
    committed = pyqtSignal(QModelIndex)

    def setModelData(self, editor, model, index):
        super().setModelData(editor, model, index)
        self.committed.emit(index)


class KsefSendSignals(QObject):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(dict)


class KsefSendWorker(QRunnable):
    def __init__(self, invoice_ids, actor_id, send):
        super().__init__()
        self.invoice_ids, self.actor_id, self.send = invoice_ids, actor_id, send
        self.signals = KsefSendSignals()
        self.setAutoDelete(False)

    def _emit(self, name, *args):
        try:
            getattr(self.signals, name).emit(*args)
        except RuntimeError:
            pass

    def run(self):
        try:
            result = process_invoice_batch(self.invoice_ids, self.actor_id, send=self.send,
                                          progress=lambda done, total, text: self._emit("progress", done, total, str(text)))
        except Exception as exc:
            logger.exception("Błąd obsługi wysyłki KSeF")
            result = {"accepted": [], "pending": list(self.invoice_ids), "rejected": [],
                      "errors": [{"id": invoice_id, "description": str(exc)} for invoice_id in self.invoice_ids]}
        self._emit("finished", result)


class KsefSyncWorker(QObject):
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(dict)
    failed = pyqtSignal(str, bool)

    def __init__(self, month, **filters):
        super().__init__()
        self.month = month
        self.filters = filters

    def run(self):
        try:
            result = sync_month(
                self.month,
                progress=lambda done, total, text: self.progress.emit(done, total, text),
                **self.filters,
            )
        except KsefError as exc:
            self.failed.emit(str(exc), True)
            return
        except Exception as exc:
            logger.exception("Błąd pobierania z KSeF")
            self.failed.emit(str(exc), False)
            return
        self.finished.emit(result)


class KsefUnmatchedDialog(QDialog):
    """Ręczne przypisanie pobranych dokumentów KSeF do kartoteki firmy."""

    def __init__(self, items, actor_id, parent=None):
        super().__init__(parent)
        self.items = list(items)
        self.actor_id = actor_id
        self.setWindowTitle("Niedopasowane faktury KSeF")
        self.resize(1150, 560)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Wybierz właściwą firmę dla faktury. Możesz wpisać fragment nazwy lub NIP-u. "
            "Przypisanie korzysta z już pobranego XML-a i nie wysyła kolejnego zapytania do KSeF."
        ))
        self.table = QTableWidget(0, 6, self)
        self.table.setHorizontalHeaderLabels(["Nr faktury", "NIP nabywcy", "Nabywca z KSeF", "Powód", "Firma", ""])
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 120)
        self.table.setColumnWidth(3, 220)
        self.table.setColumnWidth(4, 300)
        self.table.setColumnWidth(5, 100)
        layout.addWidget(self.table)
        conn = get_conn()
        try:
            companies = [dict(row) for row in conn.execute(
                "SELECT id, nazwa, nazwa_skrocona, nip FROM firmy ORDER BY COALESCE(NULLIF(nazwa_skrocona, ''), nazwa)"
            ).fetchall()]
            contractors = [dict(row) for row in conn.execute(
                "SELECT id, nazwa, nip FROM kontrahenci ORDER BY nazwa"
            ).fetchall()]
        finally:
            conn.close()
        if not (is_administrator(actor_id) or can(actor_id, "can_view_finances")):
            companies = [company for company in companies if can_access_invoice(actor_id, company["id"])]
            contractors = []
        self.companies = [dict(c, kind="firma") for c in companies] + [
            dict(c, kind="kontrahent", nazwa_skrocona=None) for c in contractors]
        for item in self.items:
            self._add_row(item)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        close.rejected.connect(self.accept)
        layout.addWidget(close)

    def _add_row(self, item):
        row = self.table.rowCount()
        self.table.insertRow(row)
        for column, value in enumerate((item["invoice_number"], item["buyer_nip"], item["buyer_name"], item["reason"])):
            self.table.setItem(row, column, _cell(value))
        company = QComboBox(self.table)
        company.setEditable(True)
        company.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        company.addItem("— Wybierz firmę lub kontrahenta —", None)
        for candidate in self.companies:
            name = candidate.get("nazwa_skrocona") or candidate["nazwa"]
            label = " (kontrahent)" if candidate["kind"] == "kontrahent" else ""
            company.addItem(f"{name}{label} — NIP {candidate.get('nip') or 'brak'}",
                            (candidate["kind"], candidate["id"]))
        completer = company.completer()
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.table.setCellWidget(row, 4, company)
        assign = QPushButton("Przypisz", self.table)
        assign.clicked.connect(lambda _checked=False, current=row: self._assign(current))
        self.table.setCellWidget(row, 5, assign)

    def _assign(self, row):
        choice = self.table.cellWidget(row, 4).currentData()
        if choice is None:
            QMessageBox.warning(self, "KSeF", "Wybierz firmę lub kontrahenta z listy.")
            return
        kind, owner_id = choice
        item = self.items[row]
        try:
            action, _invoice_id = assign_downloaded_invoice(
                item["data"], item["ksef_number"],
                company_id=int(owner_id) if kind == "firma" else None,
                contractor_id=int(owner_id) if kind == "kontrahent" else None,
            )
        except Exception:
            logger.exception("Błąd ręcznego przypisania faktury KSeF")
            QMessageBox.warning(self, "KSeF", "Nie udało się przypisać faktury. Szczegóły zapisano w logach.")
            return
        if action not in ("utworzone", "zaktualizowane"):
            QMessageBox.warning(self, "KSeF", f"Nie udało się jednoznacznie zapisać faktury: {action}.")
            return
        self.table.setItem(row, 3, _cell("Przypisano ręcznie"))
        self.table.cellWidget(row, 4).setEnabled(False)
        self.table.cellWidget(row, 5).setEnabled(False)
        if self.parent():
            self.parent().refresh()


class FakturyPage(QWidget):
    DRAFT_HEADERS = ["ID", "Rodzaj", "Nabywca", "Miesiąc", "Numer", "Kwota netto", "Kwota brutto",
                     "Data wystawienia", "Termin płatności"]
    ISSUED_HEADERS = ["ID", "Rodzaj", "Nabywca", "Miesiąc", "Numer", "Podgląd", "Netto", "Brutto",
                      "Rozliczono", "Pozostało", "Data rozliczenia", "Data eksportu", "Numer KSeF", "Status KSeF",
                      "E-mail faktury"]
    ISSUED_NUMBER_COLUMN = 4
    ISSUED_PREVIEW_COLUMN = 5
    ISSUED_PAID_AMOUNT_COLUMN = 8
    ISSUED_REMAINING_COLUMN = 9
    ISSUED_PAID_COLUMN = 10
    ISSUED_KSEF_COLUMN = 12
    SETTLEMENT_HEADERS = ["ID", "Data zapłaty", "Numer faktury", "Data wystawienia", "Nabywca", "Kwota",
                          "Sposób", "Przelew / tytuł", "Zaksięgował"]

    def __init__(self, pracownik_id, parent=None):
        super().__init__(parent)
        self.pracownik_id = pracownik_id
        self.logic = FakturyLogic()
        self._view_settings = QSettings("Biuro", "OfficeManager")
        self._payment_filter_key = f"users/{pracownik_id}/invoices/payment_filter"
        self._draft_rows = None
        self._issued_rows = None
        self._issued_payments = None
        self._issued_payment_filter = None
        self._issued_number_filter = None
        self._issued_kind_filter = None
        self._refresh_later = QTimer(self)
        self._refresh_later.setSingleShot(True)
        self._refresh_later.setInterval(250)
        self._refresh_later.timeout.connect(self.refresh)
        self._ksef_thread = None
        self._ksef_worker = None
        self._ksef_progress = None
        self._ksef_send_worker = None
        self._ksef_pending = set()
        self._ksef_poll_counts = {}
        self._ksef_poll_timer = QTimer(self)
        self._ksef_poll_timer.setSingleShot(True)
        self._ksef_poll_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._ksef_poll_timer.setInterval(60000)
        self._ksef_poll_timer.timeout.connect(self._poll_pending_ksef)

        layout = QVBoxLayout(self)
        layout.addLayout(self._build_filters())

        self.tabs = QTabWidget()
        self.tab_wystawione = QWidget()
        self.tab_robocze = QWidget()
        self.tab_dodatkowe = QWidget()
        self.tabs.addTab(self.tab_wystawione, "Faktury wystawione")
        self.tabs.addTab(self.tab_robocze, "Faktury robocze")
        self.tabs.addTab(self.tab_dodatkowe, "Czynności dodatkowe")
        self.tab_rozliczenia = QWidget()
        self.tabs.addTab(self.tab_rozliczenia, "Rozliczenia")
        layout.addWidget(self.tabs)

        self._build_tab_wystawione()
        self._build_tab_robocze()
        self._build_tab_dodatkowe()
        self._build_tab_rozliczenia()
        self.tabs.setCurrentWidget(self.tab_wystawione)
        self.refresh()

    def closeEvent(self, event):
        timer = getattr(self, "_refresh_later", None)
        if timer is not None:
            timer.stop()
        super().closeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "issued_table"):
            self._size_buyer_column()

    def _size_buyer_column(self):
        table = self.issued_table
        other_width = sum(table.columnWidth(c) for c in range(table.columnCount())
                          if c != 2 and not table.isColumnHidden(c))
        table.setColumnWidth(2, max(240, table.viewport().width() - other_width))

    # ---------------- budowa ekranu ----------------
    def _build_filters(self):
        controls = QHBoxLayout()
        self.firma_combo = QComboBox()
        self.firma_combo.setEditable(True)
        self.firma_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.firma_combo.setMinimumWidth(260)
        self.firma_combo.addItem("Wszystkie firmy", None)
        conn = get_conn()
        try:
            rows = conn.execute(
                """
                SELECT id, COALESCE(NULLIF(TRIM(nazwa_skrocona), ''), nazwa)
                FROM firmy
                ORDER BY LOWER(COALESCE(NULLIF(TRIM(nazwa_skrocona), ''), nazwa)), id
                """
            ).fetchall()
            contractors = conn.execute(
                "SELECT id, nazwa FROM kontrahenci ORDER BY LOWER(nazwa), id"
            ).fetchall()
        finally:
            conn.close()
        for row in rows:
            self.firma_combo.addItem(row[1], row[0])
        for row in contractors:
            self.firma_combo.addItem(f"{row[1]} (kontrahent)", -row[0])
        completer = self.firma_combo.completer()
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.activated[str].connect(self._company_text_chosen)
        self.firma_combo.currentIndexChanged.connect(self.refresh)
        controls.addWidget(QLabel("Firma:"))
        controls.addWidget(self.firma_combo)

        self.month_button = MonthPicker(datetime.now().strftime("%Y-%m"), allow_year=True)
        self.month_button.monthChanged.connect(self._invoice_month_changed)
        controls.addWidget(QLabel("Miesiąc:"))
        controls.addWidget(self.month_button)

        self.btn_manual = QPushButton("Wystaw faktur\u0119")
        self.btn_manual.clicked.connect(self._new_manual_invoice)
        controls.addWidget(self.btn_manual)
        self.btn_contractors = QPushButton("Kontrahenci")
        self.btn_contractors.clicked.connect(self._manage_contractors)
        controls.addWidget(self.btn_contractors)
        self.btn_generate_all = QPushButton("Generuj faktury")
        self.btn_generate_all.clicked.connect(self._on_generate_clicked)
        btn_refresh = QPushButton("Odśwież")
        btn_refresh.clicked.connect(self.refresh)
        controls.addWidget(btn_refresh)
        controls.addStretch()
        return controls

    def _build_tab_robocze(self):
        layout = QVBoxLayout(self.tab_robocze)
        buttons = QHBoxLayout()
        self.btn_edit = QPushButton("Edytuj pozycje poniżej")
        self.btn_edit.clicked.connect(self._edit_selected_draft)
        self.btn_header = QPushButton("Dane faktury...")
        self.btn_header.clicked.connect(self._edit_draft_header)
        self.btn_issue_ksef = QPushButton("Wystaw i wyślij do KSeF")
        self.btn_issue_ksef.clicked.connect(lambda: self._send_selected_to_ksef(drafts=True))
        self.btn_export = QPushButton("Wystaw faktury")
        self.btn_export.clicked.connect(self._export_selected_drafts)
        self.btn_delete = QPushButton("Usuń roboczą")
        self.btn_delete.clicked.connect(self._delete_selected_draft)
        self.btn_saldeo_correction = QPushButton("Korekta w Saldeo...")
        self.btn_saldeo_correction.clicked.connect(self._prepare_saldeo_correction)
        for button in (self.btn_generate_all, self.btn_export, self.btn_edit, self.btn_header, self.btn_issue_ksef,
                       self.btn_saldeo_correction, self.btn_delete):
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)

        self.send_pdf_draft = QCheckBox("Po przyjęciu w KSeF wyślij PDF do klienta")
        layout.addWidget(self.send_pdf_draft)
        self.draft_table = _make_table(self.DRAFT_HEADERS, multi=True)
        self.draft_table.setColumnHidden(0, True)
        self.draft_table.itemSelectionChanged.connect(self._show_draft_positions)
        self.draft_table.cellDoubleClicked.connect(lambda *_: self._edit_selected_draft())
        layout.addWidget(self.draft_table)

        layout.addWidget(QLabel("Pozycje faktury"))
        self.positions_table = _make_table(["Nazwa", "Ilość", "Jednostka", "Cena netto", "VAT %", "Netto", "Brutto", "Kategoria"])
        quantity_delegate = QuantityDelegate(self.positions_table)
        quantity_delegate.committed.connect(self._on_draft_position_changed)
        self.positions_table.setItemDelegate(quantity_delegate)
        self.positions_table.setItemDelegateForColumn(1, quantity_delegate)
        self.positions_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.SelectedClicked
        )
        self._shown_draft_id = None
        layout.addWidget(self.positions_table)
        self.vat_summary = VatSummary()
        layout.addWidget(self.vat_summary)
        inline_controls = QHBoxLayout()
        self.service_choice = QComboBox()
        self.service_choice.setEditable(True)
        self.service_choice.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.service_choice.setMinimumWidth(260)
        self.service_choice.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.service_choice.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.service_choice.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.service_quantity = QDoubleSpinBox()
        self.service_quantity.setRange(0.001, 1000000)
        self.service_quantity.setDecimals(3)
        self.service_quantity.setValue(1)
        self.btn_add_catalogue_inline = QPushButton("Dodaj usługę")
        self.btn_add_catalogue_inline.clicked.connect(self._add_inline_service)
        self.btn_add_position_inline = QPushButton("Dodaj pozycję")
        self.btn_add_position_inline.clicked.connect(self._add_inline_position)
        self.btn_remove_position_inline = QPushButton("Usuń pozycję")
        self.btn_remove_position_inline.clicked.connect(self._remove_inline_position)
        for widget in (self.service_choice, self.service_quantity, self.btn_add_catalogue_inline,
                       self.btn_add_position_inline, self.btn_remove_position_inline):
            inline_controls.addWidget(widget)
        layout.addLayout(inline_controls)
        self.positions_total = QLabel("Wybierz jedną fakturę roboczą. Zmiany pozycji są zapisywane automatycznie.")
        layout.addWidget(self.positions_total)
        self._set_inline_enabled(False)

    def _build_tab_wystawione(self):
        layout = QVBoxLayout(self.tab_wystawione)
        buttons = QHBoxLayout()
        self.btn_view = QPushButton("Podgląd faktury / PDF")
        self.btn_view.clicked.connect(self._view_selected_issued)
        self.btn_reexport = QPushButton("Eksportuj XLSX (Saldeo)")
        self.btn_reexport.clicked.connect(self._reexport_selected)
        self.btn_jpk = QPushButton("JPK_V7M (Enova)")
        self.btn_jpk.setToolTip("Ewidencja sprzedaży z numerami KSeF dla zaznaczonych faktur")
        self.btn_jpk.clicked.connect(self._export_jpk_v7m)
        self.btn_correction = QPushButton("Wystaw korektę")
        self.btn_correction.clicked.connect(self._create_correction)
        self.btn_ksef = QPushButton("Numer KSeF...")
        self.btn_ksef.clicked.connect(self._edit_ksef_number)
        self.btn_ksef_download = QPushButton("Pobierz z KSeF")
        self.btn_ksef_download.clicked.connect(self._download_from_ksef)
        self.btn_ksef_download_one = QPushButton("Pobierz fakturę z KSeF")
        self.btn_ksef_download_one.setToolTip("Pobierz lub sprawdź jedną fakturę (albo faktury jednej firmy wybranej w filtrze)")
        self.btn_ksef_download_one.clicked.connect(self._download_one_from_ksef)
        self.btn_send_ksef = QPushButton("Wyślij zaznaczone do KSeF")
        self.btn_send_ksef.clicked.connect(lambda: self._send_selected_to_ksef())
        self.btn_status_ksef = QPushButton("Status wysyłki KSeF")
        self.btn_status_ksef.clicked.connect(self._check_ksef_submission)
        self.btn_bank = QPushButton("Rozlicz ING MT940")
        self.btn_bank.clicked.connect(self._open_bank_payments)
        self.btn_cash = QPushButton("Zapłata gotówką")
        self.btn_cash.clicked.connect(self._mark_manual_payment)
        self.btn_delete_issued = QPushButton("Anuluj fakturę")
        self.btn_delete_issued.clicked.connect(self._delete_selected_issued)
        self.btn_view.setText("Podgląd faktury")
        self.btn_jpk.setText("JPK_V7M")
        self.btn_bank.setText("Rozlicz MT940")
        self.btn_cash.setText("Zapłacono")
        self.btn_email_pdf = QPushButton("Wyślij pdf e-mailem")
        self.btn_email_pdf.clicked.connect(self._email_selected_pdfs)
        self.btn_trash = QPushButton("Przenieś do kosza")
        self.btn_trash.setToolTip("Przenosi zaznaczone faktury (także wystawione i z KSeF) do kosza; można je przywrócić.")
        self.btn_trash.clicked.connect(self._trash_selected_issued)
        self.btn_trash_bin = QPushButton("Kosz…")
        self.btn_trash_bin.clicked.connect(self._open_trash)
        for button in (self.btn_trash, self.btn_trash_bin):
            button.setVisible(is_administrator(self.pracownik_id))
        for button in (self.btn_view, self.btn_jpk, self.btn_correction, self.btn_delete_issued,
                       self.btn_ksef_download, self.btn_ksef_download_one, self.btn_send_ksef, self.btn_status_ksef,
                       self.btn_bank, self.btn_cash, self.btn_email_pdf, self.btn_trash, self.btn_trash_bin):
            buttons.addWidget(button)
        for button in (self.btn_reexport, self.btn_ksef):
            button.setVisible(False)
        buttons.addStretch()
        layout.addLayout(buttons)

        self.send_pdf_after_ksef = QCheckBox("Po przyjęciu w KSeF wyślij PDF do klienta")
        layout.addWidget(self.send_pdf_after_ksef)
        payment_filters = QHBoxLayout()
        payment_column = QVBoxLayout()
        payment_column.addWidget(QLabel("Filtr płatności"))
        self.payment_filter = QComboBox()
        for label, value in (("Wszystkie", "all"), ("Zapłacone", "paid"),
                             ("Częściowo zapłacone", "partial"), ("Niezapłacone", "unpaid")):
            self.payment_filter.addItem(label, value)
        self.payment_filter.setToolTip("Niezapłacone: wszystkie faktury z pozostałym saldem, także częściowo zapłacone.")
        saved_filter = self._view_settings.value(self._payment_filter_key, "all")
        self.payment_filter.setCurrentIndex(max(0, self.payment_filter.findData(saved_filter)))
        self.payment_filter.currentIndexChanged.connect(self._payment_filter_changed)
        payment_column.addWidget(self.payment_filter)
        payment_filters.addLayout(payment_column)
        kind_column = QVBoxLayout()
        kind_column.addWidget(QLabel("Rodzaj"))
        self.invoice_kind_filter = QComboBox()
        for label, value in (("Wszystkie", "all"), ("Sprzedażowa", "sale"), ("Korekta", "korekta")):
            self.invoice_kind_filter.addItem(label, value)
        self.invoice_kind_filter.currentIndexChanged.connect(self._invoice_number_filter_changed)
        kind_column.addWidget(self.invoice_kind_filter)
        payment_filters.addLayout(kind_column)
        number_column = QVBoxLayout()
        number_column.addWidget(QLabel("Nr faktury"))
        self.invoice_number_filter = QLineEdit()
        self.invoice_number_filter.setPlaceholderText("Wpisz numer lub fragment numeru…")
        self.invoice_number_filter.setClearButtonEnabled(True)
        self.invoice_number_filter.setMinimumWidth(260)
        self.invoice_number_filter.textChanged.connect(self._invoice_number_filter_changed)
        number_column.addWidget(self.invoice_number_filter)
        payment_filters.addLayout(number_column)
        self.payment_filter_status = QLabel()
        payment_filters.addWidget(self.payment_filter_status)
        payment_filters.addStretch()
        layout.addLayout(payment_filters)
        self.issued_table = _make_table(self.ISSUED_HEADERS, multi=True)
        self.issued_table.setColumnHidden(0, True)
        self._configure_issued_columns()
        self.issued_table.cellClicked.connect(self._on_issued_cell_clicked)
        self.issued_table.cellDoubleClicked.connect(self._on_issued_double_click)
        layout.addWidget(self.issued_table)
        self.ksef_send_status = QLabel("Zaznacz faktury, aby wysłać je do KSeF. Numer zostanie pobrany automatycznie po przyjęciu.")
        self.ksef_send_status.setWordWrap(True)
        self.ksef_send_status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.ksef_send_status)

    def _configure_issued_columns(self):
        """Stałe, czytelne proporcje tabeli niezależne od długości danych."""
        header = self.issued_table.horizontalHeader()
        header.setStretchLastSection(False)
        for column in range(len(self.ISSUED_HEADERS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        ksef_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        ksef_width = QFontMetrics(ksef_font).horizontalAdvance("8522605060-20261028-9B6112C00016-4F") + 18
        widths = {
            0: 52, 1: 76, 3: 72, 4: 132, self.ISSUED_PREVIEW_COLUMN: 82,
            6: 78, 7: 78, 8: 88, 9: 88, self.ISSUED_PAID_COLUMN: 100, 11: 142,
            self.ISSUED_KSEF_COLUMN: ksef_width, 13: 100, 14: 100,
        }
        for column, width in widths.items():
            self.issued_table.setColumnWidth(column, width)
        self._size_buyer_column()

    def _build_tab_rozliczenia(self):
        layout = QVBoxLayout(self.tab_rozliczenia)
        top = QHBoxLayout()
        self.settlement_search = QLineEdit()
        self.settlement_search.setPlaceholderText("Szukaj: numer faktury, nabywca, nadawca lub tytuł przelewu")
        self.settlement_search.returnPressed.connect(self._fill_settlements)
        self.btn_settlement_search = QPushButton("Szukaj")
        self.btn_settlement_search.clicked.connect(self._fill_settlements)
        self.btn_settlement_revert = QPushButton("Wycofaj zaznaczone rozliczenie")
        self.btn_settlement_revert.clicked.connect(self._revert_selected_settlements)
        top.addWidget(self.settlement_search, 1)
        top.addWidget(self.btn_settlement_search)
        top.addWidget(self.btn_settlement_revert)
        layout.addLayout(top)
        self.settlement_table = _make_table(self.SETTLEMENT_HEADERS, multi=True)
        self.settlement_table.setColumnHidden(0, True)
        layout.addWidget(self.settlement_table)
        self.tabs.currentChanged.connect(
            lambda _i: self._fill_settlements() if self.tabs.currentWidget() is self.tab_rozliczenia else None
        )

    def _company_text_chosen(self, text):
        index = self.firma_combo.findText(text, Qt.MatchFlag.MatchFixedString)
        if index >= 0:
            self.firma_combo.setCurrentIndex(index)

    def _selected_company_id(self):
        value = self.firma_combo.currentData()
        return value if isinstance(value, int) and value > 0 else None

    def _selected_contractor_id(self):
        value = self.firma_combo.currentData()
        return -value if isinstance(value, int) and value < 0 else None

    def _fill_settlements(self):
        try:
            rows = list_settlements(self.pracownik_id, None, self.settlement_search.text())
        except PermissionError:
            self.settlement_table.setRowCount(0)
            return
        except Exception:
            self.settlement_table.setRowCount(0)
            logger.exception("Nie udało się wczytać rozliczeń")
            QMessageBox.warning(self, "Rozliczenia", "Nie udało się wczytać rozliczeń. Szczegóły w logach.")
            return
        with preserve_table_view(self.settlement_table):
            self.settlement_table.setRowCount(0)
            for item in rows:
                row = self.settlement_table.rowCount()
                self.settlement_table.insertRow(row)
                source = {'gotowka': 'Gotówka', 'kompensata': 'Rozliczenie korektą'}.get(item['sposob'], 'Przelew')
                if item['sposob'] == 'bank':
                    source = ('Ręczny + MT940' if item.get('manual_origin') else 'MT940') if item.get('transakcja_id') else 'Przelew ręczny'
                bank = item.get('correction_number') or " — ".join(filter(None, [item.get("bank_sender"), item.get("bank_title")]))
                values = [item["id"], _fmt_date(item["data_zaplaty"]), item["numer"],
                          _fmt_date(item["data_wystawienia"]), item["buyer"], f"{item['kwota']:.2f}",
                          source, bank, item.get("actor_name") or ""]
                for column, value in enumerate(values):
                    self.settlement_table.setItem(row, column, _cell(value, column == 5))

    def _revert_selected_settlements(self):
        if not self._can_manage_payments():
            return
        ids = self._selected_ids(self.settlement_table)
        if not ids:
            QMessageBox.information(self, "Rozliczenia", "Zaznacz rozliczenie do wycofania.")
            return
        if QMessageBox.question(
            self, "Wycofaj rozliczenie",
            f"Wycofać zaznaczone rozliczenia ({len(ids)})? Nowe wpłaty zostaną usunięte. "
            "Dla wpłat ręcznych powiązanych z MT940 zostanie usunięte tylko powiązanie z wyciągiem; "
            "ręczne oznaczenie zapłaty pozostanie. Przelewy wrócą na listę do uzgodnienia.",
        ) != QMessageBox.StandardButton.Yes:
            return
        errors = []
        for payment_id in ids:
            try:
                revert_settlement(payment_id, self.pracownik_id)
            except Exception as exc:
                logger.exception("Nie udało się wycofać rozliczenia %s", payment_id)
                errors.append(str(exc))
        if errors:
            QMessageBox.warning(self, "Rozliczenia", "\n".join(errors[:5]))
        self.refresh()

    def _build_tab_dodatkowe(self):
        self.extra_activities = None
        QVBoxLayout(self.tab_dodatkowe)
        self.tabs.currentChanged.connect(self._load_extra_activities)

    def _load_extra_activities(self, *_):
        if self.tabs.currentWidget() is not self.tab_dodatkowe:
            return
        if self.extra_activities is not None:
            month = self._current_month()
            if self.extra_activities.month.currentText() != month:
                self.extra_activities.month.setCurrentText(month)
            return
        from app.db.dao_companies import list_companies
        from app.db.dao_employees import list_employees
        from app.tasks.extra_activities_ui import ExtraActivitiesPage

        layout = self.tab_dodatkowe.layout()
        self.extra_activities = ExtraActivitiesPage(
            self.pracownik_id, list_companies(self.pracownik_id), list_employees(), self.tab_dodatkowe,
            month=self._current_month(),
        )
        self.extra_activities.activities_changed.connect(self.refresh)
        self.extra_activities.open_task.connect(self._open_extra_task)
        layout.addWidget(self.extra_activities)

    def _open_extra_task(self, task_id):
        from app.db.dao_companies import list_companies
        from app.db.dao_employees import list_employees
        from app.db.dao_tasks import list_tasks
        from app.tasks.tasks_ui import TaskDialog

        task = next((row for row in list_tasks(viewer_id=self.pracownik_id) if row["id"] == task_id), None)
        if task is None:
            QMessageBox.warning(self, "Zadanie", "Powiązane zadanie nie jest dostępne.")
            return
        TaskDialog(self, self.pracownik_id, list_companies(self.pracownik_id), list_employees(), task).exec()
        self.extra_activities.refresh()

    def _download_from_ksef(self):
        if self._ksef_send_worker is not None:
            QMessageBox.information(self, "KSeF", "Poczekaj na zakończenie obsługi wysyłki.")
            return
        if not ksef_configured():
            QMessageBox.warning(self, "KSeF", "Skonfiguruj NIP oraz certyfikat z kluczem prywatnym w ustawieniach. Token nie jest używany.")
            return
        month = self._current_period()
        firma_id = self._selected_company_id()
        buyer_nip, company_label = None, "wszystkich firm"
        if firma_id is not None:
            conn = get_conn()
            try:
                row = conn.execute("SELECT nip, nazwa FROM firmy WHERE id = %s", (firma_id,)).fetchone()
            finally:
                conn.close()
            nip = "".join(ch for ch in str((row or {}).get("nip") or "") if ch.isdigit()) if row else ""
            if len(nip) != 10:
                QMessageBox.warning(self, "KSeF", "Wybrana firma nie ma poprawnego NIP-u.")
                return
            buyer_nip, company_label = nip, f"firmy {row['nazwa']} (NIP {nip})"
        answer = QMessageBox.question(
            self, "Pobierz faktury sprzedaży z KSeF",
            f"Pobrać faktury sprzedaży {company_label} za {month}?\n\n"
            + ("Cały rok zostanie pobrany osobno miesiąc po miesiącu (limit zakresu KSeF).\n\n"
               if len(month) == 4 else "") +
            "Firma zostanie dopasowana po NIP-ie. Dane istniejącej faktury o tym samym numerze "
            "zostaną zastąpione treścią z KSeF. Historia płatności pozostanie bez zmian.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_ksef_download(month, **({"buyer_nip": buyer_nip} if buyer_nip else {}))

    def _download_one_from_ksef(self):
        """Pobiera/sprawdza w KSeF jedną fakturę lub wszystkie faktury jednej firmy z bieżącego miesiąca."""
        if self._ksef_send_worker is not None or self._sync_running():
            QMessageBox.information(self, "KSeF", "Poczekaj na zakończenie bieżącej operacji KSeF.")
            return
        if not ksef_configured():
            QMessageBox.warning(self, "KSeF", "Skonfiguruj NIP oraz certyfikat z kluczem prywatnym w ustawieniach. Token nie jest używany.")
            return
        firma_id = self._selected_company_id()
        month = self._current_period()
        if firma_id is None:
            number, ok = QInputDialog.getText(
                self, "Pobierz fakturę z KSeF",
                f"Wszystkie firmy, miesiąc {month}.\n\nNumer faktury lub numer KSeF (puste = faktury wszystkich firm):",
            )
            if ok:
                self._start_ksef_download(month, invoice_number=number.strip() or None, force=True)
            return
        conn = get_conn()
        try:
            row = conn.execute("SELECT nip, nazwa FROM firmy WHERE id = %s", (firma_id,)).fetchone()
        finally:
            conn.close()
        nip = "".join(ch for ch in str((row or {}).get("nip") or "") if ch.isdigit()) if row else ""
        if len(nip) != 10:
            QMessageBox.warning(self, "KSeF", "Wybrana firma nie ma poprawnego NIP-u.")
            return
        number, ok = QInputDialog.getText(
            self, "Pobierz fakturę z KSeF",
            f"Firma: {row['nazwa']} (NIP {nip}), miesiąc {month}.\n\n"
            "Numer faktury lub numer KSeF (puste = wszystkie faktury tej firmy):",
        )
        if not ok:
            return
        self._start_ksef_download(month, buyer_nip=nip, invoice_number=number.strip() or None, force=True)

    def _start_ksef_download(self, month, **filters):
        if self._sync_running():
            QMessageBox.information(self, "KSeF", "Synchronizacja KSeF już trwa.")
            return
        self.btn_ksef_download.setEnabled(False)
        self.btn_ksef_download_one.setEnabled(False)
        self._ksef_progress = QProgressDialog("Pobieranie listy faktur sprzedaży z KSeF…", "", 0, 0, self)
        self._ksef_progress.setWindowTitle("Synchronizacja KSeF")
        self._ksef_progress.setWindowModality(Qt.WindowModality.NonModal)
        self._ksef_progress.setCancelButton(None)
        self._ksef_progress.setAutoClose(False)
        self._ksef_progress.setMinimumDuration(0)
        self._ksef_progress.show()
        self._ksef_thread = QThread(self)
        self._ksef_worker = KsefSyncWorker(month, **filters)
        self._ksef_worker.moveToThread(self._ksef_thread)
        self._ksef_thread.started.connect(self._ksef_worker.run)
        self._ksef_worker.progress.connect(self._update_ksef_progress)
        self._ksef_worker.finished.connect(lambda result: self._ksef_sync_finished(month, result))
        self._ksef_worker.failed.connect(self._ksef_sync_failed)
        self._ksef_worker.finished.connect(self._ksef_thread.quit)
        self._ksef_worker.failed.connect(self._ksef_thread.quit)
        self._ksef_worker.finished.connect(self._ksef_worker.deleteLater)
        self._ksef_worker.failed.connect(self._ksef_worker.deleteLater)
        self._ksef_thread.finished.connect(self._cleanup_ksef_sync)
        self._ksef_thread.finished.connect(self._ksef_thread.deleteLater)
        self._ksef_thread.start()

    def _update_ksef_progress(self, done, total, text):
        if not self._ksef_progress:
            return
        self._ksef_progress.setRange(0, max(total, 0))
        self._ksef_progress.setValue(min(done, total) if total else 0)
        self._ksef_progress.setLabelText(text)

    def _sync_running(self):
        thread = self._ksef_thread
        if thread is None:
            return False
        try:
            if thread.isRunning():
                return True
        except RuntimeError:
            pass
        self._cleanup_ksef_sync()
        return False

    def _ksef_sync_failed(self, message, expected):
        self._cleanup_ksef_sync()
        if expected:
            QMessageBox.critical(self, "KSeF", message)
        else:
            QMessageBox.critical(self, "KSeF", "Nie udało się pobrać faktur z KSeF. Szczegóły w logach.")

    def _cleanup_ksef_sync(self):
        if self._ksef_progress:
            self._ksef_progress.close()
            self._ksef_progress.deleteLater()
        self._ksef_progress = None
        self._ksef_worker = None
        self._ksef_thread = None
        self.btn_ksef_download.setEnabled(True)
        self.btn_ksef_download_one.setEnabled(True)

    def _ksef_sync_finished(self, month, result):
        self._cleanup_ksef_sync()
        self.refresh()
        message = (f"Miesiąc {month}\nZnaleziono w KSeF: {result.get('znalezione', len(result.get('dopasowane', [])))}"
                   f"\nJuż zapisane lokalnie: {len(result.get('pominiete', []))}"
                   f"\nUtworzono: {len(result.get('utworzone', []))}"
                   f"\nZaktualizowano z KSeF: {len(result.get('zaktualizowane', []))}"
                   f"\nNiedopasowano: {len(result['niedopasowane'])}")
        if result["niedopasowane"]:
            message += "\n\nNiedopasowane faktury można przypisać ręcznie w następnym oknie."
        QMessageBox.information(self, "KSeF", message)
        details = result.get("niedopasowane_szczegoly") or []
        if details:
            KsefUnmatchedDialog(details, self.pracownik_id, self).exec()

    def _selected_single_issued(self):
        ids = self._selected_ids(self.issued_table)
        if len(ids) != 1:
            QMessageBox.information(self, "Faktura", "Zaznacz dokładnie jedną fakturę wystawioną.")
            return None
        invoice = get_invoice_by_id(ids[0])
        if not invoice or not can_access_invoice(self.pracownik_id, invoice.get("firma_id")):
            QMessageBox.warning(self, "Faktura", "Brak dostępu do wskazanej faktury.")
            return None
        if not invoice.get("eksportowana"):
            QMessageBox.warning(self, "Faktura", "Wybrany dokument nie jest już fakturą wystawioną. Odśwież listę.")
            return None
        return invoice

    def _can_manage_payments(self):
        if is_administrator(self.pracownik_id) or can(self.pracownik_id, "can_view_finances"):
            return True
        QMessageBox.warning(self, "Brak uprawnień", "Nie masz uprawnień do rozliczania płatności.")
        return False

    def _open_bank_payments(self):
        if not self._can_manage_payments():
            return
        try:
            dialog = BankPaymentReview(self.pracownik_id, self)
            dialog.exec()
        except Exception as exc:
            logger.exception("Nie udało się otworzyć uzgadniania banku")
            QMessageBox.warning(self, "ING MT940", str(exc))
        self.refresh()

    def _mark_manual_payment(self):
        if not self._can_manage_payments():
            return
        invoice = self._selected_single_issued()
        if invoice and record_payment_in_dialog(invoice, self.pracownik_id, self):
            QMessageBox.information(self, "Zapłacono", "Zapisano wpłatę do faktury.")
            self.refresh()

    def _mark_cash_payment(self):
        if not self._can_manage_payments():
            return
        invoice = self._selected_single_issued()
        if invoice and record_cash_in_dialog(invoice, self.pracownik_id, self):
            QMessageBox.information(self, "Gotówka", "Zapisano zapłatę gotówką.")
            self.refresh()

    def _send_selected_to_ksef(self, *, drafts=False):
        if not self._can_export():
            return
        if self._ksef_send_worker is not None or self._sync_running():
            QMessageBox.information(self, "KSeF", "Obsługa wysyłki KSeF już trwa.")
            return
        table = self.draft_table if drafts else self.issued_table
        ids = self._selected_ids(table)
        if not ids:
            QMessageBox.information(self, "KSeF", "Zaznacz faktury do wysłania.")
            return
        if not ksef_configured():
            QMessageBox.warning(self, "KSeF", "Wymagany certyfikat i klucz prywatny KSeF oraz NIP w ustawieniach.")
            return
        invoices = [get_invoice_by_id(invoice_id) for invoice_id in ids]
        if any(not invoice or not can_access_invoice(self.pracownik_id, invoice.get("firma_id")) for invoice in invoices):
            QMessageBox.warning(self, "KSeF", "Brak dostępu do jednej z wybranych faktur.")
            return
        email_plan = None
        send_pdf = self.send_pdf_draft.isChecked() if drafts else self.send_pdf_after_ksef.isChecked()
        if send_pdf:
            try:
                email_plan = review_delivery(self, ids, self.pracownik_id)
            except (ValueError, PermissionError, KsefError) as exc:
                QMessageBox.warning(self, "Wysyłka PDF", str(exc))
                return
            if email_plan is None:
                return
        from app.db.dao_app_settings import get_setting
        environment = get_setting("ksef_srodowisko") or "prod"
        label = {"prod": "PRODUKCYJNE", "test": "testowe", "demo": "demo"}.get(environment, environment)
        numbers = "\n".join(str(invoice.get("numer") or "(numer nadany automatycznie)") for invoice in invoices[:20])
        if len(invoices) > 20:
            numbers += f"\n… i {len(invoices)-20} kolejnych"
        if QMessageBox.question(
            self, "Wyślij faktury do KSeF",
            f"Wysłać {len(ids)} faktur do środowiska {label} KSeF?\n{numbers}\n\n"
            + ("Faktury robocze zostaną wystawione i zablokowane do edycji.\n" if drafts else "")
            + "Numer KSeF i przyjęty XML zostaną pobrane automatycznie.\nWysyłki nie można cofnąć.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        if email_plan is not None:
            try:
                queue_deliveries(email_plan[0], self.pracownik_id, email_plan[1])
            except (ValueError, PermissionError, KsefError) as exc:
                QMessageBox.warning(self, "Wysyłka PDF", str(exc))
                return
        for invoice_id in ids:
            self._ksef_poll_counts[invoice_id] = 0
        self._start_ksef_batch(ids, send=True)

    def _email_selected_pdfs(self):
        if not self._can_export() or self._ksef_send_worker is not None or self._sync_running():
            return
        ids = self._selected_ids(self.issued_table)
        if not ids:
            QMessageBox.information(self, "Wysyłka PDF", "Zaznacz faktury przyjęte w KSeF.")
            return
        invoices = [get_invoice_by_id(invoice_id) for invoice_id in ids]
        if any(not invoice or not invoice.get("ksef_numer") for invoice in invoices):
            QMessageBox.warning(self, "Wysyłka PDF", "Najpierw wyślij faktury do KSeF i poczekaj na nadanie numerów.")
            return
        try:
            plan = review_delivery(self, ids, self.pracownik_id)
            if plan is None:
                return
            queue_deliveries(plan[0], self.pracownik_id, plan[1])
        except (ValueError, PermissionError, KsefError) as exc:
            QMessageBox.warning(self, "Wysyłka PDF", str(exc))
            return
        for invoice_id in ids:
            self._ksef_poll_counts[invoice_id] = 0
        self._start_ksef_batch(ids, send=False)

    def _start_ksef_batch(self, ids, *, send=False):
        if self._ksef_send_worker is not None:
            return
        self._ksef_poll_timer.stop()
        if self._sync_running():
            self._ksef_poll_timer.start()
            return
        self.btn_email_pdf.setEnabled(False)
        self.btn_ksef_download.setEnabled(False)
        self.btn_send_ksef.setEnabled(False)
        self.btn_issue_ksef.setEnabled(False)
        self.btn_status_ksef.setEnabled(False)
        self.ksef_send_status.setText("Wysyłanie i sprawdzanie faktur w KSeF…" if send else "Sprawdzanie przyjęcia i pobieranie dokumentów z KSeF…")
        worker = KsefSendWorker(list(ids), self.pracownik_id, send)
        self._ksef_send_worker = worker
        worker.signals.progress.connect(self._ksef_batch_progress)
        worker.signals.finished.connect(self._ksef_batch_finished)
        QThreadPool.globalInstance().start(worker)

    def _ksef_batch_progress(self, done, total, text):
        self.ksef_send_status.setText(f"KSeF: {done}/{total} · {text}")

    def _ksef_batch_finished(self, result):
        self._ksef_poll_timer.stop()
        self._ksef_send_worker = None
        self.btn_email_pdf.setEnabled(True)
        self.btn_ksef_download.setEnabled(True)
        self.btn_send_ksef.setEnabled(True)
        self.btn_issue_ksef.setEnabled(True)
        self.btn_status_ksef.setEnabled(True)
        pending = set(result["pending"])
        completed = {entry["id"] for entry in result["accepted"] + result["rejected"]}
        self._ksef_pending.difference_update(completed | pending | {entry["id"] for entry in result["errors"]})
        self._ksef_pending.update(pending)
        for invoice_id in pending:
            self._ksef_poll_counts[invoice_id] = self._ksef_poll_counts.get(invoice_id, 0) + 1
        accepted_numbers = [str(entry["number"]) for entry in result["accepted"]]
        accepted = ", ".join(accepted_numbers[:5]) + (f" i {len(accepted_numbers) - 5} innych" if len(accepted_numbers) > 5 else "")
        message = f"Przyjęte i pobrane: {len(result['accepted'])}. Oczekujące: {len(self._ksef_pending)}. Odrzucone: {len(result['rejected'])}."
        emailed = sum(bool(entry.get("delivery")) and entry["delivery"].get("status") == "sent" for entry in result["accepted"])
        if emailed:
            message += f" PDF wysłane e-mailem: {emailed}."
        if accepted:
            message += "\nNumery KSeF: " + accepted
        issues = result["rejected"] + result["errors"]
        if issues:
            message += "\n" + "\n".join(f"Faktura #{entry['id']}: {entry['description']}" for entry in issues[:10])
        self.ksef_send_status.setText(message)
        self.refresh()
        if any(self._ksef_poll_counts.get(invoice_id, 0) < 60 for invoice_id in self._ksef_pending):
            self._ksef_poll_timer.start()
            self.ksef_send_status.setText(message + "\nPonowienie oczekujących operacji za minutę.")
        elif self._ksef_pending:
            self.ksef_send_status.setText(message + "\nAutomatyczne sprawdzanie zakończone. Zaznacz dokumenty i użyj Status wysyłki KSeF, aby wznowić.")

    def _poll_pending_ksef(self):
        ids = [invoice_id for invoice_id in self._ksef_pending if self._ksef_poll_counts.get(invoice_id, 0) < 60]
        if ids and self._ksef_send_worker is None:
            self._start_ksef_batch(ids, send=False)

    def _check_ksef_submission(self):
        if not self._can_export() or self._ksef_send_worker is not None or self._sync_running():
            return
        ids = self._selected_ids(self.issued_table)
        if not ids:
            QMessageBox.information(self, "KSeF", "Zaznacz faktury do sprawdzenia.")
            return
        for invoice_id in ids:
            self._ksef_poll_counts[invoice_id] = 0
        self._start_ksef_batch(ids, send=False)

    # ---------------- filtry ----------------
    def _invoice_number_filter_changed(self):
        if self._issued_rows is not None:
            self._fill_issued(self._issued_rows, payments=self._issued_payments)

    def _payment_filter_changed(self):
        self._view_settings.setValue(self._payment_filter_key, self.payment_filter.currentData())
        self._view_settings.sync()
        self._invoice_number_filter_changed()

    def _current_month(self):
        return self.month_button.monthText()

    def _current_period(self):
        return self.month_button.currentText()

    def _invoice_month_changed(self, month):
        self._load_extra_activities()
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
        # Never destroy a live inline editor while a background result arrives.
        if self.positions_table.state() == QAbstractItemView.State.EditingState:
            if not self._refresh_later.isActive():
                self._refresh_later.start()
            return
        self._refresh_later.stop()
        firma_id = self._selected_company_id()
        kontrahent_id = self._selected_contractor_id()

        miesiac = self._current_period()

        started = perf_counter()
        invoices = list_invoices(self.pracownik_id, None, miesiac, firma_id, kontrahent_id)
        fetched = perf_counter()
        self._fill_drafts([inv for inv in invoices if not inv.get("eksportowana")])
        self._fill_issued([inv for inv in invoices if inv.get("eksportowana")])
        if self.tabs.currentWidget() is self.tab_rozliczenia:
            self._fill_settlements()
        logger.info("Invoice refresh: rows=%d query=%.3fs tables_and_payments=%.3fs",
                    len(invoices), fetched - started, perf_counter() - fetched)

    def _fill_drafts(self, invoices):
        if self._draft_rows == invoices:
            return
        shown = getattr(self, "_shown_draft_id", None)
        before = next((row for row in self._draft_rows or [] if row["id"] == shown), None)
        after = next((row for row in invoices if row["id"] == shown), None)
        with preserve_table_view(self.draft_table):
            self.draft_table.setRowCount(0)
            self.draft_table.setRowCount(len(invoices))
            for row, inv in enumerate(invoices):
                values = [inv["id"], "ANULOWANA" if inv.get("cancelled_at") else _kind_label(inv), inv.get("nazwa_skrocona") or inv["firma_nazwa"], inv["miesiac"], inv["numer"],
                          f"{inv['kwota_netto'] or 0:.2f}", f"{inv['kwota_brutto'] or 0:.2f}",
                          _fmt_date(inv["data_wystawienia"]), _fmt_date(inv["termin_platnosci"])]
                for column, value in enumerate(values):
                    self.draft_table.setItem(row, column, _cell(value, column in (5, 6)))
        self._draft_rows = deepcopy(invoices)
        if before != after or (shown is not None and self._selected_ids(self.draft_table) != [shown]):
            self._show_draft_positions()

    def _fill_issued(self, invoices, *, payments=None):
        try:
            if payments is None:
                payments = payments_for_invoices([inv["id"] for inv in invoices])
        except Exception:
            logger.exception("Nie udało się wczytać dat zapłaty")
            self.payment_filter_status.setText("Nie udało się odczytać płatności. Odśwież listę.")
            with preserve_table_view(self.issued_table):
                self.issued_table.setRowCount(0)
            self._issued_rows = None
            return
        self.payment_filter_status.clear()
        payment_filter = self.payment_filter.currentData()
        number_filter = self.invoice_number_filter.text().strip().casefold()
        kind_filter = self.invoice_kind_filter.currentData()
        changed = (self._issued_rows != invoices or self._issued_payments != payments
                   or self._issued_payment_filter != payment_filter
                   or self._issued_number_filter != number_filter
                   or self._issued_kind_filter != kind_filter)
        if changed:
            with preserve_table_view(self.issued_table):
                self.issued_table.setRowCount(0)
                self.issued_table.setRowCount(len(invoices))
                row = 0
                for inv in invoices:
                    if number_filter not in str(inv.get("numer") or "").casefold():
                        continue
                    correction = inv.get("rodzaj") == "korekta"
                    if (kind_filter == "korekta" and not correction) or (kind_filter == "sale" and correction):
                        continue
                    paid = payments.get(inv["id"], [])
                    paid_text = ", ".join(dict.fromkeys(_fmt_date(p["data_zaplaty"]) for p in paid))
                    paid_sum = sum((Decimal(str(p["kwota"] or 0)) for p in paid), Decimal("0"))
                    refund = inv.get('rodzaj') == 'korekta' and Decimal(str(inv['kwota_brutto'] or 0)) < 0
                    total = abs(Decimal(str(inv['kwota_brutto'] or 0))) if refund else Decimal(str(inv['kwota_brutto'] or 0))
                    remaining = max(total - paid_sum, Decimal("0"))
                    state = "paid" if remaining == 0 else "partial" if paid_sum > 0 else "unpaid"
                    selected_state = self.payment_filter.currentData()
                    matching_states = {"all", state}
                    if remaining > 0:
                        matching_states.add("unpaid")
                    if selected_state not in matching_states:
                        continue
                    values = [inv["id"], "ANULOWANA" if inv.get("cancelled_at") else _kind_label(inv), inv.get("nazwa_skrocona") or inv["firma_nazwa"], inv["miesiac"], inv["numer"], None,
                              f"{inv['kwota_netto'] or 0:.2f}", f"{inv['kwota_brutto'] or 0:.2f}",
                              f"{paid_sum:.2f}", f"{-remaining if refund else remaining:.2f}", paid_text,
                              inv["data_eksportu"], inv["ksef_numer"], {"wysylanie":"Wysyłanie", "oczekuje":"Przetwarzanie", "do_sprawdzenia":"Sprawdzanie", "przyjeta":"Przyjęta", "odrzucona":"Odrzucona", "blad_przed_wysylka":"Błąd przed wysyłką"}.get(inv.get("ksef_status") or ("przyjeta" if inv.get("ksef_numer") else None), "—"), {"queued":"W kolejce", "sent":"Wysłano PDF", "failed_before_send":"Błąd PDF/SMTP", "sending":"Do sprawdzenia", "uncertain":"Do sprawdzenia"}.get(inv.get("invoice_email_status"), "—")]
                    for column, value in enumerate(values):
                        self.issued_table.setItem(row, column, _cell(value, column in (6, 7, 8, 9)))
                    if paid:
                        lines = "\n".join(f"{_fmt_date(p['data_zaplaty'])} — {p['kwota']:.2f} zł"
                                          + (' (rozliczenie korektą)' if p.get('sposob') == 'kompensata' else '') for p in paid)
                        self.issued_table.item(row, self.ISSUED_NUMBER_COLUMN).setToolTip("Rozliczono:\n" + lines)
                        self.issued_table.item(row, self.ISSUED_PAID_COLUMN).setToolTip(
                            f"FV {inv['numer']}\nwyst. {_fmt_date(inv.get('data_wystawienia'))}"
                        )
                    else:
                        self.issued_table.item(row, self.ISSUED_NUMBER_COLUMN).setToolTip("Brak zapłaty")
                    preview = QPushButton("Podgląd", self.issued_table)
                    preview.setToolTip("Wyświetl fakturę i umożliw zapis do PDF")
                    preview.clicked.connect(lambda _checked=False, invoice_id=inv["id"]: self._open_invoice_preview(invoice_id))
                    self.issued_table.setCellWidget(row, self.ISSUED_PREVIEW_COLUMN, preview)
                    ksef_item = self.issued_table.item(row, self.ISSUED_KSEF_COLUMN)
                    if inv["ksef_numer"]:
                        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
                        font.setUnderline(True)
                        ksef_item.setFont(font)
                        ksef_item.setForeground(KSEF_LINK_COLOR)
                        ksef_item.setToolTip(
                            "Kliknij, aby otworzyć urzędową weryfikację KSeF "
                            "(numer zostanie też skopiowany do schowka)"
                        )
                    row += 1
                self.issued_table.setRowCount(row)

            self._issued_rows = deepcopy(invoices)
            self._issued_payments = deepcopy(payments)
            self._issued_payment_filter = payment_filter
            self._issued_number_filter = number_filter
            self._issued_kind_filter = kind_filter

        # Rendering a list never schedules KSeF traffic. Only an explicit send
        # or status request can start the workflow and its one-minute retries.

    @staticmethod
    def _selected_ids(table):
        rows = sorted({index.row() for index in table.selectedIndexes()})
        return [int(table.item(row, 0).text()) for row in rows]

    # ---------------- faktury robocze ----------------
    def _set_inline_enabled(self, enabled):
        for widget in (self.positions_table, self.service_choice, self.service_quantity,
                       self.btn_add_catalogue_inline, self.btn_add_position_inline, self.btn_remove_position_inline):
            widget.setEnabled(enabled)

    def _reload_catalogue(self):
        text = self.service_choice.currentText()
        service = self.service_choice.currentData()
        selected_id = service.get("id") if isinstance(service, dict) else None
        typed = text != self.service_choice.itemText(self.service_choice.currentIndex())
        blocked = self.service_choice.blockSignals(True)
        try:
            self.service_choice.clear()
            self.service_choice.addItem("Wybierz usługę z katalogu…", None)
            try:
                for item in get_extra_services():
                    item = dict(item)
                    self.service_choice.addItem(f'{item["nazwa"]} · {item["cena_netto"]:.2f} zł netto', item)
                    if item.get("id") == selected_id:
                        self.service_choice.setCurrentIndex(self.service_choice.count() - 1)
            except Exception:
                logger.exception("Nie udało się pobrać katalogu usług")
            if typed:
                self.service_choice.setEditText(text)
        finally:
            self.service_choice.blockSignals(blocked)

    def _edit_draft_header(self):
        ids = self._selected_ids(self.draft_table)
        if len(ids) != 1:
            QMessageBox.information(self, "Faktura", "Zaznacz jedną fakturę do edycji.")
            return
        self._open_editor(ids[0])

    def _show_draft_positions(self):
        self._shown_draft_id = None
        self._shown_draft_revision = None
        self._set_inline_enabled(False)
        self.positions_table.blockSignals(True)
        self.positions_table.setRowCount(0)
        self.vat_summary.set_positions([])
        self.positions_total.setText("Wybierz jedną fakturę roboczą. Zmiany pozycji są zapisywane automatycznie.")
        try:
            ids = self._selected_ids(self.draft_table)
            if len(ids) != 1:
                return
            invoice = get_invoice_by_id(ids[0])
            if not invoice or invoice.get("eksportowana") or not can_access_invoice(self.pracownik_id, invoice.get("firma_id")):
                return
            self._shown_draft_id = ids[0]
            self._shown_draft_revision = invoice.get('revision')
            positions = [normalize_position(p) for p in get_invoice_positions(ids[0])]
            for position in positions:
                row = self.positions_table.rowCount()
                self.positions_table.insertRow(row)
                values = [position["nazwa"], f'{position["ilosc"]:g}', position.get("jednostka", ""),
                          f'{position["cena_netto"]:.2f}', position.get("vat_code") or f'{position["vat"]:g}',
                          f'{position["netto"]:.2f}', f'{position["brutto"]:.2f}', position.get("kategoria", "")]
                for column, value in enumerate(values):
                    item = _cell(value, column in (1, 3, 4, 5, 6))
                    if column not in (5, 6):
                        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                    self.positions_table.setItem(row, column, item)
            self.vat_summary.set_positions(positions)
            netto, brutto = totals(positions)
            self.positions_total.setText(f"Razem netto: {netto:.2f} zł · brutto: {brutto:.2f} zł · Zmiany zapisywane automatycznie")
            self._set_inline_enabled(True)
            self._reload_catalogue()
        finally:
            self.positions_table.blockSignals(False)

    def _save_inline_positions(self, positions, *, number=None, header=None):
        invoice_id = self._shown_draft_id
        if invoice_id is None or self._selected_ids(self.draft_table) != [invoice_id]:
            return False
        invoice = get_invoice_by_id(invoice_id)
        if not invoice or invoice.get("eksportowana") or not can_access_invoice(self.pracownik_id, invoice.get("firma_id")):
            self._show_draft_positions()
            return False
        if not positions or any(not str(p.get("nazwa") or "").strip() for p in positions):
            raise ValueError("Faktura wymaga co najmniej jednej nazwanej pozycji.")
        header = header or {}
        update_invoice_draft(invoice_id, positions, numer=number if number is not None else invoice.get("numer"),
                             data_wystawienia=header.get("data_wystawienia", invoice.get("data_wystawienia")),
                             data_sprzedazy=header.get("data_sprzedazy", invoice.get("data_sprzedazy")),
                             termin_platnosci=header.get("termin_platnosci", invoice.get("termin_platnosci")),
                             przyczyna_korekty=header.get("przyczyna_korekty", invoice.get("przyczyna_korekty")),
                             podstawa_zwolnienia=header.get("podstawa_zwolnienia", invoice.get("podstawa_zwolnienia")),
                             expected_revision=self._shown_draft_revision)
        if self._shown_draft_revision is not None:
            self._shown_draft_revision += 1
        self.refresh()
        self._select_draft(invoice_id)
        return True

    def _on_draft_position_changed(self, index):
        if index.column() not in (0, 1, 2, 3, 4, 7) or self._shown_draft_id is None:
            return
        try:
            positions = get_invoice_positions(self._shown_draft_id)
            if index.row() >= len(positions):
                raise ValueError("Nie znaleziono edytowanej pozycji.")
            position = normalize_position(positions[index.row()])
            text = self.positions_table.item(index.row(), index.column()).text().strip()
            field = {0: "nazwa", 1: "ilosc", 2: "jednostka", 3: "cena_netto", 4: "vat", 7: "kategoria"}[index.column()]
            value = text
            if index.column() == 4:
                position.pop("vat_code", None)
            if index.column() in (1, 3, 4) and not (index.column() == 4 and text.lower() in ("zw", "np", "np i", "np ii", "oo", "0 kr", "0 wdt", "0 ex")):
                value = float(text.replace(",", ".").rstrip("%"))
                if not math.isfinite(value):
                    raise ValueError("Podaj skończoną liczbę.")
                if index.column() == 4 and not 0 <= value <= 100:
                    raise ValueError("Stawka VAT musi być w zakresie 0–100%.")
            position[field] = value
            positions[index.row()] = normalize_position(position)
            self._save_inline_positions(positions)
        except InvoiceConflictError as exc:
            QMessageBox.warning(self, 'Konflikt zmian', str(exc))
        except (TypeError, ValueError, InvoiceLockedError) as exc:
            QMessageBox.warning(self, "Pozycja faktury", f"Nie udało się zapisać pozycji: {exc}")
            self._show_draft_positions()

    def _append_inline_position(self, position):
        if self._shown_draft_id is None:
            return
        try:
            positions = get_invoice_positions(self._shown_draft_id)
            positions.append(normalize_position(position))
            if self._save_inline_positions(positions):
                row = self.positions_table.rowCount() - 1
                self.positions_table.setCurrentCell(row, 0)
                self.positions_table.setFocus()
        except (TypeError, ValueError, InvoiceLockedError) as exc:
            QMessageBox.warning(self, "Pozycja faktury", str(exc))

    def _add_inline_service(self):
        service = self.service_choice.currentData()
        if not service or self.service_choice.currentText() != self.service_choice.itemText(self.service_choice.currentIndex()):
            QMessageBox.information(self, "Katalog usług", "Wybierz usługę z listy.")
            return
        self._append_inline_position({"nazwa": service["nazwa"], "ilosc": self.service_quantity.value(),
                                      "jednostka": "szt.", "cena_netto": service["cena_netto"],
                                      "vat": service["stawka_vat"], "kategoria": service.get("kategoria", ""), "generated": False})

    def _add_inline_position(self):
        self._append_inline_position({"nazwa": "Nowa pozycja", "ilosc": 1, "jednostka": "szt.", "cena_netto": 0, "vat": 23, "generated": False})

    def _remove_inline_position(self):
        row = self.positions_table.currentRow()
        if row < 0 or self._shown_draft_id is None:
            return
        if QMessageBox.question(self, "Pozycja faktury", "Usunąć wybraną pozycję?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            positions = get_invoice_positions(self._shown_draft_id)
            if row >= len(positions):
                raise ValueError("Nie znaleziono wybranej pozycji.")
            del positions[row]
            self._save_inline_positions(positions)
        except (TypeError, ValueError, InvoiceLockedError) as exc:
            QMessageBox.warning(self, "Pozycja faktury", str(exc))

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
        self._show_draft_positions()
        if self.positions_table.rowCount():
            self.positions_table.setCurrentCell(0, 0)
            self.positions_table.setFocus()
            self.positions_table.editItem(self.positions_table.item(0, 0))

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
            self, "Usuwanie",
            f"Usunąć zaznaczone faktury robocze ({len(ids)})?\n"
            "Usługi dodatkowe zostaną zachowane i uwzględnione przy ponownym generowaniu.",
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

    def _trash_selected_issued(self):
        ids = self._selected_ids(self.issued_table)
        if not ids:
            QMessageBox.information(self, "Kosz", "Zaznacz faktury do przeniesienia do kosza.")
            return
        if QMessageBox.question(
            self, "Kosz", f"Przenieść zaznaczone faktury ({len(ids)}) do kosza?\n"
            "Faktury znikną z list i raportów, ale można je przywrócić przyciskiem „Kosz…”.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        ) != QMessageBox.StandardButton.Yes:
            return
        from app.db.dao_invoices import trash_invoice
        for invoice_id in ids:
            try:
                trash_invoice(invoice_id, self.pracownik_id)
            except (ValueError, PermissionError) as exc:
                QMessageBox.warning(self, "Kosz", str(exc))
        self.refresh()

    def _open_trash(self):
        from app.invoices.invoice_trash_dialog import InvoiceTrashDialog
        InvoiceTrashDialog(self.pracownik_id, self).exec()
        self.refresh()

    def _delete_selected_issued(self):
        ids = self._selected_ids(self.issued_table)
        if not ids:
            QMessageBox.information(self, "Faktura", "Zaznacz wystawioną fakturę do usunięcia.")
            return
        if not self._can_export():
            return
        reason, ok = QInputDialog.getText(self, "Anulowanie faktur", "Przyczyna anulowania (faktura wróci do roboczych bez numeru; dokumenty z numerem KSeF wymagają korekty):")
        if not ok or not reason.strip():
            return
        from app.db.dao_invoices import cancel_invoice
        for invoice_id in ids:
            try:
                cancel_invoice(invoice_id, self.pracownik_id, reason)
            except (ValueError, PermissionError) as exc:
                QMessageBox.warning(self, "Faktura", str(exc))
        self.refresh()

    # ---------------- eksport ----------------
    def _prepare_saldeo_correction(self, invoice_id=None):
        if not self._can_export():
            return
        if not invoice_id:
            ids = self._selected_ids(self.draft_table)
            if len(ids) != 1:
                QMessageBox.information(self, "Korekta", "Zaznacz jedną korektę roboczą.")
                return
            invoice_id = ids[0]
        invoice = get_invoice_by_id(invoice_id)
        if not invoice or not can_access_invoice(self.pracownik_id, invoice["firma_id"]):
            QMessageBox.warning(self, "Korekta", "Brak dostępu do korekty.")
            return
        if invoice.get("rodzaj") != "korekta" or invoice.get("eksportowana"):
            QMessageBox.warning(self, "Korekta", "Wybierz niewystawioną korektę faktury.")
            return
        from app.invoices.saldeo_correction_dialog import SaldeoCorrectionDialog
        try:
            SaldeoCorrectionDialog(invoice, self.pracownik_id, self).exec()
        except ValueError as exc:
            QMessageBox.warning(self, "Korekta", str(exc))
        self.refresh()

    def _can_export(self):
        if is_administrator(self.pracownik_id) or can(self.pracownik_id, "can_export_data"):
            return True
        QMessageBox.warning(self, "Brak uprawnień", "Nie masz uprawnień do eksportowania danych.")
        return False

    def _export_selected_drafts(self):
        if not self._can_export():
            return
        ids = self._selected_ids(self.draft_table)
        if not ids:
            QMessageBox.information(self, "Faktury", "Zaznacz faktury robocze do wystawienia.")
            return
        try:
            for invoice_id in ids:
                invoice = get_invoice_by_id(invoice_id)
                if not invoice or not can_access_invoice(self.pracownik_id, invoice.get("firma_id")):
                    raise PermissionError("Brak dostępu do faktury.")
            mark_exported(ids, self.pracownik_id)
        except Exception as exc:
            QMessageBox.warning(self, "Wystawianie faktur", str(exc))
            return
        self.refresh()
        self.tabs.setCurrentWidget(self.tab_wystawione)

    def _reexport_selected(self):
        self._export_ids(self._selected_ids(self.issued_table), mark=False)

    def _export_jpk_v7m(self):
        from app.db.dao_app_settings import get_setting, set_setting
        from app.invoices.jpk_v7 import JpkError, build_jpk_v7m
        if not self._can_export():
            return
        ids = self._selected_ids(self.issued_table)
        if not ids:
            QMessageBox.information(self, "JPK_V7M", "Zaznacz wystawione faktury do eksportu.")
            return
        invoices = []
        for invoice_id in ids:
            invoice = get_invoice_by_id(invoice_id)
            if invoice and can_access_invoice(self.pracownik_id, invoice["firma_id"]):
                invoices.append(dict(invoice, positions=get_invoice_positions(invoice_id)))
        office = get_setting("jpk_kod_urzedu") or ""
        office, ok = QInputDialog.getText(self, "JPK_V7M", "Kod urzędu skarbowego (4 cyfry):", text=office)
        if not ok:
            return
        email = get_setting("jpk_email") or ""
        email, ok = QInputDialog.getText(self, "JPK_V7M", "E-mail podatnika:", text=email)
        if not ok:
            return
        month = self._current_month()
        seller = {"nip": get_setting("faktury_sprzedawca_nip"), "name": get_setting("faktury_sprzedawca_nazwa"), "email": email}
        try:
            xml = build_jpk_v7m(invoices, seller, month[:4], month[5:7], office)
        except JpkError as exc:
            QMessageBox.warning(self, "JPK_V7M", str(exc))
            return
        set_setting("jpk_kod_urzedu", office.strip())
        set_setting("jpk_email", email.strip())
        path, _ = QFileDialog.getSaveFileName(self, "Zapisz JPK_V7M", f"JPK_V7M_{month}.xml", "XML (*.xml)")
        if not path:
            return
        with open(path, "wb") as handle:
            handle.write(xml)
        QMessageBox.information(self, "JPK_V7M", "Zapisano plik JPK_V7M.")

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
        try:
            if any(inv.get("cancelled_at") for inv in invoices):
                raise ValueError("Nie można eksportować anulowanych faktur.")
            validate_saldeo_invoices(invoices)
            validate_invoice_extra_services_ready([invoice["id"] for invoice in invoices])
        except SaldeoCorrectionExportError as exc:
            if len(invoices) == 1:
                self._prepare_saldeo_correction(invoices[0]["id"])
            else:
                QMessageBox.warning(self, "Korekty Saldeo", str(exc))
            return
        except SaldeoExportError as exc:
            QMessageBox.warning(self, "Eksport Saldeo", str(exc))
            return
        except ValueError as exc:
            QMessageBox.warning(self, "Czynności dodatkowe", str(exc))
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
        self._open_invoice_preview(ids[0])

    def _open_invoice_preview(self, invoice_id):
        invoice = get_invoice_by_id(int(invoice_id))
        if not invoice or not can_access_invoice(self.pracownik_id, invoice.get("firma_id")):
            QMessageBox.warning(self, "Faktura", "Nie znaleziono faktury lub brak uprawnień do podglądu.")
            return
        InvoicePreviewDialog(invoice, self).exec()

    def _on_issued_double_click(self, row, column):
        if column in (self.ISSUED_NUMBER_COLUMN, self.ISSUED_PREVIEW_COLUMN):
            self._open_invoice_preview(int(self.issued_table.item(row, 0).text()))
        elif column != self.ISSUED_KSEF_COLUMN:
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
        self.month_button.setCurrentText(invoice["miesiac"])
        self.refresh()
        self._open_editor(correction_id)

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
        if column == self.ISSUED_NUMBER_COLUMN:
            self._open_invoice_preview(int(self.issued_table.item(row, 0).text()))
            return
        if column != self.ISSUED_KSEF_COLUMN:
            return
        numer = self.issued_table.item(row, column).text().strip()
        if not numer:
            return
        QGuiApplication.clipboard().setText(numer)
        invoice = get_invoice_by_id(int(self.issued_table.item(row, 0).text()))
        QDesktopServices.openUrl(QUrl(ksef_url(numer, invoice=invoice)))
