import unittest

from app.utils.saldeo_status import parse_subject


class SaldeoSubjectTests(unittest.TestCase):
    def test_results_subject(self):
        subject = "ABC Sp. z o.o. Rozliczenie wyników firmy, podatków za miesiąc wrzesień 2026"
        self.assertEqual(parse_subject(subject), ("ABC Sp. z o.o.", "2026-09", "etap"))

    def test_zus_subject_with_prefix(self):
        self.assertEqual(
            parse_subject("RE: Kowalski Rozliczenie ZUS za miesiąc styczeń 2027"),
            ("Kowalski", "2027-01", "etap_kadry"),
        )

    def test_other_subject(self):
        self.assertIsNone(parse_subject("Faktura za wrzesień 2026"))


if __name__ == "__main__":
    unittest.main()
