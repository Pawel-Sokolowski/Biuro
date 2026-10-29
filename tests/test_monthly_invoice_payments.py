"""Pokazanie zapłaconej faktury z wybranego miesiąca w księgowym widoku miesięcznym."""
import unittest

from app.db.dao_conn import get_conn
from app.db.dao_invoices import create_manual_invoice, create_or_update_invoice, mark_exported
from app.db.dao_payments import record_cash_payment
from app.monthly_view.monthly_view_logic import MonthlyViewLogic
from app.monthly_view.monthly_view_model import MonthlyViewModel
from PyQt6.QtCore import Qt
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
        self.assertIn("07/09/2026: 50.00 PLN (gotówka)", row["faktura_platnosci_tooltip"])

    def test_payment_date_from_october_remains_attached_to_september_invoice(self):
        company = self.make_company("Płatność w następnym miesiącu")
        invoice = create_or_update_invoice(company, "2026-09", [{"nazwa": "Usługa", "ilosc": 1}], 100, 123)
        mark_exported([invoice], 1)
        record_cash_payment(invoice, "2026-10-07", "123.00", actor_id=1)
        rows = MonthlyViewLogic(1).get_monthly_rows("2026-09", 0, "Wszystkie")
        row = next(r for r in rows if r["id"] == company)
        self.assertEqual(row["faktura_zaplacona"], "07/10/2026")

    def test_multiple_issued_invoices_are_visible_and_draft_is_excluded(self):
        company = self.make_company("Kilka faktur")
        invoices = []
        for day in (1, 2, 3):
            invoice = create_manual_invoice(
                "firma", company, [{"nazwa": "Usługa", "ilosc": 1, "cena_netto": 100, "stawka_vat": "23"}],
                f"2026-09-0{day}", f"2026-09-0{day}", "2026-09-15",
            )
            conn = get_conn()
            try:
                conn.execute("UPDATE faktury SET numer = %s WHERE id = %s", (f"FV/00{day}/09/2026", invoice))
                conn.commit()
            finally:
                conn.close()
            invoices.append(invoice)
        mark_exported(invoices[:2], 1)
        record_cash_payment(invoices[0], "2026-09-07", "123.00", actor_id=1)
        rows = MonthlyViewLogic(1).get_monthly_rows("2026-09", 0, "Wszystkie")
        row = next(r for r in rows if r["id"] == company)
        self.assertEqual(row["numer_faktury"], "FV/001/09/2026\nFV/002/09/2026")
        self.assertEqual(row["faktura_zaplacona"], "07/09/2026\n")
        self.assertEqual(len(row["invoice_payments"]), 2)

    def test_accounting_columns_keep_completeness_and_stage_editable_at_new_indices(self):
        model = MonthlyViewModel([{"id": 1}], "ksiegowosc")
        self.assertEqual([model.headerData(i, Qt.Orientation.Horizontal) for i in range(1, 5)],
                         ["Nr faktury", "Zapłacona", "Komplet", "Etap"])
        self.assertEqual(model.COL_DOCS, 3)
        self.assertEqual(model.COL_ETAP, 4)
        self.assertTrue(model.flags(model.index(0, 3)) & Qt.ItemFlag.ItemIsUserCheckable)
        self.assertTrue(model.flags(model.index(0, 4)) & Qt.ItemFlag.ItemIsEditable)
        payroll = MonthlyViewModel([{"id": 1}], "kadry")
        self.assertEqual(payroll.COL_DOCS, 1)
        self.assertEqual(payroll.COL_ETAP, 2)


if __name__ == "__main__":
    unittest.main()
