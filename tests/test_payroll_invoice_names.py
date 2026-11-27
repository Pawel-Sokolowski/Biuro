"""Nazwy pozycji kadrowych zależą od umowy i zakresu wybranego cennika."""
import json
from decimal import Decimal
import unittest
from unittest.mock import patch

from app.db.dao_conn import get_conn
from app.db.dao_invoices import (
    create_or_update_invoice, get_invoice_by_id, mark_exported,
)
from app.invoices.faktury_logic import FakturyLogic
from app.services.payroll_names import payroll_invoice_service_name
from tests.base import DbTestCase


class PayrollNameTests(unittest.TestCase):
    def test_exact_names_for_contract_and_service_scope(self):
        cases = [
            ("uop", "UOP_100", "Usługa kadrowo-płacowa - UoP", "Usługa kadrowo-płacowa – UoP"),
            ("uz", "UZ_100", "Usługa kadrowo-płacowa - UZ", "Usługa kadrowo-płacowa – UZ"),
            ("uop", "UOP_40", "Usługa płacowa - UoP", "Usługa płacowa – UoP"),
            ("uz", "UZ_40", "Usługa płacowa - UZ", "Usługa płacowa – UZ"),
        ]
        for contract, key, description, expected in cases:
            with self.subTest(contract=contract, description=description):
                self.assertEqual(payroll_invoice_service_name(contract, key, description), expected)

    def test_legacy_keys_and_missing_descriptions(self):
        cases = [
            ("uop", "UOP_40", None, "Usługa kadrowo-płacowa – UoP"),
            ("uz", "USLUGA_PLACOWA_UZ_30", "", "Usługa płacowa – UZ"),
            ("uop", "Usługa płacowa - UoP 40,00", None, "Usługa płacowa – UoP"),
            ("uz", "KADROWO_PLACOWA_UZ", "", "Usługa kadrowo-płacowa – UZ"),
        ]
        for contract, key, description, expected in cases:
            with self.subTest(key=key):
                self.assertEqual(payroll_invoice_service_name(contract, key, description), expected)

    def test_description_overrides_legacy_key_scope(self):
        self.assertEqual(
            payroll_invoice_service_name("uz", "PLACOWA_UZ_40", "Pełna obsługa kadrowo-płacowa"),
            "Usługa kadrowo-płacowa – UZ",
        )
        self.assertEqual(
            payroll_invoice_service_name("uop", "KADRY_UOP_100", "Obsługa płacowa"),
            "Usługa płacowa – UoP",
        )

    def test_uod_preserves_existing_description_and_fallback(self):
        self.assertEqual(payroll_invoice_service_name("uod", "UOD_40", "Umowy o dzieło"), "Umowy o dzieło")
        self.assertEqual(
            payroll_invoice_service_name("uod", "UOD_40", None),
            "Usługa kadrowo-płacowa - Umowa o dzieło",
        )

    def test_fixed_monthly_fee_keeps_generic_name(self):
        with patch.object(FakturyLogic, "_kadry_vat", return_value=23):
            positions = FakturyLogic()._kadry_positions({}, 210)
        self.assertEqual(positions[0]["nazwa"], "Usługa kadrowo-płacowa")
        self.assertEqual((positions[0]["ilosc"], positions[0]["netto"], positions[0]["brutto"]), (1, 210, 258.3))

    def test_january_invoice_names_payroll_in_january_and_overage_in_december(self):
        logic = FakturyLogic()
        company = {"fakturowac": 1, "glowny_cennik": "test", "limit_dokumentow": 20,
                   "stala_kwota_kadry": 210}
        with patch.object(logic, "_get_company", return_value=company), \
             patch.object(logic, "_get_extra_services", return_value=[]), \
             patch.object(logic, "_get_main_pricing", return_value={"cena_netto_ksiegowosc": 382.5,
                                                                  "cena_za_dokument_ponad_limit": 5.5}), \
             patch.object(logic, "_accounting_vat", return_value=23), \
             patch.object(logic, "_kadry_vat", return_value=23), \
             patch("app.db.dao_companies.count_zdrowotna_units", return_value=1), \
             patch("app.invoices.faktury_logic.count_documents_for_month", return_value=74) as count:
            positions = logic.generate_invoice_positions(1, "2027-01")
        count.assert_called_once_with(1, "2026-12")
        self.assertEqual([item["nazwa"] for item in positions], [
            "Usługa księgowa",
            "Cena ponad limit dokumentów 20 szt. grudzień 2026r., 74 dokumenty",
            "Usługa kadrowo-płacowa styczeń 2027r.",
        ])
        self.assertEqual((positions[1]["ilosc"], positions[1]["netto"]), (54, 297))


class PayrollInvoiceNamePersistenceTests(DbTestCase):
    MONTH = "2026-10"

    def setUp(self):
        super().setUp()
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO cenniki_kadry (klucz, cena_netto_kadry, opis) VALUES (%s, %s, %s)",
                ("UOP_40", 40, "Usługa płacowa - UoP"),
            )
            conn.commit()
        finally:
            conn.close()
        self.firma = self.make_company(
            "Klient kadrowy", fakturowac=1, uop_ilosc=2, uop_cennik="UOP_40"
        )
        legacy = {
            "nazwa": "Usługa płacowa - UoP", "ilosc": 2, "jednostka": "szt.",
            "netto": 80, "vat": 23, "brutto": 98.4, "kategoria": "kadry", "generated": True,
        }
        self.invoice = create_or_update_invoice(self.firma, self.MONTH, [legacy], 80, 98.4)

    def test_regenerated_draft_uses_new_name_and_same_amounts(self):
        result = FakturyLogic().generate_and_save_invoice(self.firma, self.MONTH)
        self.assertEqual(result["id"], self.invoice)
        saved = get_invoice_by_id(self.invoice)
        positions = json.loads(saved["pozycje_json"])
        self.assertEqual([p["nazwa"] for p in positions], ["Usługa płacowa – UoP październik 2026r."])
        self.assertEqual((positions[0]["ilosc"], saved["kwota_netto"], saved["kwota_brutto"]), (2, 80.0, 98.4))

    def test_issued_document_keeps_legacy_name(self):
        mark_exported([self.invoice], 1)
        before = get_invoice_by_id(self.invoice)
        generated = FakturyLogic().generate_invoice_positions(self.firma, self.MONTH)
        self.assertEqual(generated[0]["nazwa"], "Usługa płacowa – UoP październik 2026r.")
        result = FakturyLogic().generate_and_save_invoice(self.firma, self.MONTH)
        self.assertTrue(result["skipped"])
        after = get_invoice_by_id(self.invoice)
        self.assertEqual(after["pozycje_json"], before["pozycje_json"])
        self.assertEqual((after["kwota_netto"], after["kwota_brutto"]), (80.0, 98.4))

    def test_regeneration_uses_contracts_for_invoice_month_not_latest_company_counts(self):
        from app.db.dao_monthly import save_month_contracts

        save_month_contracts(self.firma, self.MONTH, 3, 0, 0)
        save_month_contracts(self.firma, "2026-11", 8, 0, 0)
        result = FakturyLogic().generate_and_save_invoice(self.firma, self.MONTH)
        saved = get_invoice_by_id(result["id"])
        position, = json.loads(saved["pozycje_json"])
        self.assertEqual((position["ilosc"], saved["kwota_netto"], saved["kwota_brutto"]), (3, 120, 147.6))


if __name__ == "__main__":
    unittest.main()
