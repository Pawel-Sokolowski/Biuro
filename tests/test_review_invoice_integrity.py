"""Regression checks for accepted PDF values and inherited tax annotations."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from lxml import etree
from PyQt6.QtWidgets import QApplication
from app.invoices import invoice_delivery, ksef_submission
from app.invoices.ksef_sync import parse_fa3_invoice
from app.invoices.fa3_builder import build_fa3_xml
from app.invoices.vat_summary import vat_summary
from tests.test_vat_and_direct_ksef import INVOICE, POSITION, SELLER


class ReviewInvoiceIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def render_official(self, xml, invoice):
        captured = []
        def write(path, html, **kwargs):
            captured.append(html)
            Path(path).write_bytes(b'%PDF-test')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'accepted.xml'
            path.write_bytes(xml)
            with patch('app.invoices.invoice_preview.get_setting', return_value=''), patch('app.invoices.invoice_preview.get_setting_first', return_value=''), patch('app.invoices.invoice_preview._qr_data_url', return_value='data:image/png;base64,AA=='), patch('app.invoices.invoice_preview.write_invoice_pdf', side_effect=write):
                invoice_delivery.official_pdf({**invoice, 'ksef_numer':'accepted'}, path)
        return captured[0]

    def test_official_pdf_preserves_fractional_quantity_and_accepted_amount(self):
        for accepted_net in ('12.50', '12.51'):
            root = etree.fromstring(build_fa3_xml(INVOICE, [POSITION], SELLER))
            for name, value in {'P_8B':'0.125', 'P_9A':'100.0001', 'P_11':accepted_net, 'P_13_1':accepted_net, 'P_14_1':'2.88', 'P_15':str(Decimal(accepted_net)+Decimal('2.88'))}.items():
                root.xpath('//*[local-name()=$name]', name=name)[0].text = value
            xml = etree.tostring(root)
            parsed = parse_fa3_invoice(xml.decode())
            self.assertEqual(parsed['positions'][0]['ilosc'], .125)
            self.assertEqual(parsed['positions'][0]['cena_netto'], 100.0001)
            group = vat_summary(parsed['positions'], accepted_amounts=True)[0]
            self.assertEqual(group['net'], Decimal(accepted_net))
            html = self.render_official(xml, INVOICE)
            self.assertIn('0.125', html)
            self.assertIn(f'<b>{accepted_net}</b>', html)

    def test_accepted_vat_and_gross_survive_rounding_boundary_without_p_11a(self):
        root = etree.fromstring(build_fa3_xml(INVOICE, [POSITION], SELLER))
        row = root.xpath('//*[local-name()="FaWiersz"]')[0]
        for name, value in {'P_11':'0.10', 'P_12':'5', 'P_11Vat':'0.01'}.items():
            row.xpath('.//*[local-name()=$name]', name=name)[0].text = value
        self.assertFalse(row.xpath('.//*[local-name()="P_11A"]'))
        parsed = parse_fa3_invoice(etree.tostring(root).decode())
        position = parsed['positions'][0]
        self.assertEqual(position['brutto'], 0.11)
        group = vat_summary(parsed['positions'], accepted_amounts=True)[0]
        self.assertEqual(group['net'], Decimal('0.10'))
        self.assertEqual(group['vat'], Decimal('0.01'))
        self.assertEqual(group['gross'], Decimal('0.11'))

    def test_official_correction_pdf_identifies_corrected_invoice(self):
        correction = {**INVOICE, 'numer':'KOR/1', 'rodzaj':'korekta', 'kwota_netto':-100, 'kwota_brutto':-123, 'przyczyna_korekty':'Zwrot'}
        xml = build_fa3_xml(correction, [{**POSITION,'ilosc':-1}], SELLER, corrected=INVOICE)
        html = self.render_official(xml, correction)
        self.assertIn('FAKTURA KORYGUJĄCA', html)
        self.assertIn(INVOICE['numer'], html)
        self.assertIn('Zwrot', html)

    def test_cash_method_precedence_invoice_company_global(self):
        settings = {'faktury_sprzedawca_nip':SELLER['nip'], 'ksef_nip':SELLER['nip'], 'faktury_sprzedawca_kraj':'PL', 'faktury_sprzedawca_adres':SELLER['address'], 'faktury_sprzedawca_nazwa':SELLER['name']}
        cases = [('', None, 'tak', '1'), ('', 'X', 'false', '1'), ('', '0', 'true', '2'), ('nie', '1', 'true', '2'), ('true', '0', 'false', '1'), (None, None, '', '2')]
        for invoice_flag, company_flag, global_flag, expected in cases:
            with self.subTest(flags=(invoice_flag,company_flag,global_flag)):
                invoice = {**INVOICE, 'firma_id':1, 'metoda_kasowa':invoice_flag}
                conn = Mock()
                conn.execute.return_value.fetchone.return_value = None
                with patch.object(ksef_submission, '_authorized_invoice', return_value=invoice), patch.object(ksef_submission, 'get_conn', return_value=conn), patch.object(ksef_submission, 'get_setting', side_effect=lambda key:settings.get(key)), patch('app.db.dao_app_settings.get_setting_first', return_value=global_flag), patch('app.db.dao_companies.get_company', return_value={'metoda_kasowa':company_flag}), patch('app.db.dao_invoices.get_invoice_positions', return_value=[POSITION]), patch('app.invoices.invoice_preview._with_buyer_data', side_effect=lambda value:value):
                    xml = ksef_submission.generate_invoice_xml(1,1)
                self.assertEqual(etree.fromstring(xml).xpath('string(//*[local-name()="P_16"])'), expected)
