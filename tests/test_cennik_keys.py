import unittest

from app.companies.cennik_keys import (
    filter_cennik_keys, find_cennik_key, parse_cennik_key, typ_from_forma, vat_flag_from_status,
)
from app.utils.importers import _is_vat_active, parse_cennik_ksiegowy


class CennikKeysTests(unittest.TestCase):
    KEYS = ["KPIR_T_1_100", "KPIR_T_10_300", "KPIR_T_5_260", "KPIR_N_5_270",
            "KPIR_T_710_4147_5", "Ryczałt_N_5_210", "KPIR_T_20_400"]

    def test_parse(self):
        self.assertEqual(parse_cennik_key("KPIR_T_710_4147_5"),
                         {"typ": "KPIR", "vat": "T", "limit": 710, "price": 4147.5})
        self.assertEqual(parse_cennik_key("Ryczałt_N_5_210")["typ"], "RYCZALT")

    def test_sort_numeric_and_filter(self):
        keys = filter_cennik_keys(self.KEYS, "KPIR", "T")
        self.assertEqual(keys, ["KPIR_T_1_100", "KPIR_T_5_260", "KPIR_T_10_300",
                                "KPIR_T_20_400", "KPIR_T_710_4147_5"])

    def test_helpers(self):
        self.assertEqual(typ_from_forma("KPiR"), "KPIR")
        self.assertEqual(typ_from_forma("Księgi rachunkowe"), "KH")
        self.assertEqual(vat_flag_from_status("Czynny"), "T")
        self.assertIsNone(vat_flag_from_status("Brak danych"))

    def test_exact_match_only(self):
        self.assertEqual(find_cennik_key(self.KEYS, "KPIR", "T", 5, 260), "KPIR_T_5_260")
        self.assertIsNone(find_cennik_key(self.KEYS, "KPIR", "T", 5, 270))

    def test_import_parsing(self):
        self.assertTrue(_is_vat_active("1."))
        parsed = parse_cennik_ksiegowy("5 szt. 260 zł + zdrowotna 60 zł, ponad limit 5,5 zł")
        self.assertEqual(parsed["ryczalt_ksiegowy"], 260)
        self.assertEqual(parsed["zdrowotna"], 60)
        self.assertEqual(parsed["cena_za_dokument"], 5.5)
        self.assertEqual(parsed["limit_dokumentow"], 5)


if __name__ == "__main__":
    unittest.main()
