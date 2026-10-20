import unittest

from app.companies.company_names import clean_company_name, sole_proprietor_short_name
from app.utils.polish_sort import polish_sort_key


class PolishSortAndNamesTests(unittest.TestCase):
    def test_polish_alphabet_places_s_with_acute_before_t(self):
        names = ["Zebra", "Tomek", "Ślusarz", "Sowa"]
        self.assertEqual(
            sorted(names, key=polish_sort_key),
            ["Sowa", "Ślusarz", "Tomek", "Zebra"],
        )

    def test_short_name_is_surname_then_first_name(self):
        self.assertEqual(sole_proprietor_short_name("Jan", "Kowalski"), "Kowalski Jan")
        self.assertEqual(sole_proprietor_short_name("", "Kowalski"), "")

    def test_quotes_removed_from_limited_company_name_starting_with_firma(self):
        self.assertEqual(
            clean_company_name("„Firma Test” sp. z o.o."),
            "Firma Test sp. z o.o.",
        )
        self.assertEqual(clean_company_name("„Inna spółka”"), "„Inna spółka”")


if __name__ == "__main__":
    unittest.main()
