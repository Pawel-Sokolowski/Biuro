import json
import unittest
from unittest.mock import patch

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QMessageBox

from app.db.dao_invoices import (
    InvoiceLockedError, create_correction, create_or_update_invoice, delete_draft_invoice,
    get_invoice_by_id, get_invoice_positions, list_invoices, mark_exported, set_ksef_number,
    update_invoice_draft,
)
from app.invoices.positions import normalize_position, reverse_positions, totals
from tests.base import DbTestCase

POSITIONS = [
    {"nazwa": "Księgowość", "ilosc": 1, "jednostka": "mies.", "netto": 500.0, "vat": 23, "brutto": 615.0,
     "kategoria": "księgowość", "generated": True},
    {"nazwa": "Dokumenty", "ilosc": 4, "jednostka": "szt.", "netto": 20.0, "vat": 23, "brutto": 24.6},
]


class PositionMathTests(unittest.TestCase):
    def test_normalize_derives_unit_price_and_totals(self):
        item = normalize_position(POSITIONS[1])
        self.assertEqual(item["cena_netto"], 5.0)
        self.assertEqual(item["netto"], 20.0)
        self.assertEqual(item["brutto"], 24.6)

    def test_reverse_negates_quantity(self):
        reversed_positions = reverse_positions(POSITIONS)
        self.assertEqual(totals(reversed_positions), (-520.0, -639.6))
        self.assertTrue(all(p["ilosc"] < 0 for p in reversed_positions))


class InvoiceLifecycleTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.firma = self.make_company("Klient SA")
        self.invoice_id = create_or_update_invoice(self.firma, "2026-10", POSITIONS, 520.0, 639.6)

    def test_edit_draft_recalculates_totals(self):
        edited = [dict(POSITIONS[0], ilosc=2, cena_netto=500)]
        netto, brutto = update_invoice_draft(self.invoice_id, edited, numer="FV/1")
        self.assertEqual((netto, brutto), (1000.0, 1230.0))
        invoice = get_invoice_by_id(self.invoice_id)
        self.assertEqual(invoice["numer"], "FV/1")
        self.assertEqual(len(get_invoice_positions(self.invoice_id)), 1)

    def test_issued_invoice_is_locked_and_listed_separately(self):
        self.assertEqual(len(list_invoices(1, False, "2026-10")), 1)
        mark_exported([self.invoice_id], 1)
        self.assertEqual(list_invoices(1, False, "2026-10"), [])
        issued = list_invoices(1, True)
        self.assertEqual([i["id"] for i in issued], [self.invoice_id])
        self.assertEqual(issued[0]["status"], "wystawiona")
        with self.assertRaises(InvoiceLockedError):
            update_invoice_draft(self.invoice_id, POSITIONS)
        with self.assertRaises(InvoiceLockedError):
            create_or_update_invoice(self.firma, "2026-10", POSITIONS, 1, 1, existing_invoice_id=self.invoice_id)
        with self.assertRaises(InvoiceLockedError):
            delete_draft_invoice(self.invoice_id)

    def test_regenerating_existing_invoice_preserves_its_issue_and_due_dates(self):
        from app.db.dao_conn import get_conn

        conn = get_conn()
        try:
            conn.execute(
                "UPDATE faktury SET data_wystawienia = %s, termin_platnosci = %s WHERE id = %s",
                ("2026-10-28", "2026-11-07", self.invoice_id),
            )
            conn.commit()
        finally:
            conn.close()

        create_or_update_invoice(
            self.firma, "2026-10", POSITIONS, 520.0, 639.6,
            existing_invoice_id=self.invoice_id,
        )
        invoice = get_invoice_by_id(self.invoice_id)
        self.assertEqual(invoice["data_wystawienia"], "2026-10-28")
        self.assertEqual(invoice["termin_platnosci"], "2026-11-07")

    def test_new_invoice_uses_seventh_of_following_month(self):
        settings = {
            "termin_platnosci_tryb": "dzien",
            "termin_platnosci_dzien": "07",
        }
        with patch("app.db.dao_invoices._now_date", return_value="2026-10-28"), \
             patch("app.utils.payment_terms.get_setting_first",
                   side_effect=lambda keys, default=None: next(
                       (settings[key] for key in keys if key in settings), default
                   )):
            invoice_id = create_or_update_invoice(
                self.firma, "2026-11", POSITIONS, 520.0, 639.6
            )
        invoice = get_invoice_by_id(invoice_id)
        self.assertEqual(invoice["data_wystawienia"], "2026-10-28")
        self.assertEqual(invoice["termin_platnosci"], "2026-11-07")

    def test_correction_requires_issued_invoice_and_allows_many(self):
        with self.assertRaises(ValueError):
            create_correction(self.invoice_id)
        mark_exported([self.invoice_id], 1)
        first = create_correction(self.invoice_id, "Błędna ilość")
        second = create_correction(self.invoice_id, "Rabat")
        correction = get_invoice_by_id(first)
        self.assertEqual(correction["rodzaj"], "korekta")
        self.assertEqual(correction["korygowana_faktura_id"], self.invoice_id)
        self.assertEqual(correction["status"], "robocza")
        self.assertEqual(correction["kwota_netto"], -520.0)
        self.assertNotEqual(first, second)
        self.assertEqual({i["id"] for i in list_invoices(1, False, "2026-10")}, {first, second})

    def test_correction_does_not_block_next_monthly_invoice_lookup(self):
        from app.db.dao_invoices import get_invoice_for_company_month
        mark_exported([self.invoice_id], 1)
        create_correction(self.invoice_id)
        self.assertEqual(get_invoice_for_company_month(self.firma, "2026-10")["id"], self.invoice_id)

    def test_drafts_and_issued_sorted_by_company_short_name(self):
        from app.db.dao_conn import get_conn

        firma_a = self.make_company("Firma Pełna Alfa", nazwa_skrocona="Alfa")
        firma_b = self.make_company("Firma Pełna Beta", nazwa_skrocona="beta")
        for firma in (firma_a, firma_b):
            create_or_update_invoice(firma, "2026-10", POSITIONS, 520.0, 639.6)

        conn = get_conn()
        try:
            conn.execute(
                "UPDATE firmy SET nazwa_skrocona = %s WHERE id = %s",
                ("Zulu", self.firma),
            )
            conn.commit()
        finally:
            conn.close()

        drafts = list_invoices(1, False, "2026-10")
        self.assertEqual([r["nazwa_skrocona"] for r in drafts], ["Alfa", "beta", "Zulu"])
        mark_exported([r["id"] for r in drafts], 1)
        issued = list_invoices(1, True)
        self.assertEqual([r["nazwa_skrocona"] for r in issued], ["Alfa", "beta", "Zulu"])

    def test_ksef_number_round_trip(self):
        set_ksef_number(self.invoice_id, " 1234567890-20261015-ABCDEF-01 ")
        self.assertEqual(get_invoice_by_id(self.invoice_id)["ksef_numer"], "1234567890-20261015-ABCDEF-01")
        set_ksef_number(self.invoice_id, "")
        self.assertIsNone(get_invoice_by_id(self.invoice_id)["ksef_numer"])

    def test_every_employee_sees_all_companies_but_anonymous_sees_none(self):
        other = self.make_employee("obcy@example.com")
        self.assertEqual([i["id"] for i in list_invoices(other, False, "2026-10")], [self.invoice_id])
        self.assertEqual(list_invoices(None, False, "2026-10"), [])


class InvoiceUiTests(DbTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["biuro-tests"])

    def test_page_and_editor_open(self):
        from app.invoices.faktury_ui import FakturyPage
        from app.invoices.invoice_editor import InvoiceEditorDialog
        firma = self.make_company("Klient SA")
        invoice_id = create_or_update_invoice(firma, "2026-10", POSITIONS, 520.0, 639.6)
        page = FakturyPage(1)
        page.month_button.setText("10-2026")
        page.refresh()
        self.assertEqual(page.draft_table.rowCount(), 1)
        dialog = InvoiceEditorDialog(get_invoice_by_id(invoice_id))
        self.assertEqual(dialog.table.rowCount(), 2)
        dialog.table.item(0, 1).setText("3")
        self.assertEqual(dialog.table.item(0, 5).text(), "1500.00")
        with patch.object(QMessageBox, "warning"):
            dialog._save()
        self.assertEqual(get_invoice_by_id(invoice_id)["kwota_netto"], 1520.0)
        mark_exported([invoice_id], 1)
        page.refresh()
        self.assertEqual(page.issued_table.rowCount(), 1)
        self.assertTrue(InvoiceEditorDialog(get_invoice_by_id(invoice_id)).read_only)

    def test_quantity_can_be_edited_directly_in_draft_positions(self):
        from PyQt6.QtWidgets import QLineEdit
        from app.invoices.faktury_ui import FakturyPage

        firma = self.make_company("Klient SA")
        invoice_id = create_or_update_invoice(firma, "2026-10", POSITIONS, 520.0, 639.6)
        page = FakturyPage(1)
        page.month_button.setText("10-2026")
        page.refresh()
        page.draft_table.selectRow(0)
        self.assertEqual(page.positions_table.rowCount(), 2)
        quantity = page.positions_table.item(1, 1)
        self.assertTrue(quantity.flags() & Qt.ItemFlag.ItemIsEditable)

        delegate = page.positions_table.itemDelegateForColumn(1)
        editor = QLineEdit()
        editor.setText("7")
        delegate.setModelData(
            editor, page.positions_table.model(), page.positions_table.model().index(1, 1)
        )

        self.assertEqual(get_invoice_positions(invoice_id)[1]["ilosc"], 7.0)
        self.assertEqual(get_invoice_by_id(invoice_id)["kwota_netto"], 535.0)
        page.close()


if __name__ == "__main__":
    unittest.main()
