"""PostgreSQL checks for delivery reservations, notification links and first sync."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import threading
from unittest.mock import Mock, patch
from app.db.dao_conn import get_conn
from app.db.dao_app_settings import get_setting, set_setting
from app.db.dao_employees import add_email_account_for_employee
from app.db.dao_invoices import create_manual_invoice, get_invoice_by_id, mark_exported, update_invoice_draft
from app.email.email_storage import (save_folder,save_message_headers,get_folder_id,get_cached_messages,
    load_raw_message,set_employee_assignment,count_unread_assigned_by_employee,resolve_assigned_message,mark_deleted)
from app.email import email_sync
from app.invoices.invoice_delivery import delivery_plan,queue_deliveries,deliver_if_queued
from app.notifications import list_user_notifications
from tests.base import DbTestCase


class DeliveryAndSyncDbTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.company=self.make_company('Client',email_faktury='invoices@client.com',adres_email='owner@client.com')
        self.account=add_email_account_for_employee(1,'office@example.com','imap.invalid',993,'smtp.invalid',465,'test-only')
        conn=get_conn()
        try:
            conn.execute('INSERT INTO pracownik_konto_email(pracownik_id,konto_id) VALUES (%s,%s)',(1,self.account))
            conn.commit()
        finally:conn.close()
        self.positions=[{'nazwa':'Usługa','ilosc':1,'cena_netto':100,'vat':23}]
        self.invoice=create_manual_invoice('firma',self.company,self.positions,'2026-11-01','2026-11-01','2026-11-10')
        update_invoice_draft(self.invoice,self.positions,numer='FV/1',data_wystawienia='2026-11-01')
        mark_exported([self.invoice],1)
        conn=get_conn()
        try:
            conn.execute("UPDATE faktury SET ksef_status='przyjeta',ksef_numer='KSEF-ACCEPTED' WHERE id=%s",(self.invoice,))
            conn.commit()
        finally:conn.close()

    def test_invoice_recipient_persists_and_delivery_reservation_allows_only_one_smtp_call(self):
        plans=delivery_plan([self.invoice],1)
        self.assertEqual(plans[0]['recipients'],['invoices@client.com'])
        queue_deliveries(plans,1,self.account)
        queue_deliveries(plans,1,self.account)  # idempotent queueing
        barrier=threading.Barrier(2)
        def render(*args):barrier.wait(5);return '/test/invoice.pdf'
        with patch('app.email.email_actions.get_smtp_config_for_account',return_value={'host':'smtp.invalid','user':'office@example.com'}), \
             patch('app.invoices.invoice_delivery.official_pdf',side_effect=render), \
             patch('app.email.email_actions.send_email_smtp',return_value=(True,'OK')) as smtp:
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda _:deliver_if_queued(self.invoice,1,'/test/invoice.xml'),range(2)))
        smtp.assert_called_once()
        self.assertEqual(get_invoice_by_id(self.invoice)['invoice_email_status'],'sent')
        self.assertTrue(any(result['status']=='sent' for result in results))
        with self.assertRaises(ValueError):queue_deliveries(plans,1,self.account)

    def test_replaced_delivery_plan_cannot_be_claimed_by_stale_renderer(self):
        from app.invoices import invoice_delivery as delivery
        plans = delivery_plan([self.invoice], 1)
        queue_deliveries(plans, 1, self.account)
        other_account = add_email_account_for_employee(1, 'other@example.com', 'imap.invalid', 993, 'smtp.invalid', 465, 'test-only')
        conn = get_conn()
        try:
            conn.execute('INSERT INTO pracownik_konto_email(pracownik_id,konto_id) VALUES (%s,%s)', (1, other_account))
            conn.commit()
        finally:
            conn.close()
        for replacement_account, recipient in ((self.account, 'new@client.com'), (other_account, 'invoices@client.com')):
            with self.subTest(sender=replacement_account, recipient=recipient):
                conn = get_conn()
                try:
                    conn.execute("UPDATE faktury SET invoice_email_status='failed_before_send' WHERE id=%s", (self.invoice,))
                    conn.commit()
                finally:
                    conn.close()
                queue_deliveries(plans, 1, self.account)
                def replace_plan(*args):
                    conn = get_conn()
                    try:
                        conn.execute("UPDATE faktury SET invoice_email_status='failed_before_send' WHERE id=%s", (self.invoice,))
                        conn.commit()
                    finally:
                        conn.close()
                    queue_deliveries([{'id':self.invoice, 'number':'FV/1', 'recipients':[recipient]}], 1, replacement_account)
                    return '/test/invoice.pdf'
                with patch('app.email.email_actions.get_smtp_config_for_account', return_value={'host':'smtp.invalid','user':'office@example.com'}), patch.object(delivery, 'official_pdf', side_effect=replace_plan), patch('app.email.email_actions.send_email_smtp') as smtp:
                    result = deliver_if_queued(self.invoice, 1, '/test/invoice.xml')
                smtp.assert_not_called()
                self.assertEqual(result['status'], 'uncertain')
                self.assertEqual(get_invoice_by_id(self.invoice)['invoice_email_status'], 'queued')

    def test_stale_draft_read_cannot_edit_issued_invoice_or_allocations(self):
        from app.db import dao_invoices as dao
        draft = create_manual_invoice('firma', self.company, self.positions, '2026-11-01', '2026-11-01', '2026-11-10')
        original = get_invoice_by_id(draft)
        def issue_after_read(_):
            mark_exported([draft], 1)
            return original
        with patch.object(dao, 'get_invoice_by_id', side_effect=issue_after_read), patch.object(dao, '_sync_extra_service_allocations') as allocations, self.assertRaises(dao.InvoiceLockedError):
            update_invoice_draft(draft, [{**self.positions[0], 'cena_netto':999}], numer='CHANGED')
        allocations.assert_not_called()
        saved = get_invoice_by_id(draft)
        self.assertEqual(saved['kwota_netto'], original['kwota_netto'])
        self.assertEqual(saved['pozycje_json'], original['pozycje_json'])
        self.assertEqual(saved['eksportowana'], 1)

    def test_exemption_basis_survives_creation_edits_and_corrections(self):
        from app.db.dao_invoices import create_correction
        positions = [{**self.positions[0], 'vat':'zw'}]
        draft = create_manual_invoice('firma', self.company, positions, '2026-11-01', '2026-11-01', '2026-11-10', podstawa_zwolnienia='art. 113 ust. 1 ustawy o VAT')
        update_invoice_draft(draft, positions, numer='ZW/1')
        self.assertEqual(get_invoice_by_id(draft)['podstawa_zwolnienia'], 'art. 113 ust. 1 ustawy o VAT')
        update_invoice_draft(draft, positions, numer='ZW/1', podstawa_zwolnienia='art. 43 ustawy o VAT')
        mark_exported([draft], 1)
        correction = create_correction(draft, 'Zwrot')
        self.assertEqual(get_invoice_by_id(correction)['podstawa_zwolnienia'], 'art. 43 ustawy o VAT')

    def test_uncertain_delivery_is_persisted_and_not_retried(self):
        queue_deliveries(delivery_plan([self.invoice],1),1,self.account)
        with patch('app.email.email_actions.get_smtp_config_for_account',return_value={'host':'smtp.invalid','user':'office@example.com'}), \
             patch('app.invoices.invoice_delivery.official_pdf',return_value='/test/invoice.pdf'), \
             patch('app.email.email_actions.send_email_smtp',return_value=(False,'timeout')) as smtp:
            deliver_if_queued(self.invoice,1,'/test/invoice.xml')
            deliver_if_queued(self.invoice,1,'/test/invoice.xml')
        smtp.assert_called_once()
        self.assertEqual(get_invoice_by_id(self.invoice)['invoice_email_status'],'uncertain')

    def test_unread_assignments_are_deduplicated_and_notification_follows_moved_message(self):
        for name in ('INBOX','Clients'):
            save_folder(self.account,name,name,'.','')
            save_message_headers(self.account,get_folder_id(self.account,name),[self.header('1','<same@example.com>')])
        inbox=get_cached_messages(self.account,get_folder_id(self.account,'INBOX'))[0]
        set_employee_assignment([inbox['id']],1,True)
        self.assertEqual(count_unread_assigned_by_employee(self.account),{1:1})
        entries=[entry for entry in list_user_notifications(1) if entry['kind']=='email']
        self.assertEqual(len(entries),1)
        mark_deleted(self.account,'1',get_folder_id(self.account,'INBOX'))
        moved=resolve_assigned_message(1,entries[0]['target'])
        self.assertEqual(moved['server_name'],'Clients')
        other=self.make_employee()
        self.assertIsNone(resolve_assigned_message(other,entries[0]['target']))
        self.assertFalse([entry for entry in list_user_notifications(other) if entry['kind']=='email'])

    @staticmethod
    def header(uid,message_id,days=0):
        return {'uid':uid,'message_id':message_id,'sender':'sender@example.com','recipient':'office@example.com',
                'subject':'Message '+uid,'date':(datetime.now()-timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S'),
                'is_seen':False,'has_attachments':False}

    def test_initial_sync_downloads_full_two_year_bodies_in_every_folder(self):
        for name in ('INBOX','Clients'):save_folder(self.account,name,name,'.','')
        def headers(account,folder,**kwargs):
            self.assertEqual(kwargs['history_days'],730)
            return [self.header('1',f'<{folder}-new@example.com>',400),self.header('2',f'<{folder}-old@example.com>',800)]
        session=email_sync._Session(Mock(), self.account)
        session.conn.select.return_value=('OK',[b'1'])
        session.conn.response.return_value=('UIDVALIDITY',[None])
        session.conn.uid.side_effect=lambda *args:('OK',[(b'1 (UID 1 BODY[] {42}',b'From: sender@example.com\r\n\r\nFull body')])
        @contextmanager
        def shared(*args,**kwargs):yield session
        with patch.object(email_sync,'list_folders_from_imap_and_store',return_value=True), \
             patch.object(email_sync,'fetch_headers',side_effect=headers), \
             patch.object(email_sync,'backfill_refs',return_value=0), \
             patch.object(email_sync,'_shared_session',side_effect=shared), \
             patch('app.utils.saldeo_status.apply_saldeo_emails'):
            errors=email_sync.sync_requested_accounts([{'id':self.account}])
            self.assertEqual(errors,[])
            # An interrupted/restarted initial pass should reuse stored MIME bodies.
            set_setting(f'email_initial_sync_complete_{self.account}','0')
            session.conn.uid.reset_mock()
            errors=email_sync.sync_requested_accounts([{'id':self.account}])
            session.conn.uid.assert_not_called()
        self.assertEqual(errors,[])
        self.assertEqual(get_setting(f'email_initial_sync_complete_{self.account}'),'1')
        for name in ('INBOX','Clients'):
            rows=get_cached_messages(self.account,get_folder_id(self.account,name))
            recent=next(row for row in rows if row['uid']=='1')
            old=next(row for row in rows if row['uid']=='2')
            self.assertIn(b'Full body',load_raw_message(recent['id']))
            self.assertFalse(load_raw_message(old['id']))
        self.assertTrue(all(call.args[2]=='(BODY.PEEK[])' for call in session.conn.uid.call_args_list))
