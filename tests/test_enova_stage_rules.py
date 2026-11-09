from datetime import date
from unittest import TestCase
from unittest.mock import Mock, patch

from app.db.dao_conn import get_conn
from app.integrations.enova.document_counts import run_import, sql_credentials, calculate_total
from app.integrations.enova.counting_rules import save_rules, load_rules, effective_divisor
from tests.base import DbTestCase

DAY = date(2026, 11, 9)
NIP = '1234567890'


class EnovaCredentialTests(TestCase):
    def test_lowercase_env_aliases_and_prefixed_pair_priority(self):
        with patch.dict('os.environ', {'sql_user': 'local_reader', 'sql_password': 'local_secret'}, clear=True):
            self.assertEqual(sql_credentials(), ('local_reader', 'local_secret'))
        with patch.dict('os.environ', {'sql_user': 'local_reader', 'sql_password': 'local_secret',
                                      'ENOVA_SQL_USER': 'reader', 'ENOVA_SQL_PASSWORD': 'secret'}, clear=True):
            self.assertEqual(sql_credentials(), ('reader', 'secret'))
        with patch.dict('os.environ', {'SQL_USER': 'upper_reader', 'SQL_PASSWORD': 'upper_secret'}, clear=True):
            self.assertEqual(sql_credentials(), ('upper_reader', 'upper_secret'))

    def test_incomplete_credentials_never_mix_pairs_or_echo_values(self):
        with patch.dict('os.environ', {'ENOVA_SQL_USER': 'do_not_print', 'sql_user': 'reader',
                                      'sql_password': 'secret'}, clear=True):
            with self.assertRaises(ValueError) as exc:
                sql_credentials()
            self.assertNotIn('do_not_print', str(exc.exception))
            self.assertNotIn('secret', str(exc.exception))

    def test_divisors_and_rounding_apply_to_month_total(self):
        self.assertEqual(calculate_total('KH', 92, 190, 3), 155)
        self.assertEqual(calculate_total('KH', 92, 190, 10), 111)
        self.assertEqual(calculate_total('KH', 92, 195, 10), 112)
        self.assertEqual(calculate_total('KH', 92, 190, 1), 282)
        self.assertEqual(calculate_total('KPIR', 55, 0, 10), 55)
        for divisor in (0, -1, True, 1.5, 1001):
            with self.assertRaises(ValueError):
                calculate_total('KH', 92, 190, divisor)


class EnovaStageRuleTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.company = self.make_company(nip=NIP)
        self.config = {'enabled': True, 'months_back': 1, 'sources': [
            {'database': 'ClientA', 'nip': NIP, 'accounting_type': 'KH'}]}
        self.fetch = Mock(side_effect=lambda config, source, months: [
            {'nip': source['nip'], 'miesiac': month, 'dokumenty': 92, 'pozycje_bankowe': 190}
            for month in months])
        self.set_stage('2026-11', 'Wysłane wyniki')

    def set_stage(self, month, stage, payroll_stage='Brak'):
        with get_conn() as conn:
            conn.execute('INSERT INTO statusy_miesieczne(firma_id,miesiac,etap,etap_kadry,ilosc_dok) '
                         'VALUES (%s,%s,%s,%s,99) ON CONFLICT(firma_id,miesiac) DO UPDATE '
                         'SET etap=EXCLUDED.etap,etap_kadry=EXCLUDED.etap_kadry',
                         (self.company, month, stage, payroll_stage))

    def run_job(self, *, day=DAY, apply=True):
        return run_import(self.config, apply=apply, today=day, fetch=self.fetch)

    def count(self, month='2026-11'):
        with get_conn() as conn:
            return conn.execute('SELECT ilosc_dok FROM statusy_miesieczne WHERE firma_id=%s AND miesiac=%s',
                                (self.company, month)).fetchone()[0]

    def test_only_accounting_sent_stage_can_contact_source(self):
        for stage in ('Brak', 'Zatwierdzone', 'Zaksięgowane'):
            self.set_stage('2026-11', stage, 'Wysłane wyniki')
            result = self.run_job()[0]
            self.assertEqual(result['mode'], 'skipped')
            self.fetch.assert_not_called()
            self.assertEqual(self.count(), 99)
        with get_conn() as conn:
            conn.execute('DELETE FROM statusy_miesieczne WHERE firma_id=%s', (self.company,))
        self.assertEqual(self.run_job()[0]['mode'], 'skipped')
        self.fetch.assert_not_called()

    def test_success_is_checked_once_per_warsaw_day_and_again_the_next_day(self):
        self.assertEqual(self.run_job()[0]['counts'], {'2026-11': 155})
        self.assertEqual(self.run_job()[0]['mode'], 'skipped')
        self.assertEqual(self.fetch.call_count, 1)
        self.assertEqual(self.run_job(day=date(2026, 11, 10))[0]['counts'], {'2026-11': 155})
        self.assertEqual(self.run_job(day=date(2026, 11, 10))[0]['mode'], 'skipped')
        self.assertEqual(self.fetch.call_count, 2)

    def test_preview_respects_stage_but_does_not_consume_daily_check(self):
        self.assertEqual(self.run_job(apply=False)[0]['counts'], {'2026-11': 155})
        self.assertEqual(self.count(), 99)
        self.assertEqual(self.run_job()[0]['counts'], {'2026-11': 155})
        self.assertEqual(self.fetch.call_count, 2)

    def test_ineligible_gaps_are_not_queried(self):
        self.config['months_back'] = 3
        self.set_stage('2026-09', 'Wysłane wyniki')
        self.set_stage('2026-10', 'Zaksięgowane')
        result = self.run_job()[0]
        self.assertEqual(list(result['counts']), ['2026-09', '2026-11'])
        self.assertEqual([call.args[2] for call in self.fetch.call_args_list], [['2026-09'], ['2026-11']])
        self.assertEqual(self.count('2026-10'), 99)

    def test_reopened_stage_during_source_read_prevents_write(self):
        original = self.fetch.side_effect
        def fetch(*args):
            self.set_stage('2026-11', 'Zaksięgowane')
            return original(*args)
        self.fetch.side_effect = fetch
        self.assertEqual(self.run_job()[0]['mode'], 'skipped')
        self.assertEqual(self.count(), 99)
        with get_conn() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM app_settings WHERE klucz LIKE 'enova_document_count_checked_%'").fetchone()[0], 0)

    def test_failed_source_can_retry_without_daily_marker(self):
        original = self.fetch.side_effect
        self.fetch.side_effect = RuntimeError('source offline')
        self.assertFalse(self.run_job()[0]['ok'])
        self.assertEqual(self.count(), 99)
        self.fetch.side_effect = original
        self.assertTrue(self.run_job()[0]['ok'])
        self.assertEqual(self.count(), 155)

    def test_global_rule_company_override_reset_and_same_day_rule_change(self):
        save_rules(10, {str(self.company): 3}, actor_id=1)
        self.assertEqual(self.run_job()[0]['counts']['2026-11'], 155)
        save_rules(10, {}, actor_id=1)
        self.assertEqual(self.run_job()[0]['counts']['2026-11'], 111)
        self.assertEqual(self.run_job()[0]['mode'], 'skipped')
        self.assertEqual(effective_divisor(load_rules(), self.company), 10)

    def test_non_admin_cannot_save_global_or_company_rules(self):
        employee = self.make_employee()
        with self.assertRaises(PermissionError):
            save_rules(10, {}, actor_id=employee)
        self.assertEqual(load_rules()['bank_divisor'], 3)

    def test_settings_page_saves_global_default_and_company_exception(self):
        from app.admin.settings.settings_ui import GlobalSettingsPage
        settings = GlobalSettingsPage(1, True)
        self.addCleanup(settings.close)
        row = next(i for i in range(settings.nav.count())
                   if settings.nav.item(i).text().strip() == 'Enova — liczba dokumentów')
        settings.nav.setCurrentRow(row)
        page = settings.stack.currentWidget().widget()
        page.default_divisor.setValue(3)
        page.editors[self.company].setValue(10)
        with patch('app.admin.settings.enova_count_settings.QMessageBox.information'):
            page._save()
        rules = load_rules()
        self.assertEqual(rules['bank_divisor'], 3)
        self.assertEqual(effective_divisor(rules, self.company), 10)
        page.editors[self.company].setValue(0)
        with patch('app.admin.settings.enova_count_settings.QMessageBox.information'):
            page._save()
        self.assertEqual(effective_divisor(load_rules(), self.company), 3)
