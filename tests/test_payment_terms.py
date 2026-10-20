import unittest
from datetime import date

from app.utils.payment_terms import MODE_DAY_OF_MONTH, MODE_DAYS, due_date_for


class DueDateTests(unittest.TestCase):
    def test_days(self):
        self.assertEqual(due_date_for(date(2026, 10, 20), MODE_DAYS, 7, 15), date(2026, 10, 27))

    def test_day_of_month(self):
        self.assertEqual(due_date_for(date(2026, 10, 5), MODE_DAY_OF_MONTH, 7, 15), date(2026, 10, 15))
        self.assertEqual(due_date_for(date(2026, 10, 20), MODE_DAY_OF_MONTH, 7, 15), date(2026, 11, 15))
        self.assertEqual(due_date_for(date(2026, 12, 20), MODE_DAY_OF_MONTH, 7, 10), date(2027, 1, 10))
        self.assertEqual(due_date_for(date(2026, 2, 1), MODE_DAY_OF_MONTH, 7, 31), date(2026, 2, 28))


if __name__ == "__main__":
    unittest.main()
