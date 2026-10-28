"""Testy bezpiecznego, globalnego uzupełniania danych rejestrowych."""
import unittest

from app.companies.registry_enrichment import (
    build_missing_registry_updates,
    enrich_all_companies,
)
from app.db.dao_companies import get_company
from tests.base import DbTestCase


class RegistryUpdatePlanTests(unittest.TestCase):
    def test_fills_only_missing_registry_fields(self):
        company = {
            "forma_dzialalnosci": "Spółka cywilna",
            "nazwa": "Nazwa własna", "miasto": "Szczecin",
            "ulica": "Nowa", "adres_budynek": "",
            "konto_bankowe": "Zapisany rachunek", "status_vat": "Czynny",
            "vat_czynny": 1, "uop_ilosc": 22, "limit_dokumentow": 10,
            "adres_zam_miasto": "Własny adres",
        }
        registry = {
            "nazwa": "Inna nazwa", "nip": "8522669775",
            "dz_miasto": "Szczecin", "dz_ulica": "Nowa 23/1",
            "regon": "387974972", "krs": "0000000001",
            "koresp_ulica": "Korespondencyjna 4/3",
            "status_vat": "Zwolniony", "konto_bankowe": "Inny rachunek",
            "uop_ilosc": 99, "limit_dokumentow": 100,
            "adres_zam_miasto": "Obcy adres",
        }
        updates = build_missing_registry_updates(company, registry)
        self.assertEqual(updates["regon"], "387974972")
        self.assertEqual(updates["adres_budynek"], "23")
        self.assertEqual(updates["adres_lokal"], "1")
        self.assertEqual(updates["adres_koresp_ulica"], "Korespondencyjna")
        self.assertEqual(updates["adres_koresp_budynek"], "4")
        self.assertEqual(updates["adres_koresp_lokal"], "3")
        for forbidden in (
            "nazwa", "miasto", "ulica", "krs", "status_vat", "vat_czynny",
            "konto_bankowe", "uop_ilosc", "limit_dokumentow", "adres_zam_miasto",
        ):
            self.assertNotIn(forbidden, updates)

    def test_conflicting_street_or_city_cannot_mix_address_parts(self):
        current = {
            "forma_dzialalnosci": "JDG", "miasto": "Szczecin",
            "ulica": "Stara", "adres_budynek": "", "kod_pocztowy": "",
        }
        registry = {
            "dz_miasto": "Szczecin", "dz_ulica": "Nowa 23/1",
            "dz_kod": "70-001", "dz_budynek": "23", "dz_lokal": "1",
            "regon": "123456789",
        }
        updates = build_missing_registry_updates(current, registry)
        self.assertEqual(updates, {"regon": "123456789"})

        # Wystarczy sprzeczne miasto, nawet jeśli ulica jeszcze nieznana.
        updates = build_missing_registry_updates(
            {"miasto": "Szczecin"}, {
                "dz_miasto": "Poznań", "dz_ulica": "Kwiatowa 23/1",
            },
        )
        self.assertEqual(updates, {})

    def test_vat_fills_unknown_status_and_derived_flag(self):
        updates = build_missing_registry_updates(
            {"forma_dzialalnosci": "Spółka z o.o.", "status_vat": "Brak danych", "vat_czynny": 0},
            {"status_vat": "Czynny"},
        )
        self.assertEqual(updates, {"status_vat": "Czynny", "vat_czynny": 1})

    def test_error_and_missing_vat_do_not_erase_anything(self):
        original = {"status_vat": "Czynny", "konto_bankowe": "PL123"}
        self.assertEqual(build_missing_registry_updates(original, {"error": True}), {})
        self.assertEqual(
            build_missing_registry_updates(original, {"status_vat": "Brak danych", "konto_bankowe": ""}),
            {},
        )

    def test_jdg_short_name_only_if_missing(self):
        from app.companies.company_names import sole_proprietor_short_name
        expected = sole_proprietor_short_name("Jan", "Kowalski")
        self.assertEqual(build_missing_registry_updates(
            {"forma_dzialalnosci": "JDG", "nazwa_skrocona": ""},
            {"imie": "Jan", "nazwisko": "Kowalski"},
        )["nazwa_skrocona"], expected)
        self.assertNotIn("nazwa_skrocona", build_missing_registry_updates(
            {"forma_dzialalnosci": "Spółka cywilna", "nazwa_skrocona": ""},
            {"imie": "Jan", "nazwisko": "Kowalski"},
        ))


class RegistryBatchTests(DbTestCase):
    def test_global_enrichment_preserves_existing_data_and_is_repeatable(self):
        firma = self.make_company(
            "Moja Spółka Cywilna",
            nip="8522669775", forma_dzialalnosci="Spółka cywilna",
            miasto="Szczecin", ulica="Zapisana ulica",
            uop_ilosc=22, uz_ilosc=4, uod_ilosc=2,
            limit_dokumentow=300, konto_bankowe="PL123",
            status_vat="Brak danych", vat_czynny=0,
        )
        called = []
        progress = []
        def fake_lookup(nip, forma_dzialalnosci):
            called.append((nip, forma_dzialalnosci))
            return {
                "nip": nip, "regon": "387974972", "nazwa": "Zmieniona",
                "dz_miasto": "Szczecin", "dz_ulica": "Zapisana ulica 23/1",
                "koresp_ulica": "Szkolna 5/2", "status_vat": "Czynny",
                "konto_bankowe": "Nowy rachunek", "krs": "0000000001",
                "adres_zam_miasto": "Błędny", "uop_ilosc": 999,
            }

        result = enrich_all_companies(
            progress=lambda done, total, name: progress.append((done, total, name)),
            lookup=fake_lookup,
        )
        self.assertIn(("8522669775", "Spółka cywilna"), called)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["errors"], [])
        self.assertEqual(progress[-1][0], result["total"])
        company = get_company(firma)
        self.assertEqual(company["regon"], "387974972")
        self.assertEqual(company["nazwa"], "Moja Spółka Cywilna")
        self.assertEqual(company["miasto"], "Szczecin")
        self.assertEqual(company["ulica"], "Zapisana ulica")
        self.assertEqual(company["adres_budynek"], "23")
        self.assertEqual(company["adres_lokal"], "1")
        self.assertEqual(company["adres_koresp_ulica"], "Szkolna")
        self.assertEqual(company["adres_koresp_budynek"], "5")
        self.assertEqual(company["adres_koresp_lokal"], "2")
        self.assertEqual(company["konto_bankowe"], "PL123")
        self.assertEqual(company["status_vat"], "Czynny")
        self.assertEqual(company["vat_czynny"], 1)
        self.assertEqual((company["uop_ilosc"], company["uz_ilosc"], company["uod_ilosc"]), (22, 4, 2))
        self.assertEqual(company["limit_dokumentow"], 300)
        self.assertFalse(company["krs"])
        self.assertFalse(company["adres_zam_miasto"])

        again = enrich_all_companies(lookup=fake_lookup)
        self.assertEqual(again["updated"], 0)
        self.assertEqual(again["errors"], [])

    def test_rereads_data_after_network_lookup_before_writing(self):
        from app.db.dao_conn import get_conn
        firma = self.make_company("Test", nip="1234563218", forma_dzialalnosci="JDG")
        def concurrent_change(nip, forma_dzialalnosci):
            conn = get_conn()
            try:
                conn.execute("UPDATE firmy SET miasto = 'Wpis pracownika' WHERE id = %s", (firma,))
                conn.commit()
            finally:
                conn.close()
            return {"nip": nip, "dz_miasto": "Wpis CEIDG", "regon": "123456789"}

        enrich_all_companies(lookup=concurrent_change)
        company = get_company(firma)
        self.assertEqual(company["miasto"], "Wpis pracownika")
        self.assertEqual(company["regon"], "123456789")

    def test_one_registry_failure_does_not_stop_other_companies(self):
        first = self.make_company("Awaria", nip="1234563218", forma_dzialalnosci="JDG")
        second = self.make_company("Działa", nip="8522669775", forma_dzialalnosci="Spółka cywilna")
        def fake_lookup(nip, forma_dzialalnosci):
            if nip == "1234563218":
                raise RuntimeError("Brak połączenia")
            return {"nip": nip, "regon": "387974972"}

        result = enrich_all_companies(lookup=fake_lookup)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["updated"], 1)
        self.assertFalse(get_company(first)["regon"])
        self.assertEqual(get_company(second)["regon"], "387974972")

    def test_cancellation_stops_before_next_company(self):
        self.make_company("Pierwsza", nip="1234563218", forma_dzialalnosci="JDG")
        self.make_company("Druga", nip="8522669775", forma_dzialalnosci="Spółka cywilna")
        seen = []
        result = enrich_all_companies(
            should_cancel=lambda: len(seen) > 0,
            lookup=lambda nip, forma_dzialalnosci: (
                seen.append(nip) or {"nip": nip, "regon": "123456789"}
            ),
        )
        self.assertEqual(len(seen), 1)
        self.assertTrue(result["cancelled"])
        self.assertLess(result["processed"], result["total"])


if __name__ == "__main__":
    unittest.main()
