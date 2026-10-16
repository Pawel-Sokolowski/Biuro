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
