from unittest import TestCase
from unittest.mock import patch, MagicMock
from tests.base import DbTestCase
from app.db.dao_conn import get_conn
from app.integrations.saldeo_web.history import parse, ingest, apply_pending, list_history, notification_batches


class WorkerRunTests(TestCase):
    def test_worker_reports_page_row_and_short_name_match_counts(self):
        from app.integrations.saldeo_web.sync import run
        item = {'title': 'DEMOCO - Komplet dokumentów', 'period': 'Październik 2026',
                'text': 'Zakończone\nDEMOCO - Komplet dokumentów\nPaździernik 2026'}
        lock = MagicMock()
        lock.return_value.__enter__.return_value = True
        saved = []
        with patch('app.integrations.saldeo_web.sync.load', return_value={'enabled': True, 'history_url': 'https://saldeo.test/history'}), \
             patch('app.integrations.saldeo_web.sync.browser_lock', lock), \
             patch('app.integrations.saldeo_web.sync.apply_pending', side_effect=[1, 2]), \
             patch('app.integrations.saldeo_web.sync.read_history',
                   side_effect=lambda _config, _stop, progress: (
                       progress('history_page_read', {'pages': 3, 'rows_seen': 8, 'items': 1}),
                       {'state': 'ok', 'items': [item], 'pages': 3, 'rows_seen': 8})[1]), \
             patch('app.integrations.saldeo_web.sync.ingest', return_value=1), \
             patch('app.integrations.saldeo_web.sync.set_setting', side_effect=lambda key, value: saved.append((key, value))):
            result = run()
        self.assertEqual((result['pages'], result['rows_seen'], result['scanned'], result['recognized'],
                          result['new'], result['applied']), (3, 8, 1, 1, 1, 3))
        self.assertTrue(any('"stage": "history_page_read"' in value for _key, value in saved))


class ParserTests(TestCase):
    def test_plain_and_wrapped_company_names_and_invalid_month(self):
        self.assertEqual(parse('MERIT. informuje o dostarczeniu dokumentów za 10/2026'), ('MERIT.', '2026-10'))
        self.assertEqual(parse('Klient (MERIT.) informuje o dostarczeniu kompletu dokumentów za 10/2026'), ('MERIT.', '2026-10'))
        self.assertIsNone(parse('MERIT. informuje o dostarczeniu dokumentów za 13/2026'))


class HistoryTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.company = self.make_company()
        with get_conn() as conn:
            self.admin = conn.execute('SELECT id FROM pracownicy WHERE is_admin=1').fetchone()[0]
            conn.execute('UPDATE firmy SET nazwa_saldeo_firmy=%s WHERE id=%s', ('MERIT.',self.company))
        self.items = [{'text':'MERIT. informuje o dostarczeniu dokumentów za 10/2026', 'id':'1'}]

    def test_restart_replays_without_duplicates_or_changing_delivery_date(self):
        self.assertEqual(ingest(self.items,'https://example.test/history'),1)
        self.assertEqual(apply_pending(),1)
        with get_conn() as conn:
            conn.execute("UPDATE statusy_miesieczne SET data_dostarczenia='2026-10-20' WHERE firma_id=%s",(self.company,))
        self.assertEqual(ingest(self.items,'https://example.test/history'),0)
        self.assertEqual(apply_pending(),0)
        with get_conn() as conn:
            self.assertEqual(conn.execute('SELECT data_dostarczenia FROM statusy_miesieczne WHERE firma_id=%s',(self.company,)).fetchone()[0],'2026-10-20')
        self.assertEqual(len(list_history(self.admin)),1)

    def test_unmapped_backlog_does_not_block_new_deliveries(self):
        source = 'https://example.test/history'
        unknown = [{'text': f'Firma {i} informuje o dostarczeniu dokumentów za 10/2026'}
                   for i in range(500)]
        self.assertEqual(ingest(unknown, source), 500)
        self.assertEqual(apply_pending(), 0)
        self.assertEqual(ingest(self.items, source), 1)
        self.assertEqual(apply_pending(), 1)
        self.assertEqual(len(notification_batches(self.admin)),1)

    def test_unknown_and_ambiguous_names_wait_for_mapping(self):
        with get_conn() as conn:
            conn.execute('UPDATE firmy SET nazwa_saldeo_firmy=NULL WHERE id=%s',(self.company,))
        ingest(self.items,'source')
        self.assertEqual(apply_pending(),0)
        self.assertEqual(list_history(self.admin)[0]['state'],'needs_mapping')
        with get_conn() as conn:
            conn.execute('UPDATE firmy SET nazwa_saldeo_firmy=%s WHERE id=%s',('MERIT.',self.company))
        second = self.make_company(nazwa='Duplicate')
        with get_conn() as conn:
            conn.execute('UPDATE firmy SET nazwa_saldeo_firmy=%s WHERE id=%s',(' merit. ',second))
        self.assertEqual(apply_pending(),0)
        with get_conn() as conn:
            conn.execute('UPDATE firmy SET nazwa_saldeo_firmy=NULL WHERE id=%s',(second,))
        self.assertEqual(apply_pending(),1)

    def test_failed_apply_keeps_inbox_pending_for_retry(self):
        ingest(self.items,'source')
        with patch('app.integrations.saldeo_web.history.datetime') as clock:
            clock.now.side_effect = RuntimeError('failure before update')
            with self.assertRaises(RuntimeError):
                apply_pending()
        self.assertEqual(list_history(self.admin)[0]['state'],'pending')
        self.assertEqual(apply_pending(),1)

    def test_batches_group_multiple_companies_and_inactive_user_has_no_history(self):
        second = self.make_company(nazwa='Second')
        with get_conn() as conn:
            conn.execute('UPDATE firmy SET nazwa_saldeo_firmy=%s WHERE id=%s',('ALFA',second))
        ingest(self.items + [{'text':'ALFA informuje o dostarczeniu dokumentów za 10/2026'}],'source')
        self.assertEqual(apply_pending(),2)
        self.assertEqual(len(notification_batches(self.admin)),1)
        self.assertIn('2 zgłoszeń',notification_batches(self.admin)[0]['title'])
        self.assertEqual(list_history(None),[])


class SaldeoPopupFormatTests(TestCase):
    def test_complete_card_with_polish_month_is_distinct_from_operation_status(self):
        self.assertEqual(parse('done\nBWSERVIC - Komplet dokumentów\nPaździernik 2026\n09:13'),('BWSERVIC','2026-10'))
        self.assertIsNone(parse('BWSERVIC\nZakończone\nPaździernik 2026'))
        self.assertIsNone(parse('BWSERVIC - Komplet dokumentów\nNieznany 2026'))
        self.assertEqual(parse('ABC - XYZ - Komplet dokumentów\nGrudzień 2026'),('ABC - XYZ','2026-12'))


class SaldeoPopupReplayTests(DbTestCase):
    def test_position_and_clock_changes_do_not_repeat_completion(self):
        first = {'id':'','title':'BWSERVIC - Komplet dokumentów','period':'Październik 2026',
                 'text':'done\nBWSERVIC - Komplet dokumentów\nPaździernik 2026\n09:13'}
        second = {**first,'text':first['text'].replace('09:13','Wczoraj')}
        self.assertEqual(ingest([first],'source'),1)
        self.assertEqual(ingest([second],'source'),0)
