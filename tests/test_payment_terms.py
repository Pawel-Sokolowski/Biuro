import unittest
from datetime import date
from unittest.mock import patch

from app.utils.payment_terms import MODE_DAY_OF_MONTH, MODE_DAYS, due_date_for, default_due_date


class DueDateTests(unittest.TestCase):
    def test_days(self):
        self.assertEqual(due_date_for(date(2026, 10, 20), MODE_DAYS, 7, 15), date(2026, 10, 27))

    def test_day_of_month(self):
        self.assertEqual(due_date_for(date(2026, 10, 5), MODE_DAY_OF_MONTH, 7, 15), date(2026, 10, 15))
        self.assertEqual(due_date_for(date(2026, 10, 20), MODE_DAY_OF_MONTH, 7, 15), date(2026, 11, 15))
        self.assertEqual(due_date_for(date(2026, 12, 20), MODE_DAY_OF_MONTH, 7, 10), date(2027, 1, 10))
        self.assertEqual(due_date_for(date(2026, 2, 1), MODE_DAY_OF_MONTH, 7, 31), date(2026, 2, 28))

    def test_fixed_07_from_october_28_gives_november_07(self):
        self.assertEqual(
            due_date_for(date(2026, 10, 28), MODE_DAY_OF_MONTH, 14, 7),
            date(2026, 11, 7),
        )

    def test_fixed_07_from_october_02_gives_october_07(self):
        self.assertEqual(
            due_date_for(date(2026, 10, 2), MODE_DAY_OF_MONTH, 14, 7),
            date(2026, 10, 7),
        )

    def test_global_setting_07_affects_due_date(self):
        settings = {
            "termin_platnosci_tryb": MODE_DAY_OF_MONTH,
            "termin_platnosci_dzien": "7",
            "termin_platnosci_dni": "14",
        }
        with patch("app.utils.payment_terms.get_setting_first",
                   side_effect=lambda keys, default=None: next(
                       (settings[key] for key in keys if key in settings), default
                   )):
            self.assertEqual(default_due_date(date(2026, 10, 28)), date(2026, 11, 7))

    def test_month_end_without_31_uses_last_day(self):
        self.assertEqual(
            due_date_for(date(2026, 1, 31), MODE_DAY_OF_MONTH, 7, 31),
            date(2026, 1, 31),
        )
        self.assertEqual(
            due_date_for(date(2026, 2, 10), MODE_DAY_OF_MONTH, 7, 31),
            date(2026, 2, 28),
        )


if __name__ == "__main__":
    unittest.main()
