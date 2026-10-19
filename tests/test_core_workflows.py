import unittest
from unittest.mock import patch

from app.db.dao_conn import get_conn
from tests.base import DbTestCase
from app.services.calculator import oblicz_miesieczny_koszt
from app.utils.importers import resolve_accounting_cennik_typ, split_address_line
from app.utils.months import month_bounds
from app.utils.timer import TimerUtils
from app.invoices.faktury_logic import FakturyLogic


class CoreWorkflowTests(unittest.TestCase):
    def test_import_address_parser(self):
        self.assertEqual(split_address_line("ul. Testowa 12/4"), ("ul. Testowa", "12", "4"))

    def test_accounting_cennik_typ_from_tax_and_activity(self):
        self.assertEqual(resolve_accounting_cennik_typ("Skala podatkowa", "Osoba fizyczna (KPiR)"), "KPIR")
        self.assertEqual(resolve_accounting_cennik_typ("Podatek liniowy", "Spółka Cywilna"), "KPIR")
        self.assertEqual(
            resolve_accounting_cennik_typ("Ryczałt ewidencjonowany", "Osoba fizyczna (KPiR)"),
            "RYCZALT",
        )
        self.assertEqual(resolve_accounting_cennik_typ("CIT 9%", "Osoba prawna - spółka z o. o"), "KH")
        self.assertEqual(resolve_accounting_cennik_typ("", "Osoba prawna - spółka z o. o"), "KH")
        self.assertEqual(resolve_accounting_cennik_typ("", "Spółka Cywilna"), "KPIR")
        self.assertEqual(resolve_accounting_cennik_typ("", "Spółka Jawna"), "KH")
        self.assertEqual(resolve_accounting_cennik_typ("", "Fundacja"), "KH")
        self.assertEqual(resolve_accounting_cennik_typ("", "Osoba fizyczna (KPiR)"), "KPIR")

    def test_month_bounds_are_index_friendly(self):
        self.assertEqual(month_bounds("2026-02"), ("2026-02-01", "2026-03-01"))

    def test_timer_formatting(self):
        self.assertEqual(TimerUtils.format_seconds(3661), "01:01:01")

    def test_invoice_includes_only_billable_extra_service(self):
        logic = FakturyLogic.__new__(FakturyLogic)
        with patch.object(logic, "_get_company", return_value={
            "id": 1, "nazwa": "Test", "glowny_cennik": None, "fakturowac": 0,
        }), patch.object(logic, "_get_month_status", return_value=None), \
             patch.object(logic, "_get_extra_services", return_value=[{
                 "nazwa": "Usługa dodatkowa", "kategoria": "extra",
                 "stawka_vat": "23", "ilosc": 2, "cena_netto": 50,
             }]):
            positions = logic.generate_invoice_positions(1, "2026-02")
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["netto"], 100.0)
        self.assertEqual(positions[0]["vat"], 23)


class MonthlyCostTests(DbTestCase):
    def test_monthly_cost_uses_document_date_range(self):
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO cenniki (klucz, cena_netto_ksiegowosc, cena_brutto_ksiegowosc, "
                "cena_za_dokument_ponad_limit, zdrowotna, netto, brutto) "
                "VALUES ('basic', 100, 123, 10, 50, 100, 123)"
            )
            conn.commit()
        finally:
            conn.close()
        firma = self.make_company(
            "Firma", typ_rozliczenia="KPIR", vat_czynny=1, limit_dokumentow=1, glowny_cennik="basic"
        )
        conn = get_conn()
        try:
            conn.execute("INSERT INTO dokumenty (firma_id, data_wyst) VALUES (%s, '2026-02-10')", (firma,))
            conn.execute("INSERT INTO dokumenty (firma_id, data_wyst) VALUES (%s, '2026-03-01')", (firma,))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(oblicz_miesieczny_koszt(firma, "2026-02")["netto"], 100.0)


if __name__ == "__main__":
    unittest.main()
