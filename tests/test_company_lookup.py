import unittest

from app.companies.biala_lista_api import parse_subject
from app.companies.company_lookup import lookup_company_by_nip

ERR = {"error": True, "message": "x"}


class LookupTests(unittest.TestCase):
    def test_parse_subject(self):
        data = parse_subject({
            "name": "EM-KA", "nip": "8522605060", "statusVat": "Czynny", "regon": "321442130",
            "krs": "0000482087", "workingAddress": "FRYDERYKA CHOPINA 35, 71-450 SZCZECIN",
            "accountNumbers": ["11111111111111111111111111"],
        })
        self.assertEqual(data["status_vat"], "Czynny")
        self.assertEqual(data["dz_kod"], "71-450")
        self.assertEqual(data["dz_miasto"], "Szczecin")
        self.assertEqual(data["konto_bankowe"], "11111111111111111111111111")

    def test_polish_street_address_splitting(self):
        from app.companies.address_parsing import split_street_address

        cases = [
            ("Ul. Kwiatowa 23/1", "", "", ("Ul. Kwiatowa", "23", "1")),
            ("ul. 3 Maja 12", "", "", ("ul. 3 Maja", "12", "")),
            ("Kwiatowa 23A/12", "", "", ("Kwiatowa", "23A", "12")),
            ("Kwiatowa 23", "", "", ("Kwiatowa", "23", "")),
            ("Kwiatowa 23/1", "23", "1", ("Kwiatowa", "23", "1")),
            ("ul. 23 Stycznia", "", "", ("ul. 23 Stycznia", "", "")),
            ("Kwiatowa", "23", "1", ("Kwiatowa", "23", "1")),
            ("Kwiatowa 23/1", "24", "2", ("Kwiatowa 23/1", "24", "2")),
        ]
        for street, building, apartment, expected in cases:
            with self.subTest(street=street, building=building, apartment=apartment):
                self.assertEqual(
                    split_street_address(street, building, apartment), expected
                )

    def test_all_registers_return_separate_street_building_and_apartment(self):
        import os
        from unittest.mock import patch
        from app.companies.ceidg_api import get_ceidg_firma_by_nip
        from app.companies.gus_api import parse_search_result
        from app.companies.krs_api import parse_odpis

        with patch.dict(os.environ, {
            "CEIDG_API_KEY": "test",
            "CEIDG_BASE_URL": "https://example.invalid",
        }), patch("app.companies.ceidg_api.requests.get") as get:
            get.return_value.status_code = 200
            get.return_value.json.return_value = {"firma": [{
                "status": "AKTYWNY", "nazwa": "Firma Test",
                "adresDzialalnosci": {
                    "ulica": "Ul. Kwiatowa 23/1", "miasto": "Szczecin",
                },
                "adresKorespondencyjny": {
                    "ulica": "ul. Szkolna 4A/2", "miasto": "Police",
                },
            }]}
            ceidg_data = get_ceidg_firma_by_nip("1234563218")
        self.assertEqual(
            (ceidg_data["dz_ulica"], ceidg_data["dz_budynek"], ceidg_data["dz_lokal"]),
            ("Ul. Kwiatowa", "23", "1"),
        )
        self.assertEqual(
            (ceidg_data["koresp_ulica"], ceidg_data["koresp_budynek"], ceidg_data["koresp_lokal"]),
            ("ul. Szkolna", "4A", "2"),
        )

        gus = parse_search_result(
            "<root><dane><Nazwa>Firma</Nazwa><Ulica>Ul. Kwiatowa</Ulica>"
            "<NrNieruchomosci>23</NrNieruchomosci><NrLokalu>1</NrLokalu>"
            "</dane></root>"
        )
        self.assertEqual(
            (gus["dz_ulica"], gus["dz_budynek"], gus["dz_lokal"]),
            ("Ul. Kwiatowa", "23", "1"),
        )

        krs = parse_odpis({
            "odpis": {"dane": {"dzial1": {"siedzibaIAdres": {"adres": {
                "ulica": "Ul. Kwiatowa", "nrDomu": "23", "nrLokalu": "1",
            }}}}}
        })
        self.assertEqual(
            (krs["dz_ulica"], krs["dz_budynek"], krs["dz_lokal"]),
            ("Ul. Kwiatowa", "23", "1"),
        )

        wl = parse_subject({
            "workingAddress": "UL. KWIATOWA 23/1, 70-001 SZCZECIN",
        })
        self.assertEqual(
            (wl["dz_ulica"], wl["dz_budynek"], wl["dz_lokal"]),
            ("Ul. Kwiatowa", "23", "1"),
        )

    def test_ceidg_form_populates_flat_correspondence_fields(self):
        from types import SimpleNamespace
        from app.companies.company_logic import ceidg_apply

        class Field:
            value = ""
            def setText(self, value):
                self.value = value

        inputs = {f"{prefix}_{field}": Field()
                  for prefix in ("dz", "ko")
                  for field in ("ulica", "budynek", "lokal", "wojewodztwo", "powiat", "gmina", "kraj")}
        form = SimpleNamespace(inputs=inputs)
        ceidg_apply(form, {
            "dz_ulica": "Ul. Kwiatowa 23/1",
            "koresp_ulica": "Ul. Szkolna 4/2",
            "koresp_budynek": "4",
            "koresp_lokal": "2",
            "koresp_wojewodztwo": "zachodniopomorskie",
            "koresp_powiat": "policki",
            "koresp_gmina": "Police",
            "koresp_kraj": "Polska",
        })
        self.assertEqual(
            tuple(inputs[f"dz_{field}"].value for field in ("ulica", "budynek", "lokal")),
            ("Ul. Kwiatowa", "23", "1"),
        )
        self.assertEqual(
            tuple(inputs[f"ko_{field}"].value for field in ("ulica", "budynek", "lokal")),
            ("Ul. Szkolna", "4", "2"),
        )
        self.assertEqual(inputs["ko_wojewodztwo"].value, "zachodniopomorskie")
        self.assertEqual(inputs["ko_powiat"].value, "policki")
        self.assertEqual(inputs["ko_gmina"].value, "Police")
        self.assertEqual(inputs["ko_kraj"].value, "Polska")

    def test_company_goes_through_krs(self):
        calls = []
        result = lookup_company_by_nip(
            "1",
            wl=lambda nip: {"nazwa": "WL", "krs": "482087", "status_vat": "Czynny"},
            krs=lambda k: calls.append(k) or {"nazwa": "KRS", "dz_miasto": "Szczecin"},
            ceidg=lambda nip: self.fail("CEIDG nie powinien być wołany"),
            gus=lambda nip: self.fail("GUS nie powinien być wołany"),
        )
        self.assertEqual(calls, ["482087"])
        self.assertEqual(result["nazwa"], "KRS")
        self.assertEqual(result["status_vat"], "Czynny")

    def test_jdg_goes_through_ceidg_then_wl(self):
        result = lookup_company_by_nip(
            "1",
            wl=lambda nip: {"nazwa": "WL", "krs": "", "status_vat": "Zwolniony"},
            krs=lambda k: self.fail("KRS nie powinien być wołany"),
            ceidg=lambda nip: {"nazwa": "JDG", "dz_miasto": "Drawsko"},
            gus=lambda nip: self.fail("GUS nie powinien być wołany"),
        )
        self.assertEqual(result["nazwa"], "JDG")
        self.assertEqual(result["status_vat"], "Zwolniony")

    def test_falls_back_to_gus_and_to_wl_only(self):
        result = lookup_company_by_nip(
            "1", wl=lambda nip: ERR, krs=lambda k: ERR,
            ceidg=lambda nip: ERR, gus=lambda nip: {"nazwa": "GUS"},
        )
        self.assertEqual(result["nazwa"], "GUS")
        result = lookup_company_by_nip(
            "1", wl=lambda nip: {"nazwa": "WL", "krs": ""}, krs=lambda k: ERR,
            ceidg=lambda nip: ERR, gus=lambda nip: ERR,
        )
        self.assertEqual(result["nazwa"], "WL")


if __name__ == "__main__":
    unittest.main()
