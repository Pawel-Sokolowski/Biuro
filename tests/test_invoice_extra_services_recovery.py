"""Ochrona usług dodatkowych przy wycofaniu faktury i regenerowaniu pozycji."""
import json

from app.db.dao_conn import get_conn
from app.db.dao_invoices import (
    add_invoice_item_for_service,
    create_or_update_invoice,
    delete_draft_invoice,
    delete_issued_invoice,
    delete_invoice_keep_positions,
    get_invoice_by_id,
    mark_exported,
)
from app.invoices.faktury_logic import FakturyLogic
from tests.base import DbTestCase


class ExtraServicesInvoiceRecoveryTests(DbTestCase):
    MONTH = "2026-10"

    def setUp(self):
        super().setUp()
        self.firma = self.make_company(
            "Klient z usługą", fakturowac=1, stala_kwota_ksiegowosc=100
        )
        conn = get_conn()
        try:
            self.service = conn.execute(
                "INSERT INTO uslugi_dodatkowe (nazwa, cena_netto, stawka_vat, kategoria) "
                "VALUES ('Aktualizacja VAT-R', 60, '23', 'dodatkowe') RETURNING id"
            ).fetchone()[0]
            conn.commit()
        finally:
            conn.close()

    def _ledger(self):
        conn = get_conn()
        try:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM uslugi_dodatkowe_firma "
                "WHERE firma_id = %s AND miesiac = %s ORDER BY id",
                (self.firma, self.MONTH),
            ).fetchall()]
        finally:
            conn.close()

    def _positions(self, invoice_id):
        return json.loads(get_invoice_by_id(invoice_id)["pozycje_json"])

    def _generate(self):
        return FakturyLogic().generate_and_save_invoice(self.firma, self.MONTH)["id"]

    def test_delete_draft_preserves_extra_service_and_regenerates(self):
        invoice_id = add_invoice_item_for_service(
            self.firma, self.service, 2, self.MONTH, cena_netto=60,
            jednostka="szt.",
        )
        self.assertIsNotNone(invoice_id)
        generated_id = self._generate()
        self.assertEqual(invoice_id, generated_id)
        before = self._positions(generated_id)
        self.assertEqual(len(before), 2)  # księgowość + dodatkowa, bez duplikacji
        self.assertEqual(sum(p["netto"] for p in before), 220.0)

        delete_draft_invoice(generated_id)
        self.assertEqual(len(self._ledger()), 1)
        self.assertIsNone(get_invoice_by_id(generated_id))

        recreated_id = self._generate()
        after = self._positions(recreated_id)
        self.assertEqual(len(after), 2)
        self.assertEqual(sum(p["netto"] for p in after), 220.0)
        self.assertEqual(len(self._ledger()), 1)

    def test_delete_issued_preserves_fractional_timer_service(self):
        invoice_id = add_invoice_item_for_service(
            self.firma, self.service, 0.5, self.MONTH, cena_netto=60,
        )
        generated_id = self._generate()
        mark_exported([invoice_id], 1)
        delete_issued_invoice(generated_id)

        rows = self._ledger()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ilosc"], 0.5)
        self.assertEqual(rows[0]["jednostka"], "godz.")

        recreated_id = self._generate()
        positions = self._positions(recreated_id)
        extra = [p for p in positions if p.get("usluga_id") == self.service]
        self.assertEqual(len(extra), 1)
        self.assertEqual(extra[0]["ilosc"], 0.5)
        self.assertEqual(extra[0]["jednostka"], "godz.")
        self.assertEqual(extra[0]["netto"], 30.0)
        self.assertEqual(len(positions), 2)

    def test_legacy_invoice_only_service_is_restored_when_deleted(self):
        legacy = {
            "nazwa": "Aktualizacja VAT-R", "usluga_id": self.service,
            "ilosc": 1, "jednostka": "szt.", "netto": 60,
            "cena_netto": 60, "vat": 23, "brutto": 73.8,
            "generated": False,
        }
        invoice_id = create_or_update_invoice(
            self.firma, self.MONTH, [legacy], 60, 73.8
        )
        self.assertEqual(len(self._ledger()), 0)

        delete_draft_invoice(invoice_id)
        self.assertEqual(len(self._ledger()), 1)

        recreated = self._generate()
        positions = self._positions(recreated)
        self.assertEqual(len([p for p in positions if p.get("usluga_id") == self.service]), 1)

        # Ponowna regeneracja ani kolejne wycofanie nie powinny dublować usługi.
        self._generate()
        self.assertEqual(len(self._positions(recreated)), 2)
        delete_draft_invoice(recreated)
        self.assertEqual(len(self._ledger()), 1)

    def test_issued_invoice_keeps_new_service_added_after_issue(self):
        generated = self._generate()
        mark_exported([generated], 1)
        # Wystawionego dokumentu nie zmieniamy, ale usługę zapisujemy.
        self.assertIsNone(add_invoice_item_for_service(
            self.firma, self.service, 1, self.MONTH, cena_netto=60,
        ))
        self.assertEqual(len(self._ledger()), 1)
        delete_issued_invoice(generated)
        recreated = self._generate()
        self.assertEqual(len(self._positions(recreated)), 2)

    def test_manual_extra_line_survives_invoice_withdrawal(self):
        # Pozycja wpisana ręcznie w edytorze nie ma identyfikatora katalogu.
        # Powinna zostać odtworzona z archiwum, nie z faktury_pozycje.
        invoice_id = create_or_update_invoice(
            self.firma, self.MONTH,
            [
                {"nazwa": "Usługa księgowa", "ilosc": 1, "netto": 100,
                 "brutto": 123, "vat": 23, "generated": True,
                 "kategoria": "księgowość"},
                {"nazwa": "Aktualizacja danych - ręcznie", "ilosc": 1,
                 "netto": 75, "brutto": 92.25, "vat": 23,
                 "generated": False, "kategoria": "dodatkowe"},
            ],
            175, 215.25,
        )
        mark_exported([invoice_id], 1)
        delete_issued_invoice(invoice_id)

        recreated = self._generate()
        positions = self._positions(recreated)
        manual = [p for p in positions if p["nazwa"] == "Aktualizacja danych - ręcznie"]
        main = [p for p in positions if p["nazwa"] == "Usługa księgowa"]
        self.assertEqual(len(manual), 1)
        self.assertEqual(manual[0]["netto"], 75)
        self.assertEqual(len(main), 1)
        self.assertEqual(sum(p["netto"] for p in positions), 175)

        delete_draft_invoice(recreated)
        regenerated = self._generate()
        self.assertEqual(len(self._positions(regenerated)), 2)

    def test_archival_delete_keeps_service(self):
        invoice_id = add_invoice_item_for_service(
            self.firma, self.service, 1, self.MONTH, cena_netto=60,
        )
        self.assertTrue(delete_invoice_keep_positions(invoice_id))
        self.assertEqual(len(self._ledger()), 1)


if __name__ == "__main__":
    import unittest
    unittest.main()
