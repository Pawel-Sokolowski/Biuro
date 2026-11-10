import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from app.db.dao_conn import get_conn
from app.db.dao_invoices import create_manual_invoice, get_exported_invoices, mark_exported
from app.db.dao_monthly import list_active_substitutions, set_substitution
from app.db.dao_payments import PaymentError, get_invoice_payment_summary, record_cash_payment, revert_settlement
from tests.base import DbTestCase

_app = QApplication.instance() or QApplication(["biuro-tests"])

POSITIONS = [{"nazwa": "Usługa", "ilosc": 1, "cena_netto": 100, "vat": 23}]


class SubstitutionDeleteTests(DbTestCase):
    def _dialog(self, firma, owner):
        from app.monthly_view.monthly_view_ui import SubstitutionDialog
        row = {"id": firma, "nazwa": "Firma", "ksiegowa_id": owner, "kadrowa_id": None}
        return SubstitutionDialog(row, [(owner, "Kasia"), (self.sub, "Pawel")])

    def setUp(self):
        super().setUp()
        self.owner = self.make_employee("kasia@example.com", "Kasia", "A")
        self.sub = self.make_employee("pawel@example.com", "Pawel", "B")
        self.firma = self.make_company("Firma A", ksiegowa_id=self.owner)

    def test_delete_button_removes_only_selected_role(self):
        set_substitution(self.firma, "ksiegowosc", self.sub, self.owner)
        set_substitution(self.firma, "kadry", self.sub, None)
        dialog = self._dialog(self.firma, self.owner)
        self.assertTrue(dialog.btn_delete.isEnabled())
        dialog.btn_delete.click()
        active = list_active_substitutions()
        self.assertNotIn((self.firma, "ksiegowosc"), active)
        self.assertIn((self.firma, "kadry"), active)

    def test_delete_button_disabled_without_substitution(self):
        dialog = self._dialog(self.firma, self.owner)
        self.assertFalse(dialog.btn_delete.isEnabled())

    def test_delete_button_follows_selected_role(self):
        set_substitution(self.firma, "kadry", self.sub, None)
        dialog = self._dialog(self.firma, self.owner)
        self.assertFalse(dialog.btn_delete.isEnabled())
        dialog.role.setCurrentIndex(dialog.role.findData("kadry"))
        self.assertTrue(dialog.btn_delete.isEnabled())

    def test_clear_substitution_restores_owner_only_visibility(self):
        from app.monthly_view.monthly_view_logic import MonthlyViewLogic
        set_substitution(self.firma, "ksiegowosc", self.sub, self.owner)
        logic = MonthlyViewLogic(self.sub)
        self.assertEqual([r["id"] for r in logic.get_monthly_rows("2026-10", self.sub, "Wszystkie")], [self.firma])
        dialog = self._dialog(self.firma, self.owner)
        dialog.btn_delete.click()
        self.assertEqual(logic.get_monthly_rows("2026-10", self.sub, "Wszystkie"), [])


class ProformaAndAdvanceTests(DbTestCase):
    def _document(self, kind, issue=True):
        firma = self.make_company("Klient")
        invoice = create_manual_invoice("firma", firma, POSITIONS, "2026-09-01", "2026-09-01", "2026-09-15", rodzaj=kind)
        if issue:
            mark_exported([invoice], 1)
        return invoice

    def _row(self, invoice_id):
        conn = get_conn()
        try:
            return dict(conn.execute("SELECT * FROM faktury WHERE id = %s", (invoice_id,)).fetchone())
        finally:
            conn.close()

    def _children(self, invoice_id):
        conn = get_conn()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM faktury WHERE dokument_zrodlowy_id = %s", (invoice_id,)).fetchall()]
        finally:
            conn.close()

    def test_documents_have_kind_and_own_number_prefix(self):
        proforma = self._row(self._document("proforma"))
        advance = self._row(self._document("zaliczkowa"))
        regular = self._row(self._document("faktura"))
        self.assertEqual((proforma["rodzaj"], advance["rodzaj"], regular["rodzaj"]), ("proforma", "zaliczkowa", "faktura"))
        self.assertTrue(proforma["numer"].startswith("PRO/0001/"))
        self.assertTrue(advance["numer"].startswith("FZ/0001/"))
        self.assertTrue(regular["numer"].startswith("FV/0001/"))

    def test_unknown_kind_is_rejected(self):
        firma = self.make_company("Klient")
        with self.assertRaises(ValueError):
            create_manual_invoice("firma", firma, POSITIONS, "2026-09-01", "2026-09-01", "2026-09-15", rodzaj="korekta")

    def test_full_payment_creates_draft_regular_invoice_once(self):
        proforma = self._document("proforma")
        record_cash_payment(proforma, "2026-09-05", "50.00", 1)
        self.assertEqual(self._children(proforma), [])
        record_cash_payment(proforma, "2026-09-06", "73.00", 1)
        children = self._children(proforma)
        self.assertEqual(len(children), 1)
        draft = children[0]
        self.assertEqual((draft["rodzaj"], draft["status"], draft["eksportowana"]), ("faktura", "robocza", 0))
        self.assertAlmostEqual(draft["kwota_brutto"], 123.0)
        self.assertEqual(draft["firma_id"], self._row(proforma)["firma_id"])
        self.assertEqual(draft["pozycje_json"], self._row(proforma)["pozycje_json"])
        self.assertFalse(draft["numer"])
        with self.assertRaises(PaymentError):
            record_cash_payment(proforma, "2026-09-07", "1.00", 1)
        self.assertEqual(len(self._children(proforma)), 1)

    def test_advance_invoice_payment_creates_draft_too(self):
        advance = self._document("zaliczkowa")
        record_cash_payment(advance, "2026-09-05", "123.00", 1)
        self.assertEqual(len(self._children(advance)), 1)

    def test_regular_invoice_payment_does_not_create_documents(self):
        regular = self._document("faktura")
        record_cash_payment(regular, "2026-09-05", "123.00", 1)
        self.assertEqual(self._children(regular), [])

    def test_reverting_and_repaying_does_not_duplicate_draft(self):
        proforma = self._document("proforma")
        record_cash_payment(proforma, "2026-09-05", "123.00", 1)
        payment_id = get_invoice_payment_summary(proforma, 1)["payments"][0]["id"]
        revert_settlement(payment_id, 1)
        record_cash_payment(proforma, "2026-09-06", "123.00", 1)
        self.assertEqual(len(self._children(proforma)), 1)

    def test_proforma_is_not_in_exported_set_for_accounting(self):
        proforma = self._document("proforma")
        advance = self._document("zaliczkowa")
        ids = {row["id"] for row in get_exported_invoices()}
        self.assertNotIn(proforma, ids)
        self.assertIn(advance, ids)

    def test_unissued_document_cannot_be_paid(self):
        proforma = self._document("proforma", issue=False)
        with self.assertRaises(PaymentError):
            record_cash_payment(proforma, "2026-09-05", "123.00", 1)

    def test_proforma_is_not_sent_to_ksef(self):
        from app.invoices.fa3_builder import KsefError, build_fa3_xml
        invoice = self._row(self._document("proforma"))
        with self.assertRaisesRegex(KsefError, 'Nieobsługiwany rodzaj'):
            build_fa3_xml(invoice, [], {})

    def test_manual_dialog_offers_document_kinds(self):
        from app.invoices.manual_invoice_dialog import ManualInvoiceDialog
        dialog = ManualInvoiceDialog()
        self.assertEqual([dialog.kind.itemData(i) for i in range(dialog.kind.count())],
                         ["faktura", "zaliczkowa", "proforma"])


if __name__ == "__main__":
    unittest.main()
