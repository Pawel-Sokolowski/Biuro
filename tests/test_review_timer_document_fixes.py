"""Cross-month time, concurrent edits and immutable document regressions."""
from datetime import datetime
from pathlib import Path
import tempfile
import threading
from unittest import TestCase, mock

from app.db.dao_conn import get_conn
from tests.base import DbTestCase


class TimerReviewTests(DbTestCase):
    def test_month_filter_does_not_change_timer_month_or_billing(self):
        from app.monthly_view.monthly_view_logic import MonthlyViewLogic
        company = self.make_company()
        with get_conn() as conn:
            service = conn.execute("INSERT INTO uslugi_dodatkowe(nazwa,cena_netto,stawka_vat) VALUES ('Billable',60,'23') RETURNING id").fetchone()[0]
        logic = MonthlyViewLogic(1)
        logic.set_month('2026-10')
        logic.toggle_stoper(company, 'ksiegowosc')
        logic._durable_timers['ksiegowosc'].monotonic_start -= 60
        logic.set_month('2026-11')
        with mock.patch('app.monthly_view.monthly_view_logic.add_invoice_item_for_service') as billing:
            logic.aktywna_firma_stoper_ks_activity = {'typ_czynnosci': 'extra', 'fakturowac': True, 'usluga_id': service}
            logic.stop_all_timers()
        with get_conn() as conn:
            row = conn.execute('SELECT miesiac, sekundy FROM czasomierze WHERE firma_id=%s', (company,)).fetchone()
            total = conn.execute("SELECT sekundy_ksiegowosc FROM statusy_miesieczne WHERE firma_id=%s AND miesiac='2026-10'", (company,)).fetchone()[0]
        self.assertEqual(row['miesiac'], '2026-10')
        self.assertGreaterEqual(row['sekundy'], 60)
        self.assertEqual(total, row['sekundy'])
        self.assertEqual(billing.call_args.args[3], '2026-10')

    def test_recovered_session_retains_original_month(self):
        from app.db import dao_active_timers as timers
        company = self.make_company()
        with get_conn() as conn:
            session = timers.start(conn, 1, company, '2026-10', 'ksiegowosc', {}, datetime.now())
            conn.execute("UPDATE active_timer_sessions SET checkpoint_at=now()-interval '2 minutes' WHERE session_id=%s", (session.session_id,))
        _, recovered = timers.claim(1, session.session_id)
        self.assertEqual(recovered.month, '2026-10')

    def test_two_edits_keep_monthly_counter_equal_to_timer(self):
        from app.db import dao_timers
        company = self.make_company()
        with get_conn() as conn:
            conn.execute("INSERT INTO statusy_miesieczne(firma_id,miesiac,sekundy_ksiegowosc) VALUES (%s,'2026-10',100)", (company,))
            record = conn.execute("INSERT INTO czasomierze(firma_id,pracownik_id,miesiac,typ,rozpoczecie,zakonczenie,sekundy) VALUES (%s,1,'2026-10','ksiegowosc','2026-10-01T09:00:00','2026-10-01T09:01:40',100) RETURNING id", (company,)).fetchone()[0]
        locked, second_started, release = threading.Event(), threading.Event(), threading.Event()
        failures = []
        real_conn = get_conn

        class Connection:
            def __init__(self):
                self.conn = real_conn()

            def __getattr__(self, name):
                return getattr(self.conn, name)

            def execute(self, sql, params=None):
                is_read = 'SELECT miesiac, typ, sekundy FROM czasomierze' in sql
                if is_read and threading.current_thread().name == 'second-edit':
                    second_started.set()
                result = self.conn.execute(sql, params)
                if is_read and threading.current_thread().name == 'first-edit':
                    locked.set()
                    if not release.wait(10):
                        raise TimeoutError('timer edit barrier')
                return result

        def edit(seconds):
            try:
                dao_timers.update_company_timer(company, dao_timers.SOURCE_TIMER, record, 'ksiegowosc', '', seconds, 1)
            except Exception as exc:
                failures.append(exc)

        with mock.patch.object(dao_timers, 'get_conn', side_effect=Connection):
            workers = [threading.Thread(target=edit, args=(200,), name='first-edit'),
                       threading.Thread(target=edit, args=(300,), name='second-edit')]
            try:
                workers[0].start()
                self.assertTrue(locked.wait(10))
                workers[1].start()
                self.assertTrue(second_started.wait(10))
            finally:
                release.set()
                for worker in workers:
                    if worker.ident:
                        worker.join(15)
            self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(failures, [])
        with get_conn() as conn:
            self.assertEqual(conn.execute('SELECT sekundy FROM czasomierze WHERE id=%s', (record,)).fetchone()[0], 300)
            self.assertEqual(conn.execute("SELECT sekundy_ksiegowosc FROM statusy_miesieczne WHERE firma_id=%s AND miesiac='2026-10'", (company,)).fetchone()[0], 300)

    def test_seller_snapshot_survives_setting_changes(self):
        from app.db.dao_app_settings import set_setting
        from app.db.dao_invoices import create_or_update_invoice, get_invoice_by_id, mark_exported
        from app.invoices.invoice_preview import invoice_html
        company = self.make_company()
        set_setting('faktury_sprzedawca_nazwa', 'Old Seller')
        set_setting('faktury_sprzedawca_nip', '1234563218')
        invoice_id = create_or_update_invoice(company, '2026-11', [{'nazwa': 'Service', 'netto': 100, 'vat': 23, 'brutto': 123}], 100, 123)
        mark_exported([invoice_id], 1)
        set_setting('faktury_sprzedawca_nazwa', 'Replacement Seller')
        set_setting('faktury_sprzedawca_nip', '9876543210')
        rendered = invoice_html(get_invoice_by_id(invoice_id), [])
        self.assertIn('Old Seller', rendered)
        self.assertIn('1234563218', rendered)
        self.assertNotIn('Replacement Seller', rendered)
        self.assertNotIn('9876543210', rendered)


class DocumentReviewTests(TestCase):
    def test_paper_upl_accepts_nip_without_xml_birth_date(self):
        from tests.test_power_of_attorney import data
        from app.documents.upl_paper import overlay_commands
        from app.documents.power_of_attorney import build_xml
        values = data()
        values['attorney'].update(pesel='', nip='1234563218')
        pages = overlay_commands(values)
        self.assertEqual(len(pages), 2)
        with self.assertRaisesRegex(ValueError, 'daty urodzenia'):
            build_xml('UPL', values)

    def test_manual_pdf_failure_preserves_destination(self):
        from app.invoices.invoice_preview import write_invoice_pdf
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'existing.pdf'
            target.write_bytes(b'original document')
            with mock.patch('app.invoices.invoice_preview._write_invoice_pdf'):
                with self.assertRaises(OSError):
                    write_invoice_pdf(target, '<p>Invoice</p>')
            self.assertEqual(target.read_bytes(), b'original document')
            self.assertEqual(list(Path(directory).iterdir()), [target])
            with self.assertRaises(OSError):
                write_invoice_pdf(Path(directory) / 'missing' / 'invoice.pdf', '<p>Invoice</p>')

    def test_legacy_accepted_invoice_never_uses_replacement_seller(self):
        from app.invoices.invoice_preview import invoice_html
        with mock.patch('app.invoices.invoice_preview.get_setting', return_value='Replacement Seller'):
            rendered = invoice_html({'eksportowana': 1, 'ksef_nip_wysylki': '1234563218', 'wystawca': 'Old Seller'}, [])
        self.assertIn('Old Seller', rendered)
        self.assertIn('1234563218', rendered)
        self.assertNotIn('NIP: Replacement Seller', rendered)
