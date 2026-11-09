import threading
import unittest
import csv
import tempfile
from pathlib import Path
from unittest import mock

from PyQt6.QtWidgets import QApplication, QMessageBox

from app.email import bulk_mail
from app.email.bulk_mail import BulkMailPlan, BulkMailSender, client_addresses, filter_companies, send_bulk_batch
from app.email.bulk_mail_dialog import BulkMailDialog
from tests.base import DbTestCase
from tests.test_review_optimizations import wait_until

_app = QApplication.instance() or QApplication(['bulk-mail-tests'])
COMPANIES = [
    {'id': 1, 'nazwa': 'JDG', 'adres_email': 'first@example.com', 'forma_dzialalnosci': 'JDG',
     'typ_rozliczenia': 'Miesięczne', 'forma_opodatkowania': 'Ryczałt', 'status': 'Aktywne'},
    {'id': 2, 'nazwa': 'Spółka', 'adres_email': 'second@example.com', 'forma_dzialalnosci': 'Spółka z o.o.',
     'typ_rozliczenia': 'Kwartalne', 'forma_opodatkowania': 'CIT', 'status': 'Aktywne'},
    {'id': 3, 'nazwa': 'Bez maila', 'adres_email': '', 'forma_dzialalnosci': 'JDG', 'status': 'Aktywne'},
    {'id': 4, 'nazwa': 'Nieaktywna', 'adres_email': 'old@example.com', 'status': 'Zakończone'},
]


def plan(count=5, **kwargs):
    return BulkMailPlan(1, 10, 'office@example.com', 'Biuro zamknięte', 'Treść komunikatu',
                        tuple((i, (f'client{i}@example.com',)) for i in range(count)), **kwargs)


class BulkMailTests(unittest.TestCase):
    def test_addresses_validate_and_deduplicate(self):
        self.assertEqual(client_addresses('Jan <a@example.com>; A@example.com, b@example.com'),
                         ('a@example.com', 'b@example.com'))
        for value in ('', 'bad', 'a@example.com, bad', 'a@example.com\r\nBcc: x@example.com'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                client_addresses(value)

    def test_company_filters_combine_activity_settlement_tax_and_status(self):
        rows = filter_companies(COMPANIES, activity='JDG', settlement='Miesięczne', tax='Ryczałt', query='FIRST@')
        self.assertEqual([row['id'] for row in rows], [1])
        self.assertEqual([row['id'] for row in filter_companies(COMPANIES, activity='Spółka z o.o.')], [2])
        self.assertEqual(len(filter_companies(COMPANIES, active_only=False)), 4)

    def test_plan_deduplicates_shared_addresses_and_excludes_sender(self):
        value = BulkMailPlan(1, 10, 'office@example.com', 'Subject', 'Body',
                            ((1, ('a@example.com', 'office@example.com')), (2, ('A@example.com',))))
        self.assertEqual(value.recipients, ('a@example.com',))
        for kwargs in ({'batch_size': 0}, {'pause_seconds': 0}, {'batch_size': 51}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                plan(**kwargs)

    def test_batch_uses_bcc_and_rechecks_the_confirmed_company_contacts(self):
        value = plan(2)
        rows = [{'id': i, 'adres_email': f'client{i}@example.com'} for i in range(2)]
        with mock.patch.object(bulk_mail, 'allowed_tabs', return_value=['E-mail']), \
                mock.patch.object(bulk_mail, 'get_email_accounts_for_employee', return_value=[{'id': 10, 'email': value.sender}]), \
                mock.patch.object(bulk_mail, 'list_mailing_companies', return_value=rows), \
                mock.patch.object(bulk_mail, 'send_email_smtp', return_value=(True, 'OK')) as smtp:
            self.assertTrue(send_bulk_batch(value, value.recipients)['ok'])
            smtp.assert_called_once_with(10, [value.sender], value.subject, value.body,
                                         bcc=list(value.recipients), save_copy=True, attachments=[])
            rows[0]['adres_email'] = 'changed@example.com'
            self.assertTrue(send_bulk_batch(value, value.recipients)['blocked'])
            self.assertEqual(smtp.call_count, 1)

    def test_lost_account_access_blocks_smtp(self):
        with mock.patch.object(bulk_mail, 'allowed_tabs', return_value=['E-mail']), \
                mock.patch.object(bulk_mail, 'get_email_accounts_for_employee', return_value=[]), \
                mock.patch.object(bulk_mail, 'send_email_smtp') as smtp:
            result = send_bulk_batch(plan(), ('client0@example.com',))
        self.assertTrue(result['blocked'])
        smtp.assert_not_called()

    def test_client_addresses_are_not_exposed_in_the_message_headers(self):
        from app.email.email_actions import prepare_message
        message = prepare_message('office@example.com', 'Biuro', ['office@example.com'],
                                   'Subject', 'Body', bcc=['private@example.com'])
        self.assertEqual(message['To'], 'office@example.com')
        self.assertIsNone(message['Bcc'])
        self.assertNotIn('private@example.com', message.as_string())

    def sender(self):
        sender = BulkMailSender()
        self.addCleanup(sender.cancel)
        return sender

    def test_batches_are_paced_and_successful_recipients_are_not_resent(self):
        sender = self.sender()
        result = []
        sender.finished.connect(result.append)
        with mock.patch.object(sender._jobs, 'submit', side_effect=lambda work, done, fail: done(work())), \
                mock.patch.object(bulk_mail, 'send_bulk_batch', return_value={'ok': True, 'blocked': False, 'info': 'OK'}) as send:
            sender.start(plan(batch_size=2))
            self.assertEqual(send.call_args.args[1], ('client0@example.com', 'client1@example.com'))
            self.assertGreater(sender._timer.remainingTime(), 59000)
            sender._timer.stop()
            sender._next()
            self.assertEqual(send.call_args.args[1], ('client2@example.com', 'client3@example.com'))
            sender._timer.stop()
            sender._next()
        self.assertEqual(send.call_count, 3)
        self.assertFalse(sender.running)
        self.assertFalse(sender._timer.isActive())
        self.assertEqual(result[0]['remaining'], 0)

    def test_uncertain_result_stops_without_automatic_retry(self):
        sender = self.sender()
        result = []
        sender.finished.connect(result.append)
        with mock.patch.object(sender._jobs, 'submit', side_effect=lambda work, done, fail: done(work())), \
                mock.patch.object(bulk_mail, 'send_bulk_batch', return_value={'ok': False, 'blocked': False, 'info': 'Connection lost'}) as send:
            sender.start(plan(batch_size=2))
            sender._next()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(result[0]['remaining'], 3)
        self.assertEqual(result[0]['entries'][0]['status'], 'uncertain')
        self.assertFalse(sender._timer.isActive())

    def test_cancel_between_batches_stops_the_timer_and_keeps_unsent_recipients(self):
        sender = self.sender()
        results = []
        sender.finished.connect(results.append)
        with mock.patch.object(sender._jobs, 'submit', side_effect=lambda work, done, fail: done(work())), \
                mock.patch.object(bulk_mail, 'send_bulk_batch', return_value={'ok': True, 'blocked': False, 'info': 'OK'}) as send:
            sender.start(plan(batch_size=2))
            sender.cancel()
            sender._next()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(results[0]['remaining'], 3)
        self.assertFalse(sender._timer.isActive())

    def test_cancel_waits_for_inflight_write_and_skips_remaining_batches(self):
        sender = self.sender()
        entered, release = threading.Event(), threading.Event()
        result, threads = [], []
        self.addCleanup(release.set)
        sender.finished.connect(result.append)
        def send(*_args):
            threads.append(threading.get_ident())
            entered.set()
            release.wait(3)
            return {'ok': True, 'blocked': False, 'info': 'OK'}
        with mock.patch.object(bulk_mail, 'send_bulk_batch', side_effect=send) as send_mock:
            sender.start(plan(batch_size=2))
            self.assertTrue(entered.wait(1))
            sender.cancel()
            self.assertTrue(sender.running)
            release.set()
            self.assertTrue(wait_until(lambda: not sender.running))
        self.assertNotEqual(threads[0], threading.get_ident())
        self.assertEqual(send_mock.call_count, 1)
        self.assertEqual(result[0]['remaining'], 3)
        self.assertTrue(result[0]['stopped'])

    def dialog(self):
        with mock.patch('app.email.bulk_mail_dialog.list_mailing_companies', return_value=COMPANIES):
            dialog = BulkMailDialog(1, [{'id': 10, 'email': 'office@example.com'}], 10)
        self.addCleanup(dialog.close)
        return dialog

    def test_dialog_filters_drop_hidden_selections_and_exclude_missing_addresses(self):
        dialog = self.dialog()
        dialog._select_visible()
        self.assertEqual(dialog._selected, {1, 2})
        dialog.activity.setCurrentIndex(dialog.activity.findData('Spółka z o.o.'))
        self.assertEqual(dialog._selected, {2})
        self.assertEqual(dialog.table.rowCount(), 1)
        dialog.settlement.setCurrentIndex(dialog.settlement.findData('Miesięczne'))
        self.assertEqual(dialog._selected, set())
        self.assertEqual(dialog.table.rowCount(), 0)

    def test_dialog_requires_confirmation_and_freezes_the_accepted_plan(self):
        dialog = self.dialog()
        dialog._select_visible()
        dialog.subject.setText('Notice')
        dialog.body.setPlainText('Biuro zamknięte')
        with mock.patch.object(QMessageBox, 'exec', return_value=QMessageBox.StandardButton.Cancel), \
                mock.patch.object(dialog.sender, 'start') as start:
            dialog._review_send()
            start.assert_not_called()
        with mock.patch.object(QMessageBox, 'exec', return_value=QMessageBox.StandardButton.Yes), \
                mock.patch.object(dialog.sender, 'start') as start:
            dialog._review_send()
        accepted = start.call_args.args[0]
        self.assertEqual(accepted.recipients, ('first@example.com', 'second@example.com'))
        self.assertFalse(dialog.body.isEnabled())
        self.assertFalse(dialog.send_button.isEnabled())

    def test_csv_report_distinguishes_uncertain_and_unsent_addresses(self):
        dialog = self.dialog()
        dialog.sender.plan = plan()
        dialog._finished({'entries': [{'recipients': ('client0@example.com',), 'status': 'uncertain', 'info': '=2+2'}],
                          'remaining': 4, 'total': 5, 'stopped': False})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report.csv'
            with mock.patch('app.email.bulk_mail_dialog.QFileDialog.getSaveFileName', return_value=(str(path), 'CSV')):
                dialog._export_report()
            with path.open(encoding='utf-8-sig', newline='') as stream:
                rows = list(csv.reader(stream, delimiter=';'))
        self.assertEqual(rows[1], ['client0@example.com', 'Wynik niepotwierdzony', "'=2+2"])
        self.assertEqual(rows[-1][1], 'Niewysłano')


class MailingCompanyQueryTests(DbTestCase):
    def test_excel_contact_address_is_available_for_bulk_mail(self):
        from openpyxl import Workbook
        from app.utils.importers import import_companies_from_excel_file
        from app.db.dao_companies import list_mailing_companies
        from app.db.dao_conn import get_conn
        workbook = Workbook()
        workbook.active.append(['NIP', 'Nazwa Firmy', 'Nazwa skrócona', 'Adres email', 'Osoba kontaktowa',
                                 'Forma działalności', 'Forma opodatkowania'])
        workbook.active.append(['8522605060', 'Imported company', 'Imported', 'client@example.com', 'Jan Kowalski',
                                 'Spółka z o.o.', 'CIT'])
        path = str(Path(self._tmp) / 'contacts.xlsx')
        workbook.save(path)
        result = import_companies_from_excel_file(path)
        self.assertEqual(result['created'], 1)
        row = next(row for row in list_mailing_companies(1) if row['nazwa'] == 'Imported company')
        self.assertEqual(row['adres_email'], 'client@example.com')
        with get_conn() as connection:
            contact = connection.execute('SELECT email FROM company_contacts WHERE firma_id = %s', (row['id'],)).fetchone()
        self.assertEqual(contact['email'], 'client@example.com')

    def test_contact_query_returns_activity_and_settlement_fields_and_no_anonymous_rows(self):
        from app.db.dao_companies import list_mailing_companies
        company = self.make_company('Spółka', adres_email='office@example.com', forma_dzialalnosci='Spółka z o.o.',
                                    typ_rozliczenia='Kwartalne', forma_opodatkowania='CIT')
        row = next(row for row in list_mailing_companies(1) if row['id'] == company)
        self.assertEqual(row['adres_email'], 'office@example.com')
        self.assertEqual(row['forma_dzialalnosci'], 'Spółka z o.o.')
        self.assertEqual(row['typ_rozliczenia'], 'Kwartalne')
        self.assertEqual(list_mailing_companies(None), [])
