import json
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.db.dao_conn import get_conn
from app.db.dao_invoices import (
    list_trashed_invoices, restore_trashed_invoice, trash_invoice, get_invoice_by_id,
)
from tests.base import DbTestCase


class InvoiceTrashTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.firma = self.make_company("Firma Kosz")
        self.other = self.make_employee("zwykly@example.com", "Jan", "Zwykły")
        conn = get_conn()
        try:
            self.invoice_id = conn.execute(
                "INSERT INTO faktury (firma_id, miesiac, status, rodzaj, numer, eksportowana, ksef_numer, "
                "pozycje_json, kwota_netto, kwota_brutto) "
                "VALUES (%s, '2026-10', 'wystawiona', 'faktura', 'FV/1/10/2026', 1, 'KSEF-123', %s, 100, 123) "
                "RETURNING id", (self.firma, json.dumps([{"nazwa": "Usługa", "netto": 100}]))).fetchone()["id"]
            conn.commit()
        finally:
            conn.close()

    def test_issued_ksef_invoice_goes_to_trash_and_is_restored(self):
        trash_invoice(self.invoice_id, 1)
        self.assertIsNone(get_invoice_by_id(self.invoice_id))
        trashed = list_trashed_invoices(1)
        self.assertEqual([t["numer"] for t in trashed], ["FV/1/10/2026"])
        restore_trashed_invoice(trashed[0]["id"], 1)
        restored = get_invoice_by_id(self.invoice_id)
        self.assertEqual((restored["numer"], restored["ksef_numer"], restored["eksportowana"]),
                         ("FV/1/10/2026", "KSEF-123", 1))
        self.assertEqual(list_trashed_invoices(1), [])

    def test_only_administrator_can_trash(self):
        with self.assertRaises(PermissionError):
            trash_invoice(self.invoice_id, self.other)
        self.assertIsNotNone(get_invoice_by_id(self.invoice_id))
        with self.assertRaises(PermissionError):
            list_trashed_invoices(self.other)

    def test_direct_delete_of_issued_invoice_is_still_blocked(self):
        conn = get_conn()
        try:
            with self.assertRaises(Exception):
                conn.execute("DELETE FROM faktury WHERE id = %s", (self.invoice_id,))
        finally:
            conn.rollback()
            conn.close()


if __name__ == "__main__":
    unittest.main()

class NumberReuseAfterTrashTests(DbTestCase):
    def draft(self, name):
        from app.db.dao_invoices import create_or_update_invoice
        invoice_id = create_or_update_invoice(self.make_company(name), '2026-11',
            [{'nazwa': 'Usługa', 'ilosc': 1, 'cena_netto': 100, 'vat': 23}], 100, 123)
        with get_conn() as conn:
            conn.execute("UPDATE faktury SET data_wystawienia='2026-11-01' WHERE id=%s", (invoice_id,))
        return invoice_id

    def test_deleted_issued_without_ksef_releases_last_number_and_keeps_snapshot(self):
        from app.db.dao_invoices import set_invoice_number, mark_exported, assign_invoice_number
        first, removed = self.draft('Piąta'), self.draft('Szósta')
        set_invoice_number(first, 'FV/0005/11/2026')
        set_invoice_number(removed, 'FV/0006/11/2026')
        mark_exported([first, removed], 1)
        trash_invoice(removed, 1)
        self.assertEqual(assign_invoice_number(self.draft('Następna')), 'FV/0006/11/2026')
        self.assertEqual(get_invoice_by_id(first)['numer'], 'FV/0005/11/2026')
        with get_conn() as conn:
            snapshot = conn.execute('SELECT faktura_json FROM faktury_kosz WHERE faktura_id=%s', (removed,)).fetchone()[0]
            self.assertEqual(snapshot['numer'], 'FV/0006/11/2026')
            self.assertTrue(snapshot['eksportowana'])
            self.assertTrue(conn.execute("SELECT 1 FROM audit_log WHERE rekord_id=%s AND akcja='kosz'", (removed,)).fetchone())

    def test_deleting_middle_document_does_not_renumber_later_invoices(self):
        from app.db.dao_invoices import mark_exported, assign_invoice_number
        removed, later = self.draft('Pierwsza'), self.draft('Druga')
        mark_exported([removed, later], 1)
        later_number = get_invoice_by_id(later)['numer']
        trash_invoice(removed, 1)
        self.assertEqual(get_invoice_by_id(later)['numer'], later_number)
        self.assertEqual(assign_invoice_number(self.draft('Trzecia')), 'FV/0003/11/2026')

    def test_restoring_old_document_cannot_steal_reused_number(self):
        from app.db.dao_invoices import mark_exported, assign_invoice_number, InvoiceLockedError
        removed = self.draft('Usuwana')
        mark_exported([removed], 1)
        number = get_invoice_by_id(removed)['numer']
        trash_invoice(removed, 1)
        trash_id = list_trashed_invoices(1)[0]['id']
        replacement = self.draft('Nowa')
        self.assertEqual(assign_invoice_number(replacement), number)
        with self.assertRaisesRegex(InvoiceLockedError, 'numer został już wykorzystany'):
            restore_trashed_invoice(trash_id, 1)
        self.assertIsNone(get_invoice_by_id(removed))
        self.assertEqual(get_invoice_by_id(replacement)['numer'], number)
        self.assertEqual(list_trashed_invoices(1)[0]['id'], trash_id)

    def test_old_trash_reservations_are_released_on_next_allocation(self):
        from app.db.dao_invoices import mark_exported, assign_invoice_number
        removed = self.draft('Stara rezerwacja')
        mark_exported([removed], 1)
        number = get_invoice_by_id(removed)['numer']
        trash_invoice(removed, 1)
        # Reproduce the stale ledger left by the previous application version.
        with get_conn() as conn:
            conn.execute('INSERT INTO invoice_number_ledger(numer, invoice_id) VALUES (%s,%s)', (number, removed))
        self.assertEqual(assign_invoice_number(self.draft('Po aktualizacji')), number)

    def test_pending_or_accepted_ksef_number_remains_reserved_in_trash(self):
        from app.db.dao_invoices import mark_exported, assign_invoice_number
        for status in ('wysylanie', 'oczekuje', 'do_sprawdzenia', 'przyjeta'):
            with self.subTest(status=status):
                invoice_id = self.draft(status)
                mark_exported([invoice_id], 1)
                number = get_invoice_by_id(invoice_id)['numer']
                with get_conn() as conn:
                    conn.execute('UPDATE faktury SET ksef_status=%s WHERE id=%s', (status, invoice_id))
                trash_invoice(invoice_id, 1)
                with get_conn() as conn:
                    self.assertEqual(conn.execute('SELECT invoice_id FROM invoice_number_ledger WHERE numer=%s', (number,)).fetchone()[0], invoice_id)
                self.assertNotEqual(assign_invoice_number(self.draft('Po ' + status)), number)

    def test_restore_before_reuse_reserves_original_number_again(self):
        from app.db.dao_invoices import mark_exported, assign_invoice_number
        invoice_id = self.draft('Przywracana')
        mark_exported([invoice_id], 1)
        number = get_invoice_by_id(invoice_id)['numer']
        trash_invoice(invoice_id, 1)
        restore_trashed_invoice(list_trashed_invoices(1)[0]['id'], 1)
        self.assertEqual(get_invoice_by_id(invoice_id)['numer'], number)
        self.assertNotEqual(assign_invoice_number(self.draft('Po przywróceniu')), number)
