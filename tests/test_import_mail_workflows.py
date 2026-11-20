"""Yearly filing scope, monthly insurance titles, folder linking and SMTP/IMAP."""
import json
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from unittest.mock import patch,Mock

from tests.base import DbTestCase
from app.db.dao_conn import get_conn
from app.db.dao_app_settings import get_setting,set_setting
from app.companies.checking_obligations import add_imported,profile,report_applies,save
from app.email.company_folders import link_account
from app.email.email_storage import save_folder
from app.services.declaration_year import read_enova


class ImportAndMailTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.company = self.make_company('Firma Żółw',nip='1112223344',adres_email='client@example.com')
        with get_conn() as conn:
            conn.execute("INSERT INTO konta_email(id,email) VALUES (10,'office@example.com')")
        save_folder(10,'INBOX','INBOX','.','')
        save_folder(10,'INBOX.Sent','Wysłane','.',r'\Sent')
        save_folder(10,'INBOX.Firma Zolw','Firma Zolw','.','')

    def test_links_main_contact_and_both_partners_idempotently_without_creating_folders(self):
        from app.db.dao_companies import save_partner,save_contact
        save_partner(self.company,{'imie_pierwsze':'Jan','nazwisko':'Test','email':'owner@example.com'})
        save_contact(self.company,{'email':'contact@example.com'})
        self.assertEqual(link_account(10),3)
        self.assertEqual(link_account(10),0)
        with get_conn() as conn:
            rules = [dict(r) for r in conn.execute('SELECT * FROM reguly_filtrow WHERE konto_id=10')]
            self.assertEqual({r['od'] for r in rules},{'client@example.com','owner@example.com','contact@example.com'})
            self.assertEqual({r['folder_docelowy'] for r in rules},{'INBOX.Firma Zolw'})
            self.assertEqual(conn.execute('SELECT count(*) FROM foldery_email').fetchone()[0],3)
        from app.email.email_rules import find_rule
        self.assertIsNone(find_rule(rules,{'sender':'notclient@example.com','recipient':'office@example.com'}))
        self.assertIsNotNone(find_rule(rules,{'sender':'Jan <CLIENT@example.com>','recipient':'office@example.com'}))

    def test_ambiguous_names_are_skipped_unique_nip_wins_and_manual_rules_are_preserved(self):
        save_folder(10,'Archiwum.Firma Zolw','Firma Zolw','.','')
        self.assertEqual(link_account(10),0)
        with get_conn() as conn:
            conn.execute("DELETE FROM foldery_email WHERE server_name='Archiwum.Firma Zolw'")
        self.make_company('Firma Zolw',adres_email='other@example.com')
        self.assertEqual(link_account(10),0)
        save_folder(10,'INBOX.1112223344','1112223344','.','')
        # The ambiguous name cannot defeat the unique NIP folder.
        self.assertEqual(link_account(10),1)
        with get_conn() as conn:
            conn.execute("UPDATE reguly_filtrow SET folder_docelowy='INBOX.Manual',aktywna=0")
        self.assertEqual(link_account(10),0)
        from app.db.dao_companies import save_contact
        other = self.make_company('Inne',adres_email='shared@example.com')
        save_contact(self.company,{'email':'shared@example.com'})
        save_folder(10,'INBOX.Inne','Inne','.','')
        link_account(10)
        with get_conn() as conn:
            self.assertFalse(conn.execute("SELECT 1 FROM reguly_filtrow WHERE od='shared@example.com' AND firma_id=ANY(%s)",
                                          ([self.company,other],)).fetchone())

    def test_yearly_declarations_fill_twelve_months_and_add_only_without_amounts(self):
        company_id = self.company
        months = [f'2026-{n:02d}' for n in range(1,13)]
        save(company_id,{'2026-01':{'vat_period':'monthly','forms':{'RCA':'none','DRA':'monthly'}}},1)
        set_setting(f'enova_checking_{company_id}_2026-01',json.dumps({'documents':{'accounting':{'old':3}}}))
        with get_conn() as conn:
            conn.execute("INSERT INTO statusy_miesieczne(firma_id,miesiac,ilosc_dok,etap) VALUES (%s,'2026-01',77,'Brak')",(company_id,))
        def fetch(config,source,periods,*,result_kind):
            self.assertEqual(periods,months)
            if result_kind=='jpk':
                return [{'nip':'1112223344','miesiac':m,'status':'prepared','upo':'','form_variant':'JPK_V7K'} for m in months]
            self.assertEqual(result_kind,'declarations')
            return [{'nip':'1112223344','miesiac':m,'department':'accounting','form':'VAT-UE','status':'prepared',
                     'sent_at':None,'upo':'','upo_at':None} for m in months]
        with patch('app.services.declaration_year.fetch_counts',side_effect=fetch) as remote:
            result = read_enova({}, {'nip':'1112223344','database':'A','declarations_query':'decl.sql','jpk_query':'jpk.sql'},company_id,months)
        self.assertTrue(result['ok'])
        self.assertEqual(remote.call_count,2)
        settings = json.loads(get_setting(f'checking_obligations_{company_id}'))
        self.assertEqual(len(settings['imported']),12)
        chosen = profile(settings,'2026-02')
        self.assertEqual(chosen['vat_period'],'monthly')  # manual VAT cadence wins
        self.assertEqual(chosen['forms']['RCA'],'none')
        self.assertEqual(chosen['forms']['VATUE'],'monthly')
        self.assertEqual(chosen['forms']['JPKVAT'],'monthly')
        self.assertEqual(json.loads(get_setting(f'enova_checking_{company_id}_2026-01'))['documents'],{'accounting':{'old':3}})
        with get_conn() as conn:
            row = conn.execute('SELECT ilosc_dok,etap FROM statusy_miesieczne WHERE firma_id=%s',(company_id,)).fetchone()
        self.assertEqual(tuple(row),(77,'Brak'))

    def test_missing_documents_preserve_positive_scope_and_quarterly_source_cadence(self):
        month = '2026-01'
        settings_key = f'checking_obligations_{self.company}'
        with get_conn() as conn:
            add_imported(conn, self.company, month, [{'form': 'JPK_VAT', 'form_variant': 'JPK_V7K'}], 'enova')
        company = {'checking_obligations': json.loads(get_setting(settings_key))}
        self.assertFalse(report_applies(company, 'RCA', month))

        with get_conn() as conn:
            add_imported(conn, self.company, month, [{'form': 'ZUS RCA'}], 'platnik')
            add_imported(conn, self.company, month, [], 'enova')
            add_imported(conn, self.company, month, [], 'platnik')
        company = {'checking_obligations': json.loads(get_setting(settings_key))}
        value = profile(company['checking_obligations'], month)
        self.assertEqual(value['forms'], {'JPKVAT': 'monthly', 'RCA': 'monthly'})
        self.assertEqual(value['vat_period'], 'quarterly')
        self.assertFalse(value.get('_configured', False))
        self.assertTrue(report_applies(company, 'RCA', month))

    def test_monthly_insurance_titles_are_imported_not_carried_into_other_months(self):
        from app.integrations.platnik.declarations import import_months
        from tests.test_platnik_declarations import ROW
        rows = [{**ROW,'nip':'1112223344','insurance_code':'05 10 00'}]
        with patch('app.integrations.platnik.declarations.load',return_value={'enabled':False}), \
             patch('app.integrations.platnik.declarations.fetch',return_value=rows):
            import_months(['2026-11'],company_ids=[self.company])
        from app.monthly_view.monthly_view_logic import MonthlyViewLogic
        from app.monthly_view.monthly_view_model import MonthlyViewModel
        from PyQt6.QtCore import Qt
        logic = MonthlyViewLogic(1)
        rows = logic.get_monthly_rows(selected_month='2026-11',selected_employee=0,selected_status='Wszystkie')
        row = next(r for r in rows if r['id']==self.company)
        model = MonthlyViewModel([row],'kadry')
        self.assertEqual(model.data(model.index(0,11)),'051000')
        self.assertIn('ZUS DRA',model.data(model.index(0,11),Qt.ItemDataRole.ToolTipRole))
        with patch('app.monthly_view.monthly_view_logic.assigned_company_ids',return_value=set()):
            hidden = logic.get_monthly_rows(selected_month='2026-11',selected_employee=0,selected_status='Wszystkie')
        self.assertTrue(all('insurance_details' not in r and 'insurance_codes' not in r for r in hidden))
        from app.companies.monthly_insurance import read_codes
        with get_conn() as conn:
            self.assertEqual(read_codes(conn,[self.company],'2026-10'),{})

    def test_civil_partner_declarations_keep_all_payers_in_one_company_snapshot(self):
        from app.db.dao_companies import save_company,save_partner
        from app.integrations.platnik.declarations import import_months,decoded_snapshot
        from tests.test_platnik_declarations import ROW
        save_company({'id':self.company,'forma_dzialalnosci':'Spółka cywilna'})
        for nip in ('2223334455','3334445566'):
            save_partner(self.company,{'imie_pierwsze':'Wspólnik','nazwisko':nip,'nip':nip})
        rows = [{**ROW,'nip':nip,'payer_id':number,'source_id':number,'insurance_code':'051000'}
                for number,nip in enumerate(('1112223344','2223334455','3334445566'),1)]
        with patch('app.integrations.platnik.declarations.load',return_value={'enabled':False}), \
             patch('app.integrations.platnik.declarations.fetch',return_value=rows):
            import_months(['2026-11'],company_ids=[self.company])
        entries = decoded_snapshot(get_setting(f'platnik_declarations_{self.company}_2026-11'))['declarations']
        self.assertEqual({e['payer_nip'] for e in entries},{'1112223344','2223334455','3334445566'})
        self.assertEqual(len(entries),3)

    def test_smtp_wire_has_no_bcc_and_copies_are_appended_to_imap_sent_and_client(self):
        from app.email.email_actions import send_email_smtp
        link_account(10)
        smtp = Mock()
        smtp.send_message.return_value = {}
        imap = Mock()
        imap.append.return_value = ('OK',[b'APPEND completed'])
        @contextmanager
        def smtp_session(*args,**kwargs):
            yield smtp
        @contextmanager
        def imap_session(*args,**kwargs):
            yield Mock(conn=imap)
        config = {'user':'office@example.com','display_name':'Office','host':'localhost','port':465,'ssl':True,'password':'synthetic'}
        with patch('app.email.email_actions.get_smtp_config_for_account',return_value=config), \
             patch('app.email.email_actions.open_smtp',side_effect=smtp_session), \
             patch('app.email.email_sync._shared_session',side_effect=imap_session), \
             patch('app.email.email_sync.sync_folder'):
            ok,info = send_email_smtp(10,['office@example.com'],'Notice','Body',bcc=['client@example.com'],save_copy=True)
        self.assertTrue(ok,info)
        wire = smtp.send_message.call_args.args[0]
        self.assertIsNone(wire['Bcc'])
        self.assertEqual(smtp.send_message.call_args.kwargs['to_addrs'],['office@example.com','client@example.com'])
        copies = {call.args[0]:BytesParser(policy=policy.default).parsebytes(call.args[3]) for call in imap.append.call_args_list}
        self.assertEqual(set(copies),{'"INBOX.Sent"','"INBOX.Firma Zolw"'})
        self.assertTrue(all(msg['Bcc']=='client@example.com' for msg in copies.values()))
        smtp.send_message.return_value = {'refused@example.com':(550,b'Rejected')}
        imap.append.reset_mock()
        with patch('app.email.email_actions.get_smtp_config_for_account',return_value=config), \
             patch('app.email.email_actions.open_smtp',side_effect=smtp_session), \
             patch('app.email.email_sync._shared_session',side_effect=imap_session), \
             patch('app.email.email_sync.sync_folder'):
            ok,info = send_email_smtp(10,['office@example.com'],'Notice','Body',
                                     bcc=['client@example.com','refused@example.com'],save_copy=True)
        self.assertFalse(ok)
        self.assertIn('refused@example.com',info)
        for call in imap.append.call_args_list:
            archive = BytesParser(policy=policy.default).parsebytes(call.args[3])
            self.assertNotIn('refused@example.com',archive['Bcc'])

    def test_imap_failure_warns_after_successful_smtp_and_does_not_resend(self):
        from app.email.email_actions import send_email_smtp
        smtp = Mock()
        smtp.__enter__ = Mock(return_value=smtp)
        smtp.__exit__ = Mock(return_value=False)
        smtp.send_message.return_value = {}
        config = {'user':'office@example.com','display_name':'Office','host':'localhost','port':465,'ssl':True,'password':'synthetic'}
        with patch('app.email.email_actions.get_smtp_config_for_account',return_value=config), \
             patch('app.email.email_actions.open_smtp',return_value=smtp), \
             patch('app.email.email_actions.store_sent_copy',return_value=False), \
             patch('app.email.email_actions.choose_copy_folder',return_value=None), \
             patch('app.email.email_storage.find_special_folder',return_value='INBOX.Sent'):
            ok,info = send_email_smtp(10,['office@example.com'],'Notice','Body',bcc=['client@example.com'],save_copy=True)
        self.assertTrue(ok)
        self.assertIn('nie potwierdzono',info)
        smtp.send_message.assert_called_once()
