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
