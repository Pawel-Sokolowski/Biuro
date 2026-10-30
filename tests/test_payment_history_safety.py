from app.config import get_schema
from app.db.dao_conn import get_conn
from app.db.dao_invoices import (
    InvoiceLockedError, create_or_update_invoice, delete_issued_invoice, mark_exported,
)
from app.db.dao_payments import record_cash_payment
from tests.base import DbTestCase


class PaymentHistorySafetyTests(DbTestCase):
    def invoice(self):
        company = self.make_company()
        invoice_id = create_or_update_invoice(company, '2026-09', [
            {'nazwa': 'Usługa', 'ilosc': 1, 'netto': 100, 'brutto': 123, 'vat': 23}
        ], 100, 123)
        mark_exported([invoice_id], 1)
        return invoice_id

    def test_paid_invoice_cannot_be_deleted_or_orphan_its_payment(self):
        invoice_id = self.invoice()
        record_cash_payment(invoice_id, '2026-09-07', '50.00', 1)
        with self.assertRaisesRegex(InvoiceLockedError, 'płatności'):
            delete_issued_invoice(invoice_id)
        with get_conn() as conn:
            self.assertEqual(conn.execute(
                'SELECT faktura_id FROM faktury_platnosci'
            ).fetchone()[0], invoice_id)
            self.assertIsNotNone(conn.execute('SELECT id FROM faktury WHERE id = %s', (invoice_id,)).fetchone())

    def test_pending_ksef_document_cannot_be_deleted(self):
        invoice_id = self.invoice()
        with get_conn() as conn:
            conn.execute("UPDATE faktury SET ksef_status = 'do_sprawdzenia' WHERE id = %s", (invoice_id,))
        with self.assertRaisesRegex(InvoiceLockedError, 'KSeF'):
            delete_issued_invoice(invoice_id)

    def test_explicit_company_reset_clears_payments_in_its_temporary_schema(self):
        from app.db.reset_data import count_company_data, delete_company_data
        self.assertTrue(self.schema_name.startswith('test_'))
        self.assertEqual(get_schema(), self.schema_name)
        with get_conn() as conn:
            self.assertEqual(conn.execute('SELECT current_schema()').fetchone()[0], self.schema_name)
        record_cash_payment(self.invoice(), '2026-09-07', '50.00', 1)
        self.assertEqual(count_company_data()['faktury_platnosci'], 1)
        delete_company_data()
        counts = count_company_data()
        self.assertEqual(counts['faktury_platnosci'], 0)
        self.assertEqual(counts['faktury'], 0)
