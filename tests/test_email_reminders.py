from copy import deepcopy
from datetime import date, datetime
from unittest import TestCase
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.services.email_reminders import DEFAULTS, polish_holiday, scheduled_slot, run_reminders, save_settings
from app.db.dao_conn import get_conn
from tests.base import DbTestCase


def at(day, hour=9):
    return datetime.fromisoformat(f'{day}T{hour:02d}:00:00').replace(tzinfo=ZoneInfo('Europe/Warsaw'))


class ReminderScheduleTests(TestCase):
    def setUp(self):
        self.rule = deepcopy(DEFAULTS['payments'])

    def test_due_date_followups_and_repeat(self):
        due = date(2026, 11, 9)
        for today, expected in [('2026-11-09', '2026-11-09'), ('2026-11-10', '2026-11-09'),
                                ('2026-11-12', '2026-11-12'), ('2026-11-16', '2026-11-16')]:
            self.assertEqual(scheduled_slot(due, self.rule, at(today)).isoformat(), expected)
        self.assertIsNone(scheduled_slot(due, self.rule, at('2026-11-09', 8)))

    def test_holidays_and_weekends_never_send_even_for_old_slots(self):
        due = date(2026, 11, 9)
        for today in ('2026-11-11', '2026-11-14', '2026-11-15'):
            self.assertIsNone(scheduled_slot(due, self.rule, at(today)))
        self.assertIsNone(scheduled_slot(due, self.rule, at('2026-11-16', 8)))
        self.rule['holidays'] = True
        self.assertEqual(scheduled_slot(due, self.rule, at('2026-11-11')), date(2026, 11, 11))

    def test_shift_merges_slots_and_catches_up_once(self):
        self.rule.update(offsets=[0, 1], repeat=0)
        due = date(2026, 11, 14)
        self.assertEqual(scheduled_slot(due, self.rule, at('2026-11-16')), date(2026, 11, 16))
        self.assertEqual(scheduled_slot(due, self.rule, at('2026-11-20')), date(2026, 11, 16))

    def test_polish_movable_holidays_and_christmas_eve(self):
        for day in ('2026-04-05', '2026-04-06', '2026-05-24', '2026-06-04', '2026-12-24'):
            self.assertTrue(polish_holiday(date.fromisoformat(day)), day)
        self.assertFalse(polish_holiday(date(2024, 12, 24)))
        self.assertFalse(polish_holiday(date(2026, 11, 9)))


class ReminderDeliveryTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.settings = deepcopy(DEFAULTS)
        self.company = self.make_company(adres_email='company@example.com', email_faktury='billing@example.com', ksiegowa_id=1)
        self.sender_patch = patch('app.email.email_actions.send_email_smtp', return_value=(True, 'OK'))
        self.sender = self.sender_patch.start()
        self.addCleanup(self.sender_patch.stop)
        account_patch = patch('app.utils.system_mailbox.get_system_account', return_value={'id': 999, 'aktywne': 1})
        account_patch.start()
        self.addCleanup(account_patch.stop)

    def enable(self, kind):
        self.settings[kind]['enabled'] = True
        save_settings(self.settings)

    def invoice(self, **overrides):
        values = {'firma_id': self.company, 'reczna': 1, 'miesiac': '2026-10', 'kwota_netto': 100,
                  'kwota_brutto': 123, 'pozycje_json': '[]', 'status': 'wystawiona', 'numer': 'FV/test',
                  'termin_platnosci': '2026-11-09'}
        values.update(overrides)
        with get_conn() as conn:
            keys = ','.join(values)
            placeholders = ','.join(['%s'] * len(values))
            return conn.execute(f'INSERT INTO faktury ({keys}) VALUES ({placeholders}) RETURNING id', tuple(values.values())).fetchone()[0]

    def test_documents_missing_status_are_reminded_once_and_complete_stops_followups(self):
        self.enable('documents')
        self.assertEqual(run_reminders(at('2026-11-12')), 1)  # 11th holiday -> 12th
        self.assertEqual(run_reminders(at('2026-11-12', 10)), 0)
        with get_conn() as conn:
            conn.execute('INSERT INTO statusy_miesieczne (firma_id, miesiac, dokumenty_dostarczone) VALUES (%s, %s, 1)', (self.company, '2026-10'))
        self.assertEqual(run_reminders(at('2026-11-16')), 0)
        self.assertEqual(self.sender.call_count, 1)

    def test_payment_uses_remaining_amount_and_stops_after_settlement(self):
        invoice = self.invoice()
        with get_conn() as conn:
            conn.execute("INSERT INTO faktury_platnosci (faktura_id, kwota, data_zaplaty, sposob) VALUES (%s, 23, '2026-11-08', 'gotowka')", (invoice,))
        self.enable('payments')
        self.assertEqual(run_reminders(at('2026-11-09')), 1)
        self.assertIn('100.00 PLN', self.sender.call_args.args[3])
        self.assertEqual(run_reminders(at('2026-11-09', 10)), 0)
        with get_conn() as conn:
            conn.execute("INSERT INTO faktury_platnosci (faktura_id, kwota, data_zaplaty, sposob) VALUES (%s, 100, '2026-11-10', 'gotowka')", (invoice,))
        self.assertEqual(run_reminders(at('2026-11-12')), 0)

    def test_drafts_cancelled_proformas_credits_and_invalid_due_dates_are_skipped(self):
        self.invoice(status='robocza')
        self.invoice(cancelled_at=at('2026-11-08'))
        self.invoice(rodzaj='proforma')
        self.invoice(rodzaj='korekta', kwota_brutto=-20)
        self.invoice(termin_platnosci='bad date')
        self.enable('payments')
        self.assertEqual(run_reminders(at('2026-11-09')), 0)
        self.sender.assert_not_called()

    def test_failure_is_recorded_and_not_retried_for_same_slot(self):
        self.invoice()
        self.enable('payments')
        self.sender.return_value = False, 'SMTP timeout'
        self.assertEqual(run_reminders(at('2026-11-09')), 0)
        self.assertEqual(run_reminders(at('2026-11-09', 10)), 0)
        self.assertEqual(self.sender.call_count, 1)
        with get_conn() as conn:
            row = conn.execute('SELECT status, error FROM email_reminder_log').fetchone()
        self.assertEqual(tuple(row), ('failed', 'SMTP timeout'))

    def test_disabled_defaults_do_not_send(self):
        self.invoice()
        self.assertEqual(run_reminders(at('2026-11-09')), 0)
        self.sender.assert_not_called()

    def test_worker_documents_use_contact_table_and_do_not_send_to_ambiguous_contacts(self):
        from app.db.dao_companies import save_contact
        with get_conn() as conn:
            conn.execute("UPDATE firmy SET adres_email='' WHERE id=%s",(self.company,))
        save_contact(self.company,{'email':'current@example.com'})
        self.enable('documents')
        self.assertEqual(run_reminders(at('2026-11-12')),1)
        self.assertEqual(self.sender.call_args.args[1],['current@example.com'])
        save_contact(self.company,{'email':'other@example.com'})
        self.assertEqual(run_reminders(at('2026-11-16')),0)
        self.assertEqual(self.sender.call_count,1)

    def test_unpaid_invoice_followups_use_selected_template(self):
        self.invoice()
        self.settings['templates'].append({'name': 'Custom', 'subject': 'Prosimy o płatność {numer}', 'body': '{firma} — {pozostalo}'})
        self.settings['payments']['template'] = 'Custom'
        self.enable('payments')
        for day in ('2026-11-09', '2026-11-12', '2026-11-16'):
            self.assertEqual(run_reminders(at(day)), 1)
            self.assertEqual(run_reminders(at(day, 10)), 0)
        self.assertEqual(self.sender.call_count, 3)
        self.assertEqual(self.sender.call_args.args[2], 'Prosimy o płatność FV/test')

    def test_settings_editor_saves_template_and_schedule(self):
        from app.admin.settings.reminder_settings import ReminderSettings
        from app.services.email_reminders import load_settings
        page = ReminderSettings()
        self.addCleanup(page.close)
        rule = page.rules['payments']
        rule.offsets.setText('0, 3')
        rule.repeat.setValue(7)
        page.templates.widget(1).subject.setText('Zapłata za {numer}')
        with patch('app.admin.settings.reminder_settings.QMessageBox.information'):
            page._save()
        saved = load_settings()
        self.assertEqual(saved['payments']['offsets'], [0, 3])
        self.assertEqual(saved['payments']['repeat'], 7)
        self.assertEqual(saved['templates'][1]['subject'], 'Zapłata za {numer}')

    def test_invalid_template_or_empty_weekdays_cannot_be_saved(self):
        self.settings['templates'][0]['body'] = '{unsupported}'
        with self.assertRaises(ValueError):
            save_settings(self.settings)
        self.settings = deepcopy(DEFAULTS)
        self.settings['payments']['weekdays'] = []
        with self.assertRaises(ValueError):
            save_settings(self.settings)

    def test_concurrent_workstations_claim_only_one_delivery(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from app.config import schema_context
        self.invoice()
        self.enable('payments')
        started, release = Event(), Event()
        def send(*args):
            started.set()
            self.assertTrue(release.wait(5))
            return True, 'OK'
        def run():
            with schema_context(self.schema_name):
                return run_reminders(at('2026-11-09'))
        self.sender.side_effect = send
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(run)
            try:
                self.assertTrue(started.wait(5))
                self.assertEqual(pool.submit(run).result(timeout=5), 0)
            finally:
                release.set()
            self.assertEqual(first.result(timeout=5), 1)
        self.assertEqual(self.sender.call_count, 1)

    def test_alembic_upgrade_is_repeatable_and_current(self):
        from app.db.schema import upgrade_database, schema_is_current
        upgrade_database()
        upgrade_database()
        self.assertTrue(schema_is_current())

    def test_document_reminders_skip_payroll_only_and_unassigned_companies(self):
        self.make_company('Kadry only', adres_email='payroll@example.com', kadrowa_id=1)
        self.make_company('Unassigned', adres_email='unassigned@example.com')
        self.enable('documents')
        self.assertEqual(run_reminders(at('2026-11-12')), 1)
        self.assertEqual(self.sender.call_args.args[1], ['company@example.com'])

    def test_accounting_and_payroll_client_still_receives_accounting_reminder(self):
        with get_conn() as conn:
            conn.execute('UPDATE firmy SET kadrowa_id=1 WHERE id=%s', (self.company,))
        self.enable('documents')
        self.assertEqual(run_reminders(at('2026-11-12')), 1)
        self.assertEqual(self.sender.call_args.args[1], ['company@example.com'])

    def test_payment_reminders_still_include_payroll_only_client_invoices(self):
        with get_conn() as conn:
            conn.execute('UPDATE firmy SET ksiegowa_id=NULL,kadrowa_id=1 WHERE id=%s', (self.company,))
        self.invoice()
        self.enable('payments')
        self.assertEqual(run_reminders(at('2026-11-09')), 1)

    def test_accounting_assignment_removed_mid_batch_is_rechecked_before_sending(self):
        self.make_company('Second accounting', adres_email='second@example.com', ksiegowa_id=1)
        self.enable('documents')
        def send(*args):
            with get_conn() as conn:
                conn.execute('UPDATE firmy SET ksiegowa_id=NULL WHERE adres_email<>%s', (args[1][0],))
            return True, 'OK'
        self.sender.side_effect = send
        self.assertEqual(run_reminders(at('2026-11-12')), 1)
        self.assertEqual(self.sender.call_count, 1)
