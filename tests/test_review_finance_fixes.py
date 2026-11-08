"""Real PostgreSQL schedules for invoice identity and shared-workstation writes."""
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

from app.config import schema_context
from app.db import dao_invoices as invoices
from app.db.dao_app_settings import set_setting
from app.db.dao_conn import get_conn
from app.db.dao_payments import apply_invoice_correction, get_invoice_payment_summary
from app.invoices import ksef_submission
from tests.base import DbTestCase


class ReviewFinanceFixTests(DbTestCase):
    MONTH = '2026-11'

    def setUp(self):
        super().setUp()
        self.company = self.make_company()
        with get_conn() as conn:
            self.service = conn.execute(
                "INSERT INTO uslugi_dodatkowe(nazwa,cena_netto,stawka_vat) "
                "VALUES ('Additional',60,'23') RETURNING id"
            ).fetchone()[0]
        self.invoice = invoices.create_or_update_invoice(self.company, self.MONTH, [
            {'nazwa': 'Base', 'ilosc': 1, 'cena_netto': 100, 'vat': 23},
        ], 100, 123)

    def _append(self):
        return invoices.add_invoice_item_for_service(self.company, self.service, 1, self.MONTH)

    def _race(self, second_operation):
        """Pause A after reading positions; B either blocks or completes first.

        Waiting for a PostgreSQL lock (rather than sleeping) makes both old,
        unsafe and new, serialized implementations follow a deterministic order.
        """
        read, release, finished = threading.Event(), threading.Event(), threading.Event()
        second_connected = threading.Event()
        errors, values, pids = [], {}, {}
        real_connect, real_loads = invoices.get_conn, json.loads

        def connect():
            conn = real_connect()
            if threading.current_thread().name == 'finance-second':
                pids.setdefault('second', conn.info.backend_pid)
                second_connected.set()
            return conn

        def loads(value, *args, **kwargs):
            result = real_loads(value, *args, **kwargs)
            if threading.current_thread().name == 'finance-first':
                read.set()
                if not release.wait(10):
                    raise TimeoutError('The test did not release the first invoice append')
            return result

        def run(name, work):
            try:
                with schema_context(self.schema_name):
                    values[name] = work()
            except Exception as exc:
                errors.append(exc)
            finally:
                if name == 'second':
                    finished.set()

        first = threading.Thread(target=run, args=('first', self._append), name='finance-first')
        second = threading.Thread(target=run, args=('second', second_operation), name='finance-second')
        with patch.object(invoices, 'get_conn', side_effect=connect), patch.object(
            invoices, 'json', SimpleNamespace(loads=loads, dumps=json.dumps)
        ):
            try:
                first.start()
                self.assertTrue(read.wait(10), 'First append did not read the invoice')
                second.start()
                self.assertTrue(second_connected.wait(10))
                deadline = time.monotonic() + 10
                while not finished.is_set():
                    with get_conn() as observer:
                        blocked = observer.execute('SELECT pg_blocking_pids(%s)', (pids['second'],)).fetchone()[0]
                    if blocked:
                        break
                    if time.monotonic() >= deadline:
                        self.fail('Second operation neither completed nor waited for the invoice lock')
                    finished.wait(0.01)
            finally:
                release.set()
                first.join(12)
                if second.ident is not None:
                    second.join(12)
        self.assertFalse(first.is_alive() or second.is_alive())
        if errors:
            raise errors[0]
        return values

    def test_issuance_serializes_with_service_append_and_issued_contents_stay_fixed(self):
        def issue():
            invoices.mark_exported([self.invoice], 1)
            return invoices.get_invoice_by_id(self.invoice)

        result = self._race(issue)
        latest = invoices.get_invoice_by_id(self.invoice)
        issued = result['second']
        self.assertTrue(latest['eksportowana'])
        self.assertEqual(latest['numer'], issued['numer'])
        self.assertEqual(latest['kwota_brutto'], issued['kwota_brutto'])
        self.assertEqual(json.loads(latest['pozycje_json']), json.loads(issued['pozycje_json']))
        self.assertEqual(latest['kwota_brutto'], 196.8)

    def test_concurrent_appends_preserve_both_lines_ledger_and_allocations(self):
        self._race(self._append)
        row = invoices.get_invoice_by_id(self.invoice)
        positions = json.loads(row['pozycje_json'])
        self.assertEqual(len(positions), 3)
        self.assertEqual(row['kwota_brutto'], 270.6)
        with get_conn() as conn:
            ledger = conn.execute('SELECT id FROM uslugi_dodatkowe_firma WHERE firma_id=%s',
                                  (self.company,)).fetchall()
            allocated = conn.execute('SELECT extra_service_row_id FROM faktury_uslugi_dodatkowe WHERE invoice_id=%s',
                                     (self.invoice,)).fetchall()
        self.assertEqual({r[0] for r in ledger}, {r[0] for r in allocated})
        self.assertEqual(len(ledger), 2)

    def test_concurrent_first_appends_create_one_draft(self):
        invoices.delete_draft_invoice(self.invoice)
        results = self._race(self._append)
        self.assertEqual(results['first'], results['second'])
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id, kwota_brutto, pozycje_json FROM faktury WHERE firma_id=%s AND miesiac=%s",
                (self.company, self.MONTH),
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(float(rows[0]['kwota_brutto']), 147.6)
        self.assertEqual(len(json.loads(rows[0]['pozycje_json'])), 2)

    def test_issued_invoice_keeps_later_service_only_in_ledger(self):
        invoices.mark_exported([self.invoice], 1)
        self.assertIsNone(self._append())
        self.assertEqual(invoices.get_invoice_by_id(self.invoice)['kwota_brutto'], 123)
        with get_conn() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM uslugi_dodatkowe_firma WHERE firma_id=%s',
                                          (self.company,)).fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT count(*) FROM faktury_uslugi_dodatkowe WHERE invoice_id=%s',
                                          (self.invoice,)).fetchone()[0], 0)

    def _pending_submission(self):
        invoices.mark_exported([self.invoice], 1)
        set_setting('ksef_nip', '9876543210')
        set_setting('ksef_srodowisko', 'test')
        with get_conn() as conn:
            conn.execute("UPDATE faktury SET ksef_status='oczekuje', ksef_referencja_sesji='SESSION', "
                         "ksef_referencja_faktury='REF', ksef_srodowisko_wysylki='test', "
                         "ksef_nip_wysylki='9876543210' WHERE id=%s", (self.invoice,))

    def test_late_pending_or_rejected_response_cannot_regress_accepted_status(self):
        self._pending_submission()
        accepted = SimpleNamespace(invoice_submission_status=lambda *a: {
            'status': {'code': 200}, 'ksefNumber': 'KSEF-ACCEPTED',
        })
        for old_code in (150, 450):
            with self.subTest(old_code=old_code):
                def late(*args):
                    ksef_submission.refresh_invoice_submission(self.invoice, 1, client=accepted)
                    return {'status': {'code': old_code}}
                result = ksef_submission.refresh_invoice_submission(
                    self.invoice, 1, client=SimpleNamespace(invoice_submission_status=late),
                )
                row = invoices.get_invoice_by_id(self.invoice)
                self.assertEqual(row['ksef_status'], 'przyjeta')
                self.assertEqual(row['ksef_numer'], 'KSEF-ACCEPTED')
                self.assertEqual(result['status']['code'], 200)
                self.assertEqual(result['ksefNumber'], 'KSEF-ACCEPTED')

    def test_late_pending_response_cannot_regress_rejected_status(self):
        self._pending_submission()
        rejected = SimpleNamespace(invoice_submission_status=lambda *a: {'status': {'code': 450}})
        def late(*args):
            ksef_submission.refresh_invoice_submission(self.invoice, 1, client=rejected)
            return {'status': {'code': 150}}
        result = ksef_submission.refresh_invoice_submission(
            self.invoice, 1, client=SimpleNamespace(invoice_submission_status=late),
        )
        self.assertEqual(invoices.get_invoice_by_id(self.invoice)['ksef_status'], 'odrzucona')
        self.assertGreaterEqual(result['status']['code'], 400)

    def test_external_contractor_correction_preserves_identity_and_can_be_compensated(self):
        with get_conn() as conn:
            contractor = conn.execute("INSERT INTO kontrahenci(typ,nazwa,nip,email) "
                                      "VALUES ('firma','External','1234567890','external@example.com') RETURNING id"
                                      ).fetchone()[0]
        invoice = invoices.create_manual_invoice('kontrahent', contractor, [
            {'nazwa': 'Consultation', 'ilosc': 1, 'cena_netto': 100, 'vat': 23},
        ], '2026-11-01', '2026-11-01', '2026-11-10')
        invoices.mark_exported([invoice], 1)
        correction = invoices.create_correction(invoice, 'Full refund')
        invoices.mark_exported([correction], 1)
        row = invoices.get_invoice_by_id(correction)
        self.assertEqual(row['kontrahent_id'], contractor)
        self.assertTrue(row['reczna'])
        self.assertIsNone(row['firma_id'])
        apply_invoice_correction(correction, invoice, 123, '2026-11-02', 1)
        self.assertEqual(get_invoice_payment_summary(invoice, 1)['balance'], 0)
        self.assertEqual(get_invoice_payment_summary(correction, 1)['balance'], 0)
