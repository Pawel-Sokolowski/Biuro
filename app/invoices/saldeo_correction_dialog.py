"""Dane do wystawienia korekty w Saldeo i jawne potwierdzenie numeru."""
import json
import logging

from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from app.db.dao_invoices import get_invoice_by_id
from app.invoices.saldeo_correction import confirm_saldeo_correction

logger = logging.getLogger(__name__)


class SaldeoCorrectionDialog(QDialog):
    def __init__(self, invoice, actor_id, parent=None):
        super().__init__(parent)
        self.invoice = invoice
        self.actor_id = actor_id
        original = get_invoice_by_id(invoice['korygowana_faktura_id'])
        if not original:
            raise ValueError('Nie znaleziono faktury pierwotnej.')
        self.setWindowTitle('Korekta w Saldeo')
        self.resize(760, 540)
        layout = QVBoxLayout(self)
        notice = QLabel(
            'Szablon importu zwykłych faktur XLSX nie obsługuje korekt. '
            'W Saldeo otwórz fakturę pierwotną i wystaw do niej korektę. '
            'Poniższe wartości są różnicami względem faktury pierwotnej; zachowaj ich znaki. '
            'Po wystawieniu wpisz nadany numer i potwierdź ten fakt.'
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        lines = [f"Faktura pierwotna: {original.get('numer') or original['id']}",
                 f"Korekta lokalna: {invoice.get('numer') or invoice['id']}",
                 f"Data: {invoice.get('data_wystawienia') or ''}",
                 f"Przyczyna: {invoice.get('przyczyna_korekty') or ''}",
                 f"Zmiana netto: {invoice['kwota_netto']:.2f} PLN",
                 f"Zmiana brutto: {invoice['kwota_brutto']:.2f} PLN", '', 'Zmiany pozycji:']
        for p in json.loads(invoice.get('pozycje_json') or '[]'):
            lines.append(f"{p.get('nazwa') or p.get('opis') or ''} | ilość {p.get('ilosc', '')} | "
                         f"netto {p.get('netto', '')} | brutto {p.get('brutto', '')} | VAT {p.get('vat', '')}")
        self.details = QPlainTextEdit('\n'.join(lines))
        self.details.setReadOnly(True)
        layout.addWidget(self.details)
        copy = QPushButton('Kopiuj dane korekty')
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.details.toPlainText()))
        layout.addWidget(copy)
        self.number_input = QLineEdit()
        self.number_input.setPlaceholderText('Numer korekty nadany w Saldeo')
        layout.addWidget(self.number_input)
        self.confirmed = QCheckBox('Potwierdzam, że ta korekta została już wystawiona w Saldeo.')
        layout.addWidget(self.confirmed)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('Zapisz potwierdzone wystawienie')
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self):
        if not self.confirmed.isChecked():
            QMessageBox.warning(self, 'Korekta', 'Najpierw wystaw korektę w Saldeo i potwierdź ten fakt.')
            return
        try:
            confirm_saldeo_correction(self.invoice['id'], self.number_input.text(), self.actor_id)
        except (ValueError, PermissionError) as exc:
            QMessageBox.warning(self, 'Korekta', str(exc))
            return
        except Exception:
            logger.exception('Błąd potwierdzania korekty Saldeo')
            QMessageBox.critical(self, 'Korekta', 'Nie udało się zapisać potwierdzenia. Szczegóły w logach.')
            return
        self.accept()
