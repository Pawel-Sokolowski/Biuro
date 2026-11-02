"""Accepted KSeF XML -> PDF with QR -> explicitly queued SMTP delivery."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from PyQt6.QtWidgets import QApplication, QScrollArea, QPushButton
from app.invoices import invoice_delivery as delivery
from app.invoices.fa3_builder import build_fa3_xml
from app.invoices.ksef_client import KsefError
from app.invoices.ksef import ksef_url
from app.companies import company_form

SELLER={'nip':'9876543210','name':'Official Seller','address':'Sender Street 1'}
INVOICE={'id':1,'firma_id':2,'eksportowana':1,'numer':'FV/1/2026','data_wystawienia':'2026-11-01',
         'data_sprzedazy':'2026-10-31','nabywca_nip':'1234567890','nabywca_pelna':'Official Buyer',
         'nabywca_adres':'Buyer Street 7','kwota_netto':100,'kwota_brutto':123,
         'ksef_status':'przyjeta','ksef_numer':'9876543210-20261101-123456789ABC-12','ksef_srodowisko_wysylki':'test',
         'ksef_nip_wysylki':SELLER['nip'],'invoice_email_status':'queued',
         'invoice_email_account_id':8,'invoice_email_recipients':json.dumps(['invoices@example.com'])}
POSITION={'nazwa':'Accounting consultation','ilosc':1,'cena_netto':100,'vat':23}


class InvoiceDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.xml_path=Path(self.temp.name)/'accepted.xml'
        self.xml=build_fa3_xml(INVOICE,[POSITION],SELLER)
        self.xml_path.write_bytes(self.xml)
        self.invoice={**INVOICE,'ksef_hash_faktury':base64.b64encode(hashlib.sha256(self.xml).digest()).decode()}
        for patcher in (patch('app.invoices.ksef_submission._authorized_invoice',side_effect=lambda *args:dict(self.invoice)),
                        patch('app.email.email_storage.get_email_accounts_for_employee',return_value=[{'id':8,'email':'office@example.com'}]),
                        patch('app.email.email_actions.get_smtp_config_for_account',return_value={'host':'smtp.example.com','user':'office@example.com'}),
                        patch('app.invoices.invoice_preview.get_setting',return_value=None),
                        patch('app.invoices.ksef.get_setting',return_value=None),
                        patch('app.invoices.invoice_preview.get_setting_first',return_value='Changed Config Seller')):
            patcher.start();self.addCleanup(patcher.stop)

    def test_company_invoice_address_overrides_general_and_snapshot_addresses(self):
        with patch('app.db.dao_companies.get_company',return_value={'email_faktury':'billing@client.com','adres_email':'owner@client.com'}):
            plan=delivery.delivery_plan([1],7)
        self.assertEqual(plan[0]['recipients'],['billing@client.com'])
        for value in ('bad','a@b.com\r\nBcc:bad@b.com','Name <a@b.com>'):
            with self.subTest(value=value),self.assertRaises(ValueError):delivery.parse_recipients(value)

    def test_real_pdf_contains_official_parties_ksef_number_and_qr_image(self):
        path=delivery.official_pdf(self.invoice,self.xml_path)
        content=Path(path).read_bytes()
        self.assertTrue(content.startswith(b'%PDF'))
        self.assertIn(b'/Subtype /Image',content)
        self.assertIn('qr-test.ksef.mf.gov.pl',ksef_url(self.invoice['ksef_numer'],self.invoice))
        if shutil.which('pdftotext'):
            text=subprocess.check_output(['pdftotext','-layout',path,'-'],text=True)
            self.assertIn('Official Seller',text)
            self.assertIn('Official Buyer',text)
            self.assertIn(self.invoice['ksef_numer'],text)
            self.assertNotIn('Changed Config Seller',text)

    def test_no_smtp_before_acceptance(self):
        self.invoice.update(ksef_status='oczekuje',ksef_numer=None)
        with patch('app.email.email_actions.send_email_smtp') as smtp,self.assertRaises(KsefError):
            delivery.deliver_if_queued(1,7,str(self.xml_path))
        smtp.assert_not_called()

    def test_no_queued_delivery_does_not_send(self):
        self.invoice['invoice_email_status']=None
        with patch('app.email.email_actions.send_email_smtp') as smtp:
            self.assertIsNone(delivery.deliver_if_queued(1,7,str(self.xml_path)))
        smtp.assert_not_called()

    def test_success_sends_pdf_once_to_frozen_recipients(self):
        conn=Mock();conn.execute.return_value.fetchone.return_value=(1,)
        def update(invoice_id,status,**kwargs):self.invoice['invoice_email_status']=status
        with patch.object(delivery,'get_conn',return_value=conn), \
             patch.object(delivery,'_delivery_state',side_effect=update), \
             patch('app.email.email_actions.send_email_smtp',return_value=(True,'OK')) as smtp:
            result=delivery.deliver_if_queued(1,7,str(self.xml_path))
            self.assertEqual(result['status'],'sent')
            delivery.deliver_if_queued(1,7,str(self.xml_path))
        smtp.assert_called_once()
        self.assertEqual(smtp.call_args.args[1],['invoices@example.com'])
        self.assertTrue(smtp.call_args.kwargs['require_attachments'])
        self.assertTrue(Path(smtp.call_args.kwargs['attachments'][0]).read_bytes().startswith(b'%PDF'))
        self.assertIn('\n',smtp.call_args.args[3])

    def test_uncertain_smtp_result_does_not_retry_and_revoked_sender_cannot_send(self):
        conn=Mock();conn.execute.return_value.fetchone.return_value=(1,)
        def update(invoice_id,status,**kwargs):self.invoice['invoice_email_status']=status
        with patch.object(delivery,'get_conn',return_value=conn),patch.object(delivery,'_delivery_state',side_effect=update), \
             patch('app.email.email_actions.send_email_smtp',return_value=(False,'timeout after DATA')) as smtp:
            self.assertEqual(delivery.deliver_if_queued(1,7,str(self.xml_path))['status'],'uncertain')
            delivery.deliver_if_queued(1,7,str(self.xml_path))
        smtp.assert_called_once()
        self.invoice['invoice_email_status']='queued'
        with patch('app.email.email_storage.get_email_accounts_for_employee',return_value=[]), \
             patch.object(delivery,'_delivery_state'),patch('app.email.email_actions.send_email_smtp') as smtp, self.assertRaises(PermissionError):
            delivery.deliver_if_queued(1,7,str(self.xml_path))
        smtp.assert_not_called()

    def test_competing_reservation_does_not_send(self):
        conn=Mock();conn.execute.return_value.fetchone.return_value=None
        with patch.object(delivery,'get_conn',return_value=conn),patch('app.email.email_actions.send_email_smtp') as smtp:
            self.assertEqual(delivery.deliver_if_queued(1,7,str(self.xml_path))['status'],'uncertain')
        smtp.assert_not_called()

    def test_pdf_without_qr_is_not_sent(self):
        with patch('app.invoices.invoice_preview._qr_data_url',return_value=''), \
             patch.object(delivery,'_delivery_state'),patch('app.email.email_actions.send_email_smtp') as smtp,self.assertRaises(KsefError):
            delivery.deliver_if_queued(1,7,str(self.xml_path))
        smtp.assert_not_called()


class CompanyTabsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def test_company_form_is_tabbed_and_invoice_email_roundtrips_payload(self):
        patchers=[patch.object(company_form,name,return_value=[]) for name in (
            'get_company_field_options','get_tax_offices','get_employees_list','get_pricing_kadry',
            'get_all_email_accounts','get_ksiegowosc_options','get_known_sender_addresses')]
        patchers += [patch.object(company_form,'get_setting_bool',return_value=False)]
        for patcher in patchers:patcher.start();self.addCleanup(patcher.stop)
        form=company_form.CompanyForm();self.addCleanup(form.close)
        self.assertEqual(form.section_tabs.count(),9)
        for index in range(form.section_tabs.count()):
            self.assertIsInstance(form.section_tabs.widget(index),QScrollArea)
        form.inputs['email_faktury'].setText('invoices@firma.com')
        self.assertEqual(form._collect_payload()['email_faktury'],'invoices@firma.com')
        form._load_company_from_payload({'nazwa':'Example','email_faktury':'billing@firma.com'})
        self.assertEqual(form.inputs['email_faktury'].text(),'billing@firma.com')
        save=next(button for button in form.findChildren(QPushButton) if button.text()=='Zapisz')
        self.assertIs(save.parentWidget(),form)
