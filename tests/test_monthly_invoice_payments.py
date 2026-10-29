"""Pokazanie zapłaconej faktury z wybranego miesiąca w księgowym widoku miesięcznym."""
import unittest

from app.db.dao_conn import get_conn
from app.db.dao_invoices import create_or_update_invoice, mark_exported
from app.db.dao_payments import record_cash_payment
from app.monthly_view.monthly_view_logic import MonthlyViewLogic
from tests.base import DbTestCase


class MonthlyInvoicePaymentTests(DbTestCase):
    def test_invoice_number_and_paid_date_are_scoped_to_month(self):
        company = self.make_company("Klient", nip="1234567890")
        invoice_id = create_or_update_invoice(
            company, "2026-09",
            [{"nazwa": "Ksiegowosc", "ilosc": 1, "netto": 100,
              "brutto": 123, "vat": 23}],
            100, 123,
        )
        conn = get_conn()
        try:
            conn.execute("UPDATE faktury SET numer = %s WHERE id = %s",
                         ("FV/001/09/2026", invoice_id))
            conn.commit()
        finally:
            conn.close()
        mark_exported([invoice_id], 1)
        record_cash_payment(invoice_id, "2026-09-07", "123.00", actor_id=1)

        logic = MonthlyViewLogic(pracownik_id=1)
        rows = logic.get_monthly_rows("2026-09", selected_employee=0, selected_status="Wszystkie")
        row = next(r for r in rows if r["id"] == company)
        self.assertEqual(row["numer_faktury"], "FV/001/09/2026")
        self.assertEqual(row["faktura_zaplacona"], "07/09/2026")

        october = logic.get_monthly_rows("2026-10", selected_employee=0, selected_status="Wszystkie")
        row_next = next(r for r in october if r["id"] == company)
        self.assertFalse(row_next["numer_faktury"])
        self.assertFalse(row_next["faktura_zaplacona"])

    def test_partial_payment_does_not_mark_invoice_fully_paid(self):
        company = self.make_company("Klient czesciowy")
        invoice_id = create_or_update_invoice(
            company, "2026-09",
            [{"nazwa": "Usluga", "ilosc": 1, "netto": 100,
              "brutto": 123, "vat": 23}], 100, 123
        )
        mark_exported([invoice_id], 1)
        record_cash_payment(invoice_id, "2026-09-07", "50.00", actor_id=1)
        logic = MonthlyViewLogic(pracownik_id=1)
        rows = logic.get_monthly_rows("2026-09", 0, "Wszystkie")
        row = next(r for r in rows if r["id"] == company)
        self.assertEqual(row["faktura_zaplacona"], "")


if __name__ == "__main__":
    unittest.main()
