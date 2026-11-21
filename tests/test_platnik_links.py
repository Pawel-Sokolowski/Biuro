import json
from unittest.mock import patch

from app.db.checking_storage import read_snapshot, save_snapshot
from app.db.dao_app_settings import set_setting
from app.db.dao_conn import get_conn
from app.integrations.platnik import links
from app.integrations.platnik.declarations import decoded_snapshot, import_months, import_year
from app.integrations.platnik.holidays import KEY
from app.integrations.platnik.insurance import SOURCE
from tests.base import DbTestCase

NIP = '1112223344'
OTHER_NIP = '9998887766'
RECORD = {'form': 'ZUS DRA', 'source_id': 1, 'payer_id': 7, 'nip': NIP, 'report_id': '01',
          'miesiac': '2026-11', 'employee_id': None, 'employee_name': None, 'raw_status': 'P', 'valid_link': 1}


class PlatnikLinksTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.company = self.make_company('Alfa', nip=NIP)
        self.other = self.make_company('Beta', nip=OTHER_NIP)
        set_setting(KEY, json.dumps({'server': 'fixture', 'database': 'platnik', 'enabled': True}))
        catalog = patch.object(links, 'catalogue', return_value=(
            'source-db', {NIP: [7, 8], OTHER_NIP: [9]},
            {(NIP, 7): [{'person_id': person, 'first': '2026-01-01', 'last': '2026-07-01', 'count': 2}
                       for person in (20, 21)],
             (NIP, 8): [{'person_id': 80, 'first': '2026-01-01', 'last': '2026-01-01', 'count': 1}]}, True,
        ))
        self.catalogue = catalog.start()
        self.addCleanup(catalog.stop)

    def save(self, payer=7, person=21, source='source-db', revision=None):
        return links.save(1, self.company, NIP, payer, person, source, revision)

    def review_company(self):
        return next(row for row in links.review(1)['rows'] if row['company_id'] == self.company)

    def test_approval_selects_payer_and_owner_for_worker_and_yearly_import(self):
        with get_conn() as conn:
            save_snapshot(conn, 'platnik_declarations', self.company, '2026-11', 'keep existing')
        other_record = {**RECORD, 'source_id': 3, 'payer_id': 9, 'nip': OTHER_NIP}
        registry = [(NIP, 7, 20, '0510', 0, 0, '2026-01-01', None),
                    (NIP, 7, 21, '0590', 0, 0, '2026-01-01', None),
                    (NIP, 8, 80, '0570', 0, 0, '2026-01-01', None)]
        self.assertEqual(self.review_company()['status'], 'Wybierz kartotekę płatnika')
        with patch('app.integrations.platnik.declarations.fetch', return_value=[other_record]) as read, \
             patch('app.integrations.platnik.insurance.fetch_periods', return_value=[]):
            result = import_months(['2026-11'])
            self.assertEqual(result['declaration_link_errors'], {self.company: 'PlatnikPayerAmbiguous'})
            self.assertEqual((result['mode'], result['companies']), ('partial', 1))
            self.assertEqual(read.call_args.kwargs['selected_nips'], {OTHER_NIP: self.other})
        with get_conn() as conn:
            self.assertEqual(read_snapshot(conn, 'platnik_declarations', self.company, '2026-11'), 'keep existing')
            self.assertEqual(len(decoded_snapshot(read_snapshot(conn, 'platnik_declarations', self.other, '2026-11'))['declarations']), 1)
        self.save()
        self.assertEqual(self.review_company()['status'], 'Zatwierdzono')
        preferred = {**RECORD, 'source_id': 2}
        alternate = {**RECORD, 'payer_id': 8}
        with patch('app.integrations.platnik.declarations.fetch', return_value=[alternate, preferred, other_record]) as read, \
             patch('app.integrations.platnik.insurance.fetch_periods', return_value=registry) as read_registry:
            result = import_months(['2026-11'])
            self.assertEqual(read.call_args.kwargs['expected_source'], 'source-db')
            self.assertEqual(read_registry.call_args.kwargs['expected_source'], 'source-db')
            self.assertEqual(result['insurance_registry'], 'applied')
        with get_conn() as conn:
            declarations = decoded_snapshot(read_snapshot(conn, 'platnik_declarations', self.company, '2026-11'))
            owner = decoded_snapshot(read_snapshot(conn, SOURCE, self.company))
            self.assertEqual([row['source_id'] for row in declarations['declarations']], ['2'])
            self.assertEqual([row['code'] for row in owner['periods']], ['059000'])
        annual = [{**row, 'form': 'ZUS IWA', 'rok': '2026'} for row in (alternate, preferred)]
        with patch('app.integrations.platnik.declarations.fetch', return_value=annual):
            self.assertEqual(import_year(2026, company_ids=[self.company])['mode'], 'applied')
        with get_conn() as conn:
            declarations = decoded_snapshot(read_snapshot(conn, 'platnik_declarations', self.company, '2026'))
            self.assertEqual([row['source_id'] for row in declarations['declarations']], ['2'])

    def test_changed_source_or_missing_owner_does_not_overwrite_saved_snapshots(self):
        self.save()
        with get_conn() as conn:
            save_snapshot(conn, SOURCE, self.company, '', 'old registry')
            save_snapshot(conn, 'platnik_declarations', self.company, '2026-10', 'old declarations')
        with patch('app.integrations.platnik.declarations.fetch', return_value=[]), \
             patch('app.integrations.platnik.insurance.fetch_periods', return_value=[]):
            result = import_months(['2026-11'], company_ids=[self.company])
            self.assertEqual(result['insurance_registry_errors'], {self.company: 'InsuranceOwnerLinkStale'})
        _, payers, owners, available = self.catalogue.return_value
        self.catalogue.return_value = ('other-db', payers, owners, available)
        self.assertEqual(self.review_company()['status'], 'Wymaga ponownego zatwierdzenia')
        with patch('app.integrations.platnik.declarations.fetch') as read:
            result = import_months(['2026-10'], company_ids=[self.company])
            self.assertEqual(result['declaration_link_errors'], {self.company: 'PlatnikLinkStale'})
            read.assert_not_called()
        with get_conn() as conn:
            self.assertEqual(read_snapshot(conn, SOURCE, self.company), 'old registry')
            self.assertEqual(read_snapshot(conn, 'platnik_declarations', self.company, '2026-10'), 'old declarations')

    def test_changed_company_nip_requires_review_of_the_old_approval(self):
        self.save()
        new_nip = '3332221144'
        with get_conn() as conn:
            conn.execute('UPDATE firmy SET nip=%s WHERE id=%s', (new_nip, self.company))
        source, payers, owners, available = self.catalogue.return_value
        self.catalogue.return_value = (source, {**payers, new_nip: [11]}, owners, available)
        with patch('app.integrations.platnik.declarations.fetch') as read:
            result = import_months(['2026-11'], company_ids=[self.company])
        self.assertEqual(result['declaration_link_errors'], {self.company: 'PlatnikLinkStale'})
        read.assert_not_called()

    def test_civil_partners_have_separate_payer_approvals(self):
        from app.db.dao_companies import save_partner

        company_nip, partner_nip = '3332221144', '7778889900'
        company = self.make_company('Spółka', nip=company_nip, forma_dzialalnosci='s.c.')
        save_partner(company, {'imie_pierwsze': 'Anna', 'nazwisko': 'Testowa', 'nip': partner_nip})
        source, payers, owners, available = self.catalogue.return_value
        self.catalogue.return_value = (source, {**payers, company_nip: [11], partner_nip: [12, 13]}, owners, available)
        rows = {row['nip']: row for row in links.review(1)['rows'] if row['company_id'] == company}
        self.assertEqual(rows[company_nip]['status'], 'Dopasowano po NIP')
        self.assertEqual(rows[partner_nip]['status'], 'Wybierz kartotekę płatnika')
        links.save(1, company, partner_nip, 13, None, source, None)
        mapped = {company_nip: company, partner_nip: company}
        selected, errors, _ = links.import_links({'server': 'fixture', 'database': 'platnik'}, mapped)
        self.assertFalse(errors)
        self.assertEqual({nip: row['payer_id'] for nip, row in selected.items()}, {company_nip: 11, partner_nip: 13})

    def test_requires_admin_and_a_current_owner_in_the_selected_payer(self):
        employee = self.make_employee()
        with self.assertRaises(PermissionError):
            links.review(employee)
        with self.assertRaises(PermissionError):
            links.save(employee, self.company, NIP, 7, 21, 'source-db', None)
        with self.assertRaisesRegex(ValueError, 'właściciela'):
            self.save(person=80)
        with self.assertRaisesRegex(ValueError, 'Odśwież'):
            self.save(source='old-db')
        with self.assertRaisesRegex(ValueError, 'należeć'):
            links.save(1, self.other, NIP, 7, 21, 'source-db', None)
        self.save()
        with self.assertRaisesRegex(ValueError, 'zostało zmienione'):
            self.save(person=20)
        revision = self.review_company()['approval']['revision']
        self.save(payer=None, person=None, revision=revision)
        self.assertEqual(self.review_company()['status'], 'Wybierz kartotekę płatnika')
        with get_conn() as conn:
            self.assertEqual([row[0] for row in conn.execute(
                "SELECT akcja FROM audit_log WHERE tabela='platnik_approved_links' ORDER BY id"
            )], ['approve', 'revoke'])

    def test_dialog_selects_an_owner_from_the_chosen_payer(self):
        from app.admin.settings.platnik_links import PlatnikLinksDialog
        from tests.test_review_optimizations import wait_until

        dialog = PlatnikLinksDialog(1)
        try:
            self.assertTrue(wait_until(lambda: not dialog.busy and dialog.table.rowCount() > 0))
            dialog.search.setText('Alfa')
            self.assertEqual(dialog.table.rowCount(), 1)
            self.assertFalse(dialog.approve.isEnabled())
            dialog.payer.setCurrentIndex(dialog.payer.findData(7))
            self.assertEqual([dialog.person.itemData(i) for i in range(dialog.person.count())], [None, 20, 21])
            dialog.person.setCurrentIndex(dialog.person.findData(21))
            dialog.approve.click()
            self.assertTrue(wait_until(lambda: not dialog.busy and dialog.table.item(0, 2).text() == 'Zatwierdzono'))
            self.assertEqual(self.review_company()['approval']['person_id'], 21)
        finally:
            dialog.close()
