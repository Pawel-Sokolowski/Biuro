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

    def test_gus_handles_unwrapped_and_namespaced_dane(self):
        from app.companies.gus_api import parse_search_result, parse_legal_report
        bare = (
            "<dane><Regon>123456789</Regon><Nip>1234563218</Nip>"
            "<Nazwa>Spółka z o.o.</Nazwa><Typ>P</Typ>"
            "<Miejscowosc>Szczecin</Miejscowosc></dane>"
        )
        self.assertEqual(parse_search_result(bare)["nazwa"], "Spółka z o.o.")
        self.assertEqual(parse_search_result(bare)["nip"], "1234563218")
        wrapped = (
            "<root><n:dane xmlns:n='urn:example'><n:Regon>123456789</n:Regon>"
            "<n:Nip>1234563218</n:Nip><n:Nazwa>Firma</n:Nazwa>"
            "<n:Typ>P</n:Typ></n:dane></root>"
        )
        self.assertEqual(parse_search_result(wrapped)["regon"], "123456789")
        legal = (
            "<dane><praw_rodzajRejestruEwidencji_Nazwa>KRAJOWY REJESTR SĄDOWY"
            "</praw_rodzajRejestruEwidencji_Nazwa>"
            "<praw_numerWrejestrzeEwidencji>482087"
            "</praw_numerWrejestrzeEwidencji></dane>"
        )
        self.assertEqual(parse_legal_report(legal)["krs"], "0000482087")

    def test_gus_fetches_krs_using_fallback_report_when_needed(self):
        from unittest.mock import patch
        from app.companies.gus_api import get_gus_firma_by_nip

        calls = []
        def mock_call(url, action, body, sid=None):
            calls.append((action, body))
            if action == "Zaloguj":
                return "<s:ZalogujResult>SESSION_ID</s:ZalogujResult>"
            if action == "DaneSzukajPodmioty":
                return (
                    "<a:DaneSzukajPodmiotyResult>"
                    "&lt;dane&gt;&lt;Regon&gt;123456789&lt;/Regon&gt;"
                    "&lt;Nip&gt;1234563218&lt;/Nip&gt;"
                    "&lt;Typ&gt;P&lt;/Typ&gt;&lt;Nazwa&gt;Firma&lt;/Nazwa&gt;&lt;/dane&gt;"
                    "</a:DaneSzukajPodmiotyResult>"
                )
            if "BIR11OsPrawna" in body:
                return "<a:DanePobierzPelnyRaportResult />"
            self.assertIn("PublDaneRaportPrawna", body)
            return (
                "<a:DanePobierzPelnyRaportResult>"
                "&lt;dane&gt;"
                "&lt;praw_rodzajRejestruEwidencji_Nazwa&gt;"
                "KRAJOWY REJESTR SĄDOWY"
                "&lt;/praw_rodzajRejestruEwidencji_Nazwa&gt;"
                "&lt;praw_numerWrejestrzeEwidencji&gt;482087"
                "&lt;/praw_numerWrejestrzeEwidencji&gt;"
                "&lt;/dane&gt;"
                "</a:DanePobierzPelnyRaportResult>"
            )

        with (patch("app.companies.gus_api._config", return_value=("key", "https://example.invalid")),
              patch("app.companies.gus_api._call", side_effect=mock_call)):
            result = get_gus_firma_by_nip("1234563218")
        self.assertEqual(result["nazwa"], "Firma")
        self.assertEqual(result["regon"], "123456789")
        self.assertEqual(result["krs"], "0000482087")
        self.assertEqual([action for action, _ in calls], [
            "Zaloguj", "DaneSzukajPodmioty",
            "DanePobierzPelnyRaport", "DanePobierzPelnyRaport",
        ])

    def test_missing_gus_data_with_available_vat_is_explained(self):
        result = lookup_company_by_nip(
            "1234563218",
            forma_dzialalnosci="Spółka z o.o.",
            gus=lambda nip: {"error": True, "message": "GUS: testowa awaria"},
            wl=lambda nip: {"status_vat": "Czynny", "konto_bankowe": "PL_TEST"},
            krs=lambda n: self.fail("Brak numeru KRS, nie wywołujemy KRS"),
            ceidg=lambda n: self.fail("Spółka z o.o. nie jest JDG"),
        )
        self.assertEqual(result["nip"], "1234563218")
        self.assertEqual(result["status_vat"], "Czynny")
        self.assertEqual(result["konto_bankowe"], "PL_TEST")
        self.assertIn("testowa awaria", result["_registry_warning"])
        self.assertNotIn("nazwa", result)

    def test_gus_without_krs_still_returns_name_and_warning(self):
        result = lookup_company_by_nip(
            "1234563218", forma_dzialalnosci="Spółka z o.o.",
            gus=lambda nip: {"nip": nip, "nazwa": "Firma z GUS", "regon": "123456789"},
            wl=lambda nip: {"status_vat": "Zwolniony"},
            krs=lambda n: self.fail("Brak numeru KRS"),
        )
        self.assertEqual(result["nazwa"], "Firma z GUS")
        self.assertIn("numeru KRS", result["_registry_warning"])

    def test_company_goes_through_gus_and_krs(self):
        calls = []
        result = lookup_company_by_nip(
            "8522669775",
            forma_dzialalnosci="Spółka z o.o.",
            wl=lambda nip: calls.append(("VAT", nip)) or {
                "nazwa": "Z BIAŁEJ LISTY", "krs": "987654321", "dz_miasto": "Nie używać",
                "status_vat": "Czynny", "konto_bankowe": "12123456789",
            },
            gus=lambda nip: calls.append(("GUS", nip)) or {
                "nip": nip, "nazwa": "GUS Firma", "regon": "387974972", "krs": "0000482087",
            },
            krs=lambda number: calls.append(("KRS", number)) or {
                "nip": "8522669775", "nazwa": "KRS Firma", "dz_miasto": "Szczecin",
            },
            ceidg=lambda nip: self.fail("Dla spółki z o.o. nie odpytujemy CEIDG"),
        )
        self.assertEqual(calls, [
            ("VAT", "8522669775"), ("GUS", "8522669775"), ("KRS", "0000482087")
        ])
        self.assertEqual(result["nazwa"], "KRS Firma")
        self.assertEqual(result["dz_miasto"], "Szczecin")
        self.assertEqual(result["regon"], "387974972")
        self.assertEqual(result["status_vat"], "Czynny")
        self.assertEqual(result["konto_bankowe"], "12123456789")
        self.assertEqual(result["krs"], "0000482087")

    def test_jdg_goes_only_through_ceidg_and_checks_vat(self):
        calls = []
        result = lookup_company_by_nip(
            "8522669775",
            forma_dzialalnosci="Osoba fizyczna (KPiR)",
            wl=lambda nip: calls.append(("VAT", nip)) or {
                "nazwa": "Z Białej Listy", "dz_ulica": "Nie używać", "status_vat": "Zwolniony",
            },
            krs=lambda number: self.fail("JDG nie ma KRS"),
            ceidg=lambda nip: calls.append(("CEIDG", nip)) or {
                "nip": nip, "nazwa": "JDG", "dz_miasto": "Drawsko",
            },
            gus=lambda nip: self.fail("JDG: bez GUS"),
        )
        self.assertEqual(calls, [("VAT", "8522669775"), ("CEIDG", "8522669775")])
        self.assertEqual(result["nazwa"], "JDG")
        self.assertEqual(result["dz_miasto"], "Drawsko")
        self.assertNotIn("dz_ulica", result)
        self.assertEqual(result["status_vat"], "Zwolniony")

    def test_civil_partnership_uses_own_nip_gus_and_vat_not_partners_nips(self):
        calls = []
        result = lookup_company_by_nip(
            "8522669775",
            forma_dzialalnosci="Spółka cywilna",
            wl=lambda nip: calls.append(("VAT", nip)) or {
                "nazwa": "Nazwa z MF", "nip": "NIP WSPÓLNIKA",
                "dz_miasto": "Nie używać", "status_vat": "Czynny",
                "konto_bankowe": "11 2222 3333",
            },
            gus=lambda nip: calls.append(("GUS", nip)) or {
                "nip": nip, "nazwa": "Przykładowa Spółka Cywilna",
                "regon": "387974972", "dz_miasto": "Szczecin",
            },
            krs=lambda number: self.fail("Spółka cywilna nie ma KRS"),
            ceidg=lambda nip: self.fail("Nie pobieramy JDG po NIP spółki"),
            civil_partners=lambda nip: calls.append(("WSPÓLNICY CEIDG", nip)) or [
                {"nip": "1111111111", "nazwa_dzialalnosci": "Wspólnik 1"},
                {"nip": "2222222222", "nazwa_dzialalnosci": "Wspólnik 2"},
            ],
        )
        self.assertEqual(calls, [
            ("VAT", "8522669775"), ("GUS", "8522669775"),
            ("WSPÓLNICY CEIDG", "8522669775"),
        ])
        self.assertEqual(result["nip"], "8522669775")
        self.assertEqual(result["regon"], "387974972")
        self.assertEqual(result["nazwa"], "Przykładowa Spółka Cywilna")
        self.assertEqual(result["dz_miasto"], "Szczecin")
        self.assertEqual(result["status_vat"], "Czynny")
        self.assertEqual(result["konto_bankowe"], "11 2222 3333")
        self.assertNotIn("krs", result)
        self.assertEqual(len(result["wspolnicy_ceidg"]), 2)

    def test_civil_partnership_without_ceidg_partners_still_loads_gus(self):
        result = lookup_company_by_nip(
            "8522669775",
            forma_dzialalnosci="Spółka cywilna",
            wl=lambda nip: {"status_vat": "Zwolniony"},
            gus=lambda nip: {"nip": nip, "regon": "387974972", "nazwa": "S.C."},
            krs=lambda number: self.fail("No KRS"),
            ceidg=lambda nip: self.fail("No CEIDG JDG lookup"),
            civil_partners=lambda nip: [],
        )
        self.assertEqual(result["nip"], "8522669775")
        self.assertEqual(result["nazwa"], "S.C.")
        self.assertEqual(result["wspolnicy_ceidg"], [])
        self.assertEqual(result["status_vat"], "Zwolniony")

    def test_incomplete_registry_does_not_use_white_list_identity_or_address(self):
        result = lookup_company_by_nip(
            "8522669775",
            forma_dzialalnosci="Spółka jawna",
            wl=lambda nip: {
                "nazwa": "Nie przejmować", "krs": "0000011111",
                "dz_ulica": "Nie przejmować", "status_vat": "Czynny",
                "konto_bankowe": "12121212",
            },
            gus=lambda nip: {"error": True, "message": "Niedostępny GUS"},
            krs=lambda number: self.fail("Bez KRS z GUS brak KRS"),
            ceidg=lambda nip: self.fail("Bez CEIDG"),
        )
        self.assertEqual(result, {
            "nip": "8522669775", "status_vat": "Czynny",
            "konto_bankowe": "12121212",
        })

    def test_wrong_nip_from_krs_is_not_applied(self):
        result = lookup_company_by_nip(
            "8522669775",
            forma_dzialalnosci="Spółka jawna",
            wl=lambda nip: {"status_vat": "Czynny"},
            gus=lambda nip: {"nip": nip, "nazwa": "GUS", "krs": "0000123456"},
            krs=lambda number: {
                "nip": "1111111111", "nazwa": "Zły klient", "dz_miasto": "Obce",
            },
        )
        self.assertEqual(result["nazwa"], "GUS")
        self.assertNotIn("dz_miasto", result)
        self.assertEqual(result["status_vat"], "Czynny")

    def test_dreamlune_gus_register_przedsiebiorcow_yields_krs(self):
        from app.companies.gus_api import parse_legal_report

        # Zgłoszony rzeczywisty przypadek GUS: rodzaj rejestru to
        # "REJESTR PRZEDSIĘBIORCÓW", a nie dosłownie "KRS".
        xml = (
            "<dane>"
            "<praw_regon9>545639823</praw_regon9>"
            "<praw_nip>8393268547</praw_nip>"
            "<praw_nazwa>DREAMLUNE SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ</praw_nazwa>"
            "<praw_podstawowaFormaPrawna_Symbol>1</praw_podstawowaFormaPrawna_Symbol>"
            "<praw_szczegolnaFormaPrawna_Symbol>117</praw_szczegolnaFormaPrawna_Symbol>"
            "<praw_szczegolnaFormaPrawna_Nazwa>SPÓŁKI Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ</praw_szczegolnaFormaPrawna_Nazwa>"
            "<praw_organRejestrowy_Nazwa>SĄD REJONOWY GDAŃSK-PÓŁNOC W GDAŃSKU, "
            "VIII WYDZIAŁ GOSPODARCZY KRAJOWEGO REJESTRU SĄDOWEGO</praw_organRejestrowy_Nazwa>"
            "<praw_rodzajRejestruEwidencji_Nazwa>REJESTR PRZEDSIĘBIORCÓW</praw_rodzajRejestruEwidencji_Nazwa>"
            "<praw_numerWrejestrzeEwidencji>0001264722</praw_numerWrejestrzeEwidencji>"
            "</dane>"
        )
        result = parse_legal_report(xml)
        self.assertEqual(result["krs"], "0001264722")
        self.assertEqual(result["gus_rodzaj_rejestru"], "REJESTR PRZEDSIĘBIORCÓW")

    def test_register_przedsiebiorcow_krs_flows_gus_to_krs(self):
        from app.companies.gus_api import parse_legal_report

        report = parse_legal_report(
            "<dane>"
            "<praw_rodzajRejestruEwidencji_Nazwa>REJESTR PRZEDSIĘBIORCÓW</praw_rodzajRejestruEwidencji_Nazwa>"
            "<praw_numerWrejestrzeEwidencji>0001264722</praw_numerWrejestrzeEwidencji>"
            "</dane>"
        )
        calls = []
        result = lookup_company_by_nip(
            "8393268547", forma_dzialalnosci="Spółka z o.o.",
            gus=lambda nip: {"nip": nip, "regon": "545639823", "nazwa": "GUS DREAMLUNE", **report},
            wl=lambda nip: {"status_vat": "Czynny"},
            krs=lambda number: calls.append(number) or {
                "nip": "8393268547",
                "nazwa": "DREAMLUNE SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ",
                "dz_miasto": "Wodnica",
            },
            ceidg=lambda nip: self.fail("Spółka z o.o. nie powinna trafiać do CEIDG"),
        )
        self.assertEqual(calls, ["0001264722"])
        self.assertEqual(result["krs"], "0001264722")
        self.assertEqual(result["dz_miasto"], "Wodnica")
        self.assertEqual(result["nip"], "8393268547")
        self.assertNotIn("_registry_warning", result)

    def test_gus_krs_number_requires_explicit_krs_register(self):
        from app.companies.gus_api import parse_legal_report
        result = parse_legal_report(
            "<root><dane>"
            "<praw_rodzajRejestruEwidencji_Nazwa>KRAJOWY REJESTR SĄDOWY</praw_rodzajRejestruEwidencji_Nazwa>"
            "<praw_numerWrejestrzeEwidencji>482087</praw_numerWrejestrzeEwidencji>"
            "</dane></root>"
        )
        self.assertEqual(result["krs"], "0000482087")
        other = parse_legal_report(
            "<root><dane>"
            "<praw_rodzajRejestruEwidencji_Nazwa>INNY REJESTR</praw_rodzajRejestruEwidencji_Nazwa>"
            "<praw_numerWrejestrzeEwidencji>482087</praw_numerWrejestrzeEwidencji>"
            "</dane></root>"
        )
        self.assertNotIn("krs", other)



if __name__ == "__main__":
    unittest.main()
