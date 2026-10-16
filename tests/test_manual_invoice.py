import json
import unittest

from PyQt6.QtWidgets import QApplication

from app.db.dao_conn import get_conn
from app.db.dao_invoices import create_manual_invoice, create_or_update_invoice, get_invoice_by_id, get_invoice_for_company_month, list_invoices
from app.db.dao_kontrahenci import delete_kontrahent, get_kontrahent, list_kontrahenci, save_kontrahent, search_kontrahenci
from tests.base import DbTestCase


class ManualInvoiceTests(DbTestCase):
    def test_contractor_crud_search_and_separate_company_table(self):
        contractor_id = save_kontrahent({"typ": "osoba_fizyczna", "nazwa": "Anna Kowalska"})
        self.assertEqual(get_kontrahent(contractor_id)["typ"], "osoba_fizyczna")
        self.assertEqual([item["id"] for item in search_kontrahenci("Anna")], [contractor_id])
        company_id = self.make_company("Sp??ka z firmy")
        conn = get_conn()
        try:
            company_names = [row[0] for row in conn.execute("SELECT nazwa FROM firmy")]
            self.assertIn("Sp??ka z firmy", company_names)
        finally:
            conn.close()
        self.assertNotIn("Anna Kowalska", company_names)
        save_kontrahent({"typ": "firma", "nazwa": "Anna Nowa"}, contractor_id)
        self.assertEqual(get_kontrahent(contractor_id)["nazwa"], "Anna Nowa")
        delete_kontrahent(contractor_id)
        self.assertIsNone(get_kontrahent(contractor_id))

    def test_individual_without_nip_and_multiple_manual_invoices(self):
        contractor_id = save_kontrahent({"typ": "osoba_fizyczna", "nazwa": "Jan Nowak"})
        position = [{"nazwa": "Konsultacja", "ilosc": 1, "jednostka": "szt.", "cena_netto": 100, "vat": 23}]
        first = create_manual_invoice("kontrahent", contractor_id, position, "2026-10-02", "2026-10-02", "2026-10-09")
        second = create_manual_invoice("kontrahent", contractor_id, position, "2026-10-18", "2026-10-18", "2026-10-25")
        invoice = get_invoice_by_id(first)
        self.assertEqual(invoice["nabywca_pelna"], "Jan Nowak")
        self.assertIsNone(invoice["nabywca_nip"])
        self.assertEqual(invoice["kontrahent_id"], contractor_id)
        self.assertIsNone(invoice["firma_id"])
        self.assertEqual(invoice["reczna"], 1)
        self.assertEqual(invoice["status"], "robocza")
        self.assertIn(first, {row["id"] for row in list_invoices(1, False, "2026-10")})
        self.assertNotEqual(first, second)
        self.assertEqual(json.loads(invoice["pozycje_json"])[0]["netto"], 100.0)

    def test_manual_company_invoice_does_not_consume_monthly_invoice_slot(self):
        company_id = self.make_company("Klient firmowy")
        position = [{"nazwa": "Us?uga", "ilosc": 1, "jednostka": "szt.", "cena_netto": 20, "vat": 23}]
        manual = create_manual_invoice("firma", company_id, position, "2026-11-01", "2026-11-01", "2026-11-08")
        self.assertIsNone(get_invoice_for_company_month(company_id, "2026-11"))
        regular = create_or_update_invoice(company_id, "2026-11", position, 20, 24.6)
        self.assertEqual(get_invoice_by_id(manual)["reczna"], 1)
        self.assertEqual(get_invoice_by_id(regular)["reczna"], 0)
        self.assertEqual(get_invoice_for_company_month(company_id, "2026-11")["id"], regular)
        with self.assertRaises(ValueError):
            create_or_update_invoice(company_id, "2026-11", position, 20, 24.6)


class ManualInvoiceUiTests(DbTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_manual_invoice_dialog_searches_firms_and_contractors(self):
        from app.invoices.manual_invoice_dialog import ManualInvoiceDialog

        self.make_company("Firma do wyboru")
        save_kontrahent({"typ": "osoba_fizyczna", "nazwa": "Klient bez NIP"})
        dialog = ManualInvoiceDialog()
        self.assertEqual(dialog.windowTitle(), "Wystaw faktur\u0119")
        dialog.search.setText("Klient bez NIP")
        self.assertEqual(dialog.results.count(), 1)
        dialog.results.itemClicked.emit(dialog.results.item(0))
        self.assertEqual(dialog.buyer["typ"], "kontrahent")
        dialog.search.setText("Firma do wyboru")
        self.assertEqual(dialog.results.count(), 1)
        self.assertEqual(dialog.results.item(0).text().split(":", 1)[0], "Firma")


if __name__ == "__main__":
    unittest.main()
