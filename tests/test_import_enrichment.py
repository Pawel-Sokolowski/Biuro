import unittest

from app.utils.import_enrichment import enrich_payload


class EnrichPayloadTests(unittest.TestCase):
    def test_fills_missing_and_keeps_file_data(self):
        payload = {"nip": "123-456-32-18", "regon": "111", "dz_miasto": ""}
        data = {
            "regon": "999", "pkd": ["62.01.Z", "62.02.Z"], "data_rozpoczecia": "2020-01-02",
            "dz_miasto": "Lublin", "koresp_ulica": "Długa", "status": "AKTYWNY",
            "status_vat": "Czynny", "imie": "Jan", "nazwisko": "Kowalski",
            "nazwa": "„Firma Test” sp. z o.o.",
        }
        self.assertIsNone(enrich_payload(payload, lambda nip: data))
        self.assertEqual(payload["regon"], "111")
        self.assertEqual(payload["dz_miasto"], "Lublin")
        self.assertEqual(payload["ko_ulica"], "Długa")
        self.assertEqual(payload["kody_pkd"], "62.01.Z, 62.02.Z")
        self.assertEqual(payload["data_rozpoczecia_dg"], "2020-01-02")
        self.assertEqual(payload["status_vat"], "Czynny")
        self.assertEqual(payload["vat_czynny"], 1)
        self.assertEqual(payload["nazwa_skrocona"], "Kowalski Jan")
        self.assertEqual(payload["nazwa"], "Firma Test sp. z o.o.")
        self.assertNotIn("status", payload)

    def test_error_and_bad_nip(self):
        self.assertIn("brak", enrich_payload({"nip": "1234563218"}, lambda n: {"error": True, "message": "brak"}))
        called = []
        self.assertIsNone(enrich_payload({"nip": "12"}, lambda n: called.append(n)))
        self.assertFalse(called)


if __name__ == "__main__":
    unittest.main()
