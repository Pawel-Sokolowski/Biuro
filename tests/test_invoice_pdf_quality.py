"""PDF text, clickable verification and seller/buyer information parity."""
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, mock

from PyQt6.QtWidgets import QApplication
from app.invoices.invoice_preview import invoice_html, write_invoice_pdf
from app.invoices.ksef import ksef_url

_app = QApplication.instance() or QApplication(['invoice-pdf-quality'])
SETTINGS = {'faktury_sprzedawca_nazwa': 'Biuro Łódź', 'faktury_sprzedawca_nip': '8522605060',
            'faktury_sprzedawca_regon': '123456789', 'faktury_sprzedawca_email': 'biuro@example.com',
            'faktury_sprzedawca_kraj': 'PL', 'faktury_sprzedawca_adres': 'Testowa 1'}
INVOICE = {'numer': 'FV/0001/11/2026', 'data_wystawienia': '2026-11-05',
           'seller_snapshot': {key.replace('faktury_', ''): value for key, value in SETTINGS.items()},
           'ksef_numer': '8522605060-20261105-4DCCB6C0000A-25',
           'ksef_nip_wysylki': '8522605060', 'ksef_hash_faktury': 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=',
           'nabywca_pelna': 'Nabywca Żółć', 'nabywca_nip': '1234567890',
           'nabywca_adres': 'Przykładowa 15', 'nabywca_kod': '71-450', 'nabywca_miasto': 'Szczecin',
           'nabywca_regon': '987654321', 'nabywca_kraj': 'PL', 'nabywca_email': 'faktury@example.com',
           'data_sprzedazy': '2026-11-05', 'termin_platnosci': '2026-11-12',
           'konto_bankowe': '46105015591000000981362718', 'kwota_netto': 100, 'kwota_brutto': 123}


class InvoicePdfQualityTests(TestCase):
    def render(self, invoice=None):
        with mock.patch('app.invoices.invoice_preview.get_setting', side_effect=SETTINGS.get), \
                mock.patch('app.invoices.invoice_preview.get_setting_first', return_value=''), \
                mock.patch('app.invoices.ksef.get_setting', return_value=None):
            return invoice_html(invoice or INVOICE, [{"nazwa": "Usługa księgowa – listopad", "ilosc": 1,
                                                    "cena_netto": 100, "netto": 100, "vat": 23, "brutto": 123}])

    def test_seller_has_regon_country_email_and_identical_verification_target_to_qr(self):
        rendered = self.render()
        self.assertIn('REGON: 123456789', rendered)
        self.assertIn('E-mail: biuro@example.com', rendered)
        self.assertIn('Kraj: PL', rendered)
        with mock.patch('app.invoices.ksef.get_setting', return_value=None):
            expected = ksef_url(INVOICE['ksef_numer'], INVOICE)
        self.assertIn(f'href="{expected}"', rendered)
        self.assertIn('Zweryfikuj fakturę w KSeF', rendered)

    def test_foreign_official_seller_does_not_inherit_our_regon_or_email(self):
        rendered = self.render({**INVOICE, '_official': True, 'sprzedawca_nip': '9876543210',
                                'sprzedawca_nazwa': 'Other seller'})
        self.assertNotIn('REGON: 123456789', rendered)
        self.assertNotIn('biuro@example.com', rendered)

    def test_pdf_preserves_vector_text_clickable_link_and_single_a4_page(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'invoice.pdf'
            write_invoice_pdf(path, self.render())
            raw = path.read_bytes()
            self.assertIn(b'/Subtype /Link', raw)
            self.assertIn(b'/URI', raw)
            self.assertIn(b'qr.ksef.mf.gov.pl/invoice/', raw)
            if shutil.which('pdftotext'):
                text = subprocess.check_output(['pdftotext', '-layout', str(path), '-'], text=True)
                for expected in ('Biuro Łódź', 'Nabywca Żółć', 'FV/0001/11/2026', 'Data wystawienia:', 'biuro@example.com'):
                    self.assertIn(expected, text)
            if shutil.which('pdfinfo'):
                info = subprocess.check_output(['pdfinfo', str(path)], text=True)
                self.assertRegex(info, r'Pages:\s+1\b')
            if shutil.which('pdffonts'):
                fonts = subprocess.check_output(['pdffonts', str(path)], text=True)
                lines = fonts.splitlines()[2:]
                self.assertTrue(lines)
                self.assertTrue(all(line.split()[-5] == 'yes' for line in lines))
