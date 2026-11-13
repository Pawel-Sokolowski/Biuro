import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile
from pypdf import PdfWriter
from tests.base import DbTestCase
from app.db.dao_conn import get_conn
from app.db.dao_app_settings import set_setting
from app.db.dao_companies import save_partner, get_partners, get_company, list_mailing_companies
from app.companies.identities import payer_nips, company_recipients
from app.employees.authority_profiles import save, load


class CivilOwnersAuthorityTests(DbTestCase):
    def setUp(self):
        super().setUp()
        with get_conn() as conn:
            self.actor = conn.execute('SELECT id FROM pracownicy WHERE is_admin=1').fetchone()[0]
        self.company = self.make_company(nip='1112223344', forma_dzialalnosci='Spółka cywilna',
                                        ulica='Testowa 1', kod_pocztowy='00-001', miasto='Testowo')
        for first, nip, email in [('Anna','2223334455','a@example.test'),('Jan','3334445556','b@example.test')]:
            save_partner(self.company, dict(imie_pierwsze=first, nazwisko='Testowy', nip=nip,
                         pesel='90010112345', email=email, ulica='Testowa', nr_domu='1', kod_pocztowy='00-001', miejscowosc='Testowo'))

    def test_company_identity_stays_distinct_and_both_partners_receive_company_mail(self):
        company, owners = get_company(self.company), get_partners(self.company)
        self.assertEqual(company['nip'], '1112223344')
        self.assertEqual(payer_nips(company, owners), ['1112223344','2223334455','3334445556'])
        self.assertEqual(company_recipients(company, owners), ['a@example.test','b@example.test'])
        row = next(row for row in list_mailing_companies(self.actor) if row['id'] == self.company)
        self.assertEqual(row['adres_email'], 'a@example.test, b@example.test')
        with self.assertRaises(ValueError):
            company_recipients(company, [owners[0], dict(owners[1], email='')])
        from app.services.email_reminders import _civil_recipient
        with get_conn() as conn:
            reminder = _civil_recipient(conn, dict(company, email='company@example.test', contact_emails=['other@example.test']), self.company, 'email')
        self.assertEqual(reminder['email'], 'a@example.test, b@example.test')
        self.assertEqual(reminder['contact_emails'], [])

    def test_partial_partner_edit_does_not_erase_identifiers_or_contacts(self):
        person = get_partners(self.company)[0]
        save_partner(self.company, {'id': person['id'], 'imie_pierwsze': 'Nowe'})
        updated = get_partners(self.company)[0]
        for field in ('nip','pesel','email','ulica'):
            self.assertEqual(updated[field], person[field])
        with self.assertRaises(ValueError):
            save_partner(self.make_company(nip='9999999999'), {'id': person['id']})

    def test_employee_profiles_are_encrypted_permission_checked_and_autofilled(self):
        employee = self.make_employee('employee@example.test', 'Ewa', 'Testowa')
        save(employee, {'pesel': '90010112345'}, self.actor)
        self.assertEqual(load(employee, self.actor)['pesel'], '90010112345')
        with get_conn() as conn:
            value = conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s', (f'employee_authority_{employee}',)).fetchone()[0]
        self.assertNotIn('90010112345', value)
        with self.assertRaises(PermissionError):
            save(employee, {}, None)
        from app.documents.power_of_attorney import resolve_employee
        for key, value in [('adres','Testowa 1'),('kod','00-001'),('miasto','Testowo'),('email','office@example.test')]:
            set_setting('faktury_sprzedawca_'+key, value)
        person = resolve_employee({'attorney': {'employee_id': employee}}, self.actor)
        self.assertEqual(person['pesel'], '90010112345')
        self.assertEqual(person['email'], 'office@example.test')

    def test_owner_principal_selection_and_checkbox_employee_profiles(self):
        from app.documents.contract_pack_ui import ContractPackDialog
        from PyQt6.QtCore import Qt
        employee = self.make_employee('employee@example.test', 'Ewa', 'Testowa')
        save(employee, {'pesel':'90010112345'}, self.actor)
        dialog = ContractPackDialog(self.company, self.actor)
        self.addCleanup(dialog.close)
        dialog.principal_owner.setCurrentIndex(1)
        self.assertEqual(dialog.values()['nip'], '2223334455')
        self.assertEqual(dialog.values()['principal_kind'], 'person')
        dialog.principal_owner.setCurrentIndex(0)
        self.assertEqual(dialog.values()['nip'], '1112223344')
        for index in range(dialog.employee_checks.count()):
            item = dialog.employee_checks.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == employee:
                item.setCheckState(Qt.CheckState.Checked)
        self.assertEqual(dialog.values()['attorneys'][0]['pesel'], '90010112345')

    def test_multi_employee_upl_bundle_has_one_distinct_form_per_employee(self):
        from app.documents.contract_pack import generate_pack
        people = [self.make_employee(f'e{i}@example.test', f'Osoba{i}', 'Testowa') for i in (1,2)]
        for employee in people:
            save(employee, {'pesel':'90010112345'}, self.actor)
        for key,value in [('adres','Testowa 1'),('kod','00-001'),('miasto','Testowo'),('email','office@example.test')]:
            set_setting('faktury_sprzedawca_'+key,value)
        seen = []
        def render(data, path, renderer):
            seen.append(data['attorney']['employee_id'])
            writer = PdfWriter();writer.add_blank_page(width=595,height=842);writer.write(path);writer.close()
        overrides = dict(agreement_date='01.11.2026', authority_from='01.11.2026', tax_office='Testowy US',
                         attorneys=[{'employee_id': person} for person in people])
        with tempfile.TemporaryDirectory() as directory, patch('app.documents.upl_paper.write_pdf', side_effect=render):
            result = Path(directory)/'forms.zip'
            generate_pack(self.company, self.actor, overrides, ['11_upl'], result, docx_only=True, form_renderer=lambda *args:None)
            with ZipFile(result) as archive:
                self.assertEqual(len(archive.namelist()),2)
                self.assertEqual(len(set(archive.namelist())),2)
        self.assertEqual(seen, people)

    def test_owner_zla_is_stored_under_explicit_company_link_and_remains_readable(self):
        from app.integrations.ezla.settings import save_settings
        from app.integrations.ezla.sync import run_sync, load_reports
        from app.integrations.ezla.service import Download, ServiceError
        from tests.test_ezla_report import report, certificate
        save_settings({'enabled':True,'login':'service@example.test','companies':[self.company]}, actor_id=self.actor,
                      password='synthetic-password',zip_password='synthetic-zip')
        requested=[]
        def fetch(nip,*args,**kwargs):
            requested.append(nip)
            if nip=='1112223344':
                raise ServiceError('200')
            return [Download('2018-08-01', report(certificate()).replace(b'1112223344',nip.encode()))] if nip=='2223334455' else []
        with patch('app.integrations.ezla.sync.fetch_reports',side_effect=fetch):
            result=run_sync(actor_id=self.actor)
        self.assertEqual(set(requested),{'1112223344','2223334455','3334445556'})
        self.assertEqual(result['failed'],1)  # Partial errors are not presented as complete success.
        reports=load_reports(self.actor,'2018-04')
        self.assertEqual(reports['errors'],[])
        self.assertEqual(reports['reports'][0]['company']['id'],self.company)
        self.assertEqual(reports['reports'][0]['report'].nip,'2223334455')
        with get_conn() as conn:
            status=json.loads(conn.execute('SELECT wartosc FROM app_settings WHERE klucz=%s',(f'ezla_sync_{self.company}',)).fetchone()[0])
        self.assertEqual(status['payers']['2223334455']['state'],'ok')
        self.assertEqual(status['payers']['1112223344']['state'],'failed')
