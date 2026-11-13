from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import uuid
from unittest import mock

from app.config import schema_context
from app.db.dao_conn import get_conn
from app.db.dao_invoices import create_correction, create_or_update_invoice, update_invoice_draft, mark_exported, cancel_invoice, InvoiceLockedError
from app.db import dao_payments as payments
from tests.base import DbTestCase


class BankReconciliationTests(DbTestCase):
    def invoice(self, total='123', original=None):
        value = Decimal(total)
        positions = [{'nazwa': 'Usługa', 'ilosc': -1 if value < 0 else 1, 'cena_netto': float(abs(value)), 'vat': 0}]
        if original is None:
            company = self.make_company('Client ' + uuid.uuid4().hex[:8])
            invoice = create_or_update_invoice(company, '2026-11', positions, float(value), float(value))
        else:
            invoice = create_correction(original)
            update_invoice_draft(invoice, positions)
        mark_exported([invoice], 1)
        return invoice

    def transfer(self, amount='123', direction='C', currency='PLN'):
        with get_conn() as conn:
            return conn.execute("INSERT INTO bank_transakcje(hash,data_operacji,kwota,waluta,kierunek) "
                                "VALUES (%s,'2026-11-05',%s,%s,%s) RETURNING id",
                                (uuid.uuid4().hex, amount, currency, direction)).fetchone()[0]

    def manual(self, invoice, amount):
        payments.record_manual_payment(invoice, '2026-10-31', amount, 1)
        with get_conn() as conn:
            return conn.execute('SELECT id FROM faktury_platnosci WHERE faktura_id = %s ORDER BY id DESC LIMIT 1', (invoice,)).fetchone()[0]

    def remaining(self, transfer):
        return next(row['remaining'] for row in payments.list_unsettled_transfers(1, include_all=True) if row['id'] == transfer)

    def test_manual_payment_links_once_then_remaining_110_pays_positive_correction(self):
        invoice = self.invoice()
        manual_id = self.manual(invoice, '123')
        transfer = self.transfer('233')
        choices = payments.list_invoice_choices(1)
        choice = next(row for row in choices if row['id'] == invoice)
        self.assertEqual(choice['balance'], Decimal('0'))
        self.assertEqual(choice['manual_payments'][0]['id'], manual_id)
        payments.confirm_bank_payment(transfer, invoice, '123', 1, manual_payment_id=manual_id)
        self.assertEqual(payments.get_invoice_payment_summary(invoice, 1)['paid'], Decimal('123'))
        self.assertEqual(self.remaining(transfer), Decimal('110'))
        correction = self.invoice('110', original=invoice)
        self.assertIn(correction, {row['id'] for row in payments.list_invoice_choices(1)})
        payments.confirm_bank_payment(transfer, correction, '110', 1)
        self.assertEqual(self.remaining(transfer), 0)
        self.assertNotIn(transfer,{row['id'] for row in payments.list_unsettled_transfers(1)})
        self.assertIn(transfer,{row['id'] for row in payments.list_unsettled_transfers(1,include_all=True)})
        self.assertEqual(payments.get_invoice_payment_summary(correction, 1)['balance'], 0)

    def test_partial_manual_link_splits_without_changing_payment_total_or_date(self):
        invoice = self.invoice('200')
        manual = self.manual(invoice, '200')
        transfer = self.transfer('110')
        payments.confirm_bank_payment(transfer, invoice, '110', 1, manual_payment_id=manual)
        summary = payments.get_invoice_payment_summary(invoice, 1)
        self.assertEqual(summary['paid'], Decimal('200'))
        self.assertEqual({row['kwota'] for row in summary['payments']}, {Decimal('110'), Decimal('90')})
        self.assertTrue(all(str(row['data_zaplaty']) == '2026-10-31' for row in summary['payments']))
        linked = next(row for row in summary['payments'] if row['transakcja_id'] == transfer)
        payments.revert_settlement(linked['id'], 1)
        self.assertEqual(payments.get_invoice_payment_summary(invoice, 1)['paid'], Decimal('200'))
        self.assertEqual(self.remaining(transfer), Decimal('110'))

    def test_delete_import_can_preserve_existing_payment_as_manual(self):
        invoice = self.invoice()
        transfer = self.transfer()
        payments.confirm_bank_payment(transfer, invoice, '123', 1)
        with self.assertRaises(payments.PaymentError):
            payments.delete_bank_transaction(transfer, 1)
        payments.delete_bank_transaction(transfer, 1, preserve_payments=True)
        summary = payments.get_invoice_payment_summary(invoice, 1)
        self.assertEqual(summary['paid'], Decimal('123'))
        self.assertIsNone(summary['payments'][0]['transakcja_id'])
        self.assertTrue(summary['payments'][0]['manual_origin'])

    def test_legacy_direction_requires_confirmation_without_reclassifying_storna(self):
        invoice, transfer = self.invoice(), self.transfer(direction=None)
        self.assertIn(transfer, {row['id'] for row in payments.list_unsettled_transfers(1)})
        with self.assertRaises(payments.PaymentError):
            payments.confirm_bank_payment(transfer, invoice, '123', 1)
        payments.confirm_bank_payment(transfer, invoice, '123', 1, allow_legacy_credit=True)
        for direction in ('RC', 'RD'):
            with self.assertRaises(payments.PaymentError):
                payments.confirm_bank_payment(self.transfer(direction=direction), self.invoice(), '123', 1, allow_legacy_credit=True)

    def test_negative_correction_reduces_invoice_without_creating_bank_money(self):
        invoice = self.invoice('200')
        self.manual(invoice, '100')
        credit = self.invoice('-100', original=invoice)
        payments.apply_invoice_correction(credit, invoice, '100', '2026-11-05', 1)
        self.assertEqual(payments.get_invoice_payment_summary(invoice, 1)['balance'], 0)
        self.assertEqual(payments.get_invoice_payment_summary(credit, 1)['balance'], 0)
        with get_conn() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM bank_transakcje').fetchone()[0], 0)
        mapped = payments.payments_for_invoices([invoice, credit])
        self.assertEqual(sum(row['kwota'] for row in mapped[credit]), Decimal('100'))
        offset = next(row for row in payments.list_settlements(1) if row['sposob'] == 'kompensata')
        payments.revert_settlement(offset['id'], 1)
        self.assertEqual(payments.get_invoice_payment_summary(invoice, 1)['balance'], Decimal('100'))
        self.assertEqual(payments.get_invoice_payment_summary(credit, 1)['balance'], Decimal('100'))

    def test_unused_credit_can_be_refunded_with_outgoing_transfer(self):
        invoice = self.invoice('200')
        self.manual(invoice, '150')
        credit = self.invoice('-100', original=invoice)
        payments.apply_invoice_correction(credit, invoice, '50', '2026-11-05', 1)
        transfer = self.transfer('-50', 'D')
        payments.confirm_bank_payment(transfer, credit, '50', 1)
        self.assertEqual(payments.get_invoice_payment_summary(credit, 1)['balance'], 0)
        self.assertEqual(self.remaining(transfer), 0)
        with self.assertRaises(payments.PaymentError):
            payments.apply_invoice_correction(credit, invoice, '1', '2026-11-05', 1)

    def test_manual_refund_links_to_bank_debit_without_another_refund(self):
        credit = self.invoice('-100', original=self.invoice('200'))
        manual = self.manual(credit, '100')
        transfer = self.transfer('-100', 'D')
        payments.confirm_bank_payment(transfer, credit, '100', 1, manual_payment_id=manual)
        self.assertEqual(payments.get_invoice_payment_summary(credit, 1)['paid'], Decimal('100'))
        self.assertEqual(self.remaining(transfer), 0)

    def test_wrong_family_overallocation_and_cash_link_are_rejected(self):
        invoice = self.invoice('200')
        other = self.invoice('200')
        credit = self.invoice('-100', original=invoice)
        with self.assertRaises(payments.PaymentError):
            payments.apply_invoice_correction(credit, other, '100', '2026-11-05', 1)
        with self.assertRaises(payments.PaymentError):
            payments.apply_invoice_correction(credit, invoice, '101', '2026-11-05', 1)
        payments.record_cash_payment(invoice, '2026-11-05', '100', 1)
        payment = payments.get_invoice_payment_summary(invoice, 1)['payments'][0]
        with self.assertRaises(payments.PaymentError):
            payments.confirm_bank_payment(self.transfer(), invoice, '100', 1, manual_payment_id=payment['id'])

    def test_used_correction_cannot_be_cancelled(self):
        invoice = self.invoice('200')
        credit = self.invoice('-100', original=invoice)
        payments.apply_invoice_correction(credit, invoice, '100', '2026-11-05', 1)
        with self.assertRaises(InvoiceLockedError):
            cancel_invoice(credit, 1, 'Cannot remove used credit')

    def test_concurrent_links_cannot_reuse_the_same_manual_payment(self):
        invoice = self.invoice()
        manual = self.manual(invoice, '123')
        transfers = [self.transfer(), self.transfer()]
        def link(transfer):
            with schema_context(self.schema_name):
                try:
                    payments.confirm_bank_payment(transfer, invoice, '123', 1, manual_payment_id=manual)
                    return 'ok'
                except payments.PaymentError:
                    return 'rejected'
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(link, transfers))
        self.assertCountEqual(results, ['ok', 'rejected'])
        self.assertEqual(payments.get_invoice_payment_summary(invoice, 1)['paid'], Decimal('123'))

    def test_invoice_choices_do_not_query_permissions_per_invoice(self):
        self.invoice()
        with mock.patch.object(payments, 'can_access_invoice', wraps=payments.can_access_invoice) as access:
            self.assertTrue(payments.list_invoice_choices(1))
            self.assertTrue(payments.list_company_choices(1))
        access.assert_not_called()
