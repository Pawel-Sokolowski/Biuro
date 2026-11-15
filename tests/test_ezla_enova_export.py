"""The add-on may retrieve only authenticated, authorized employer reports."""
import base64
import hashlib
import json
from unittest.mock import patch

from tests.base import DbTestCase
from tests.test_ezla_report import report, certificate
from app.db.dao_app_settings import set_setting
from app.integrations.ezla.enova_export import export_reports
from app.utils.crypto import encrypt_password


class EnovaEzlaExportTests(DbTestCase):
    def save_report(self, company, raw, **extra):
        key = f'ezla_report_{company}_' + hashlib.sha256(raw).hexdigest()
        set_setting(key, json.dumps({'months': ['2018-04'], 'needs_review': False,
                    'xml_enc': encrypt_password(base64.b64encode(raw).decode('ascii')), **extra}))
        return key

    def test_export_is_bound_to_one_company_and_contains_no_service_credentials(self):
        company = self.make_company(nip='1112223344')
        other = self.make_company('Other company', nip='9999999999')
        self.make_company('No tax ID configured', nip='')
        raw = report(certificate())
        key = self.save_report(company, raw)
        self.save_report(other, raw.replace(b'1112223344', b'9999999999'))
        result = export_reports(1, {'company_nip': '1112223344', 'month': '2018-04'})
        self.assertEqual(set(result), {'schema', 'company_nip', 'month', 'reports', 'next'})
        self.assertEqual([r['id'] for r in result['reports']], [key])
        self.assertEqual(base64.b64decode(result['reports'][0]['xml']), raw)
        self.assertEqual(set(result['reports'][0]), {'id','sha256','payer_nip','xml'})
        self.assertEqual(export_reports(1, {'company_nip': '1112223344', 'month':'2018-04','after':key})['reports'], [])

    def test_missing_export_permission_is_rejected_before_reading_xml(self):
        with patch('app.integrations.ezla.enova_export.can', return_value=False):
            with self.assertRaises(PermissionError):
                export_reports(1, {'company_nip':'1112223344','month':'2018-04'})

    def test_ambiguous_or_unassigned_company_is_rejected(self):
        company = self.make_company(nip='1112223344')
        self.save_report(company, report(certificate()))
        with patch('app.integrations.ezla.enova_export.assigned_company_ids', return_value=set()):
            with self.assertRaises(PermissionError):
                export_reports(1, {'company_nip':'1112223344','month':'2018-04'})
        self.make_company('Duplicate NIP in another format', nip='PL111-222-33-44')
        with self.assertRaises(PermissionError):
            export_reports(1, {'company_nip':'1112223344','month':'2018-04'})

    def test_wrong_payer_and_tampered_content_fail_closed(self):
        company = self.make_company(nip='1112223344')
        self.save_report(company, report(certificate()).replace(b'1112223344',b'9999999999'))
        with self.assertRaises(ValueError):
            export_reports(1, {'company_nip':'1112223344','month':'2018-04'})

    def test_civil_partners_reports_share_the_single_company_database(self):
        from app.db.dao_companies import save_partner
        company = self.make_company('Spółka cywilna', nip='1112223344', forma_dzialalnosci='Spółka cywilna')
        save_partner(company, {'imie':'Wspólnik A', 'nazwisko':'Test', 'nip':'9999999999'})
        save_partner(company, {'imie':'Wspólnik B', 'nazwisko':'Test', 'nip':'8888888888'})
        for payer in ('9999999999','8888888888'):
            self.save_report(company, report(certificate()).replace(b'1112223344',payer.encode()))
        # There are no JDG company rows/databases. The add-on asks with the civil company's NIP.
        result = export_reports(1, {'company_nip':'1112223344','month':'2018-04'})
        self.assertEqual(result['company_nip'], '1112223344')
        self.assertEqual({r['payer_nip'] for r in result['reports']}, {'9999999999','8888888888'})
        for payer in ('9999999999','8888888888'):
            with self.assertRaises(PermissionError):
                export_reports(1, {'company_nip':payer,'month':'2018-04'})

    def test_annulment_reports_are_returned_even_outside_selected_month(self):
        company = self.make_company(nip='1112223344')
        self.save_report(company, report(certificate(status='Anulowane')), needs_review=True)
        result = export_reports(1, {'company_nip':'1112223344','month':'2026-10'})
        self.assertEqual(len(result['reports']), 1)
