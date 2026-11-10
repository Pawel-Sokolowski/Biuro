"""VAT grouping, schema-valid generation, and automatic KSeF reconciliation."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import base64
from copy import deepcopy
from decimal import Decimal
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from lxml import etree as E
from PyQt6.QtCore import Qt, QItemSelectionModel
from PyQt6.QtWidgets import QApplication, QWidget, QTableWidget, QTableWidgetItem, QMessageBox, QFileDialog
from app.invoices.positions import normalize_position
from app.invoices.vat_summary import vat_summary, summary_html
from app.invoices.fa3_builder import build_fa3_xml, validate_fa3
from app.invoices.ksef_client import KsefError
from app.invoices import ksef_submission as submission
from app.invoices.faktury_ui import FakturyPage

SELLER = {'nip':'9876543210', 'name':'Biuro & Partnerzy', 'address':'Testowa 1, 00-001 Warszawa'}
INVOICE = {'id':1, 'eksportowana':1, 'numer':'FV/1/2026', 'rodzaj':'faktura',
           'data_wystawienia':'2026-11-01', 'data_sprzedazy':'2026-10-31', 'termin_platnosci':'2026-11-10',
           'nabywca_nip':'1234567890', 'nabywca_pelna':'Nabywca <Test>', 'nabywca_adres':'Inna 2',
           'nabywca_kraj':'Polska', 'kwota_netto':100, 'kwota_brutto':123}
POSITION = {'nazwa':'Usługa & pomoc', 'ilosc':1, 'cena_netto':100, 'vat':23}


class VatAndFa3Tests(unittest.TestCase):
    def test_mixed_rates_rounding_and_correction_signs(self):
        positions = [dict(POSITION), {**POSITION,'vat':8}, {**POSITION,'vat':5},
                     {**POSITION,'vat':0}, {**POSITION,'vat':'zw'},
                     {**POSITION,'ilosc':-0.5}]
        groups = {g['code']:g for g in vat_summary(positions)}
        self.assertEqual(set(groups), {'23','8','5','0','zw'})
        self.assertEqual(groups['23']['net'], Decimal('50.00'))
        self.assertEqual(groups['23']['vat'], Decimal('11.50'))
        self.assertEqual(groups['8']['gross'], Decimal('108.00'))
        self.assertEqual(normalize_position({**POSITION,'vat':'zw'})['vat_code'], 'zw')
        self.assertIn('Stawka VAT', summary_html(positions))

    def test_standard_mixed_rates_and_person_without_nip_validate_against_official_schema(self):
        positions = [POSITION, {**POSITION,'vat':8}, {**POSITION,'vat':5}, {**POSITION,'vat':0}]
        invoice = {**INVOICE,'kwota_netto':400,'kwota_brutto':436}
        for nip in ('1234567890', None):
            xml = build_fa3_xml({**invoice,'nabywca_nip':nip},positions,SELLER)
            validate_fa3(xml)
            root = E.fromstring(xml)
            self.assertEqual(root.xpath('string(//*[local-name()="P_14_1"])'), '23.00')
            self.assertEqual(root.xpath('string(//*[local-name()="P_13_6_1"])'), '100.00')
            self.assertEqual(submission.inspect_fa3_xml(xml)['buyer_name'], INVOICE['nabywca_pelna'])

    def test_advance_has_zal_marker_and_schema_valid_mixed_vat_totals(self):
        positions = [POSITION, {**POSITION, 'vat': 8}, {**POSITION, 'vat': 5}, {**POSITION, 'vat': 0}]
        for nip in ('1234567890', None):
            with self.subTest(nip=nip):
                invoice = {**INVOICE, 'rodzaj': 'zaliczkowa', 'numer': 'FZ/1/2026',
                           'kwota_netto': 400, 'kwota_brutto': 436, 'nabywca_nip': nip}
                xml = build_fa3_xml(invoice, positions, SELLER)
                validate_fa3(xml)
                self.assertEqual(submission.inspect_fa3_xml(xml)['kind'], 'ZAL')
                self.assertEqual(submission.inspect_fa3_xml(xml)['gross'], '436.00')
                with patch.object(submission, 'get_setting', return_value=SELLER['nip']):
                    self.assertEqual(submission._validate_invoice_xml(
                        invoice, submission.inspect_fa3_xml(xml)), SELLER['nip'])

    def test_correction_and_exemption_have_required_annotations(self):
        correction = {**INVOICE,'rodzaj':'korekta','kwota_netto':-100,'kwota_brutto':-123,'przyczyna_korekty':'Zwrot usługi'}
        xml = build_fa3_xml(correction,[{**POSITION,'ilosc':-1}],SELLER,corrected=INVOICE)
        self.assertEqual(submission.inspect_fa3_xml(xml)['kind'], 'KOR')
        xml = build_fa3_xml({**INVOICE,'kwota_brutto':100,'podstawa_zwolnienia':'art. 43 ustawy o VAT'},
                            [{**POSITION,'vat':'zw'}],SELLER)
        self.assertIn(b'P_19A',xml)
        with self.assertRaises(KsefError):
            build_fa3_xml({**INVOICE,'kwota_brutto':100},[{**POSITION,'vat':'zw'}],SELLER)

    def test_invalid_totals_missing_parties_and_unsupported_procedure_are_rejected(self):
        for changes in ({'kwota_brutto':124}, {'nabywca_pelna':''}, {'waluta':'EUR'}, {'procedura_marzy':True}, {'data_wystawienia':'bad'}):
            with self.subTest(changes=changes), self.assertRaises(KsefError):
                build_fa3_xml({**INVOICE,**changes},[POSITION],SELLER)
        with self.assertRaises(KsefError):
            build_fa3_xml(INVOICE,[POSITION],{**SELLER,'address':''})


class DirectKsefBatchTests(unittest.TestCase):
    def setUp(self):
        self.state = {**INVOICE, 'ksef_status':None, 'ksef_numer':None}
        self.client = Mock()
        self.send_calls = []
        self.status_calls = []
        self.response = {'status':{'code':150}}
        def send(invoice_id, actor, **kwargs):
            self.send_calls.append(invoice_id)
            self.state['ksef_status']='oczekuje'
        def refresh(invoice_id, actor, **kwargs):
            self.status_calls.append(invoice_id)
            if self.response['status']['code']==200:
                self.state.update(ksef_status='przyjeta',ksef_numer=self.response['ksefNumber'])
            elif self.response['status']['code']>=400:
                self.state['ksef_status']='odrzucona'
            return deepcopy(self.response)
        for patcher in (patch.object(submission,'_authorized_invoice',side_effect=lambda *a:dict(self.state)),
                        patch.object(submission,'client_from_settings',return_value=self.client),
                        patch.object(submission,'send_invoice_document',side_effect=send),
                        patch.object(submission,'refresh_invoice_submission',side_effect=refresh)):
            patcher.start(); self.addCleanup(patcher.stop)

    def test_pending_then_accepted_downloads_without_sending_again(self):
        first=submission.process_invoice_batch([1],7,send=True)
        self.assertEqual(first['pending'],[1])
        self.assertFalse(self.state['ksef_numer'])
        self.response={'status':{'code':200},'ksefNumber':'KSEF-ACCEPTED'}
        with patch.object(submission,'download_submitted_invoice',return_value='/archive/invoice.xml') as download:
            second=submission.process_invoice_batch([1],7,send=False)
        self.assertEqual(second['accepted'][0]['number'],'KSEF-ACCEPTED')
        download.assert_called_once_with(1,7,client=self.client)
        self.assertEqual(self.send_calls,[1])

    def test_acceptance_survives_failed_download_and_retry_only_downloads(self):
        self.response={'status':{'code':200},'ksefNumber':'KSEF-ACCEPTED'}
        with patch.object(submission,'download_submitted_invoice',side_effect=OSError('temporary network error')):
            result=submission.process_invoice_batch([1],7,send=True)
        self.assertEqual(self.state['ksef_numer'],'KSEF-ACCEPTED')
        self.assertEqual(result['pending'],[1])
        with patch.object(submission,'download_submitted_invoice',return_value='/archive/invoice.xml'):
            result=submission.process_invoice_batch([1],7,send=False)
        self.assertEqual(len(result['accepted']),1)
        self.assertEqual(self.send_calls,[1])
        self.assertEqual(self.status_calls,[1])

    def test_uncertain_transmission_never_resends_on_automatic_pass(self):
        self.state['ksef_status']='do_sprawdzenia'
        result=submission.process_invoice_batch([1],7,send=False)
        self.assertEqual(result['pending'],[1])
        self.assertEqual(self.send_calls,[])

    def test_rejection_does_not_assign_number_or_download(self):
        self.response={'status':{'code':450,'description':'Invalid invoice'}}
        with patch.object(submission,'download_submitted_invoice') as download:
            result=submission.process_invoice_batch([1],7,send=True)
        self.assertEqual(result['rejected'][0]['id'],1)
        self.assertFalse(self.state['ksef_numer'])
        download.assert_not_called()


class AcceptedDownloadTests(unittest.TestCase):
    def test_accepted_xml_is_verified_archived_and_reused(self):
        xml=build_fa3_xml(INVOICE,[POSITION],SELLER)
        invoice={**INVOICE,'ksef_status':'przyjeta','ksef_numer':'KSEF-ACCEPTED',
                 'ksef_srodowisko_wysylki':'test','ksef_nip_wysylki':SELLER['nip'],
                 'ksef_hash_faktury':base64.b64encode(hashlib.sha256(xml).digest()).decode()}
        settings={submission.ENV_KEY:'test',submission.NIP_KEY:SELLER['nip']}
        client=Mock(); client.download_invoice_bytes.return_value=xml
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(submission,'_authorized_invoice',return_value=invoice), \
             patch.object(submission,'get_setting',side_effect=lambda key:settings.get(key)), \
             patch('app.config.get_data_dir',return_value=Path(folder)):
            path=submission.download_submitted_invoice(1,7,client=client)
            self.assertEqual(Path(path).read_bytes(),xml)
            self.assertEqual(path,submission.download_submitted_invoice(1,7,client=client))
            client.download_invoice_bytes.assert_called_once_with('KSEF-ACCEPTED')

    def test_changed_download_is_not_archived(self):
        xml=build_fa3_xml(INVOICE,[POSITION],SELLER)
        invoice={**INVOICE,'ksef_status':'przyjeta','ksef_numer':'KSEF-ACCEPTED',
                 'ksef_srodowisko_wysylki':'test','ksef_nip_wysylki':SELLER['nip'],'ksef_hash_faktury':'wrong-hash'}
        client=Mock();client.download_invoice_bytes.return_value=xml
        settings={submission.ENV_KEY:'test',submission.NIP_KEY:SELLER['nip']}
        with tempfile.TemporaryDirectory() as folder, patch.object(submission,'_authorized_invoice',return_value=invoice), \
             patch.object(submission,'get_setting',side_effect=lambda key:settings.get(key)), \
             patch('app.config.get_data_dir',return_value=Path(folder)), self.assertRaises(KsefError):
            submission.download_submitted_invoice(1,7,client=client)


class DirectSendUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def test_selected_invoices_start_batch_without_xml_file_dialog(self):
        page=FakturyPage.__new__(FakturyPage);QWidget.__init__(page)
        self.addCleanup(page.close)
        page.pracownik_id=7;page._ksef_send_worker=None;page._ksef_thread=None;page._ksef_poll_counts={}
        page._can_export=Mock(return_value=True)
        page.send_pdf_after_ksef=Mock();page.send_pdf_after_ksef.isChecked.return_value=False
        page._start_ksef_batch=Mock()
        page.issued_table=QTableWidget(2,1)
        for row in range(2):
            page.issued_table.setItem(row,0,QTableWidgetItem(str(row+1)))
            page.issued_table.selectionModel().select(page.issued_table.model().index(row,0),QItemSelectionModel.SelectionFlag.Select)
        with patch('app.invoices.faktury_ui.get_invoice_by_id',side_effect=lambda i:{**INVOICE,'id':i}), \
             patch('app.invoices.faktury_ui.can_access_invoice',return_value=True), \
             patch('app.invoices.faktury_ui.ksef_configured',return_value=True), \
             patch('app.db.dao_app_settings.get_setting',return_value='test'), \
             patch.object(QMessageBox,'question',return_value=QMessageBox.StandardButton.Yes), \
             patch.object(QFileDialog,'getOpenFileName') as file_dialog:
            page._send_selected_to_ksef()
        page._start_ksef_batch.assert_called_once_with([1,2],send=True)
        file_dialog.assert_not_called()
