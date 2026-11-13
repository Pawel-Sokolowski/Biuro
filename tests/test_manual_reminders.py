from copy import deepcopy
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from tests.base import DbTestCase
from app.db.dao_conn import get_conn
from app.services.manual_reminders import prepare,send
from app.services.email_reminders import DEFAULTS,save_settings,run_reminders


class ManualReminderTests(DbTestCase):
    def setUp(self):
        super().setUp()
        with get_conn() as conn:
            self.admin = conn.execute('SELECT id FROM pracownicy WHERE is_admin=1').fetchone()[0]
        self.company = self.make_company(adres_email='client@example.com',email_faktury='billing@example.com',ksiegowa_id=self.admin)
        self.clock = datetime(2026,11,12,10,tzinfo=ZoneInfo('Europe/Warsaw'))
        clock = patch('app.services.manual_reminders.datetime')
        clock.start().now.return_value = self.clock
        self.addCleanup(clock.stop)
        accounts = patch('app.services.manual_reminders.get_email_accounts_for_employee',return_value=[{'id':99,'email':'office@example.com'}])
        self.accounts = accounts.start()
        self.addCleanup(accounts.stop)
        sender = patch('app.email.email_actions.send_email_smtp',return_value=(True,'OK'))
        self.sender = sender.start()
        self.addCleanup(sender.stop)

    def message(self,plan):
        return {'account_id':99,'to':plan['recipients'],'subject':plan['subject'],'body':plan['body']}

    def invoice(self):
        with get_conn() as conn:
            return conn.execute('''INSERT INTO faktury(firma_id,reczna,miesiac,kwota_netto,kwota_brutto,
                pozycje_json,status,numer,termin_platnosci)
                VALUES (%s,1,'2026-10',100,123,'[]','wystawiona','FV/test','2026-11-09') RETURNING id''',
                (self.company,)).fetchone()[0]

    def test_documents_use_selected_month_and_manual_send_does_not_require_enabled_schedule(self):
        plan = prepare('documents',self.company,self.admin,'2025-02')
        self.assertEqual(plan['context']['miesiac'],'2025-02')
        self.assertEqual(plan['context']['termin'],'2025-03-10')
        self.assertEqual(plan['recipients'],['client@example.com'])
        self.assertTrue(send(plan,self.admin,self.message(plan))['ok'])
        self.assertTrue(self.sender.call_args.kwargs['save_copy'])
        with get_conn() as conn:
            row = conn.execute('SELECT kind,target,status FROM email_reminder_log').fetchone()
            self.assertEqual(tuple(row),('manual_documents',f'{self.company}:2025-02','sent'))
            self.assertEqual(conn.execute("SELECT uzytkownik_id FROM audit_log WHERE akcja='manual_send'").fetchone()[0],self.admin)

    def test_completion_after_preview_blocks_send(self):
        plan = prepare('documents',self.company,self.admin,'2026-10')
        with get_conn() as conn:
            conn.execute('INSERT INTO statusy_miesieczne(firma_id,miesiac,dokumenty_dostarczone) VALUES (%s,%s,1)',(self.company,'2026-10'))
        with self.assertRaises(ValueError):
            send(plan,self.admin,self.message(plan))
        self.sender.assert_not_called()

    def test_payment_preview_uses_balance_and_rechecks_changes_before_send(self):
        invoice = self.invoice()
        with get_conn() as conn:
            conn.execute("INSERT INTO faktury_platnosci(faktura_id,kwota,sposob,data_zaplaty) VALUES (%s,23,'bank','2026-11-10')",(invoice,))
        plan = prepare('payments',invoice,self.admin)
        self.assertEqual(plan['context']['pozostalo'],'100.00')
        self.assertEqual(plan['recipients'],['billing@example.com'])
        with get_conn() as conn:
            conn.execute("INSERT INTO faktury_platnosci(faktura_id,kwota,sposob,data_zaplaty) VALUES (%s,10,'bank','2026-11-11')",(invoice,))
        with self.assertRaises(ValueError):
            send(plan,self.admin,self.message(plan))
        self.sender.assert_not_called()

    def test_lost_module_or_sender_access_and_changed_contact_prevent_delivery(self):
        with patch('app.services.manual_reminders.allowed_tabs',return_value=[]),self.assertRaises(PermissionError):
            prepare('documents',self.company,self.admin,'2026-10')
        with patch('app.services.manual_reminders.can_manage_invoices',return_value=False),self.assertRaises(PermissionError):
            prepare('payments',self.invoice(),self.admin)
        plan = prepare('documents',self.company,self.admin,'2026-10')
        self.accounts.return_value = []
        with self.assertRaises(PermissionError):
            send(plan,self.admin,self.message(plan))
        self.accounts.return_value = [{'id':99}]
        with get_conn() as conn:
            conn.execute("UPDATE firmy SET adres_email='changed@example.com' WHERE id=%s",(self.company,))
        with self.assertRaises(ValueError):
            send(plan,self.admin,self.message(plan))
        self.sender.assert_not_called()

    def test_failed_delivery_is_not_retried_and_worker_skips_manual_attempt_today(self):
        settings = deepcopy(DEFAULTS)
        settings['documents']['enabled'] = True
        save_settings(settings)
        plan = prepare('documents',self.company,self.admin,'2026-10')
        self.sender.return_value = False,'Wynik SMTP nieznany'
        self.assertFalse(send(plan,self.admin,self.message(plan))['ok'])
        with self.assertRaises(ValueError):
            send(plan,self.admin,self.message(plan))
        with patch('app.utils.system_mailbox.get_system_account',return_value={'id':99,'aktywne':1}):
            self.assertEqual(run_reminders(self.clock),0)
        self.assertEqual(self.sender.call_count,1)
        with get_conn() as conn:
            self.assertEqual(conn.execute('SELECT status FROM email_reminder_log').fetchone()[0],'failed')

    def test_parallel_workstations_claim_only_one_manual_send(self):
        from concurrent.futures import ThreadPoolExecutor
        from app.config import get_schema,schema_context
        plan = prepare('documents',self.company,self.admin,'2026-10')
        schema = get_schema()
        def attempt():
            with schema_context(schema):
                try:
                    return send(plan,self.admin,self.message(plan))['ok']
                except ValueError:
                    return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(attempt),pool.submit(attempt)]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(sorted(results),[False,True])
        self.assertEqual(self.sender.call_count,1)
