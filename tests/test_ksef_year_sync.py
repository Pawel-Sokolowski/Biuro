import unittest
from datetime import datetime, timezone
from unittest import mock

from app.invoices import ksef_sync

SELLER_NIP = "9240000010"


class YearSyncTests(unittest.TestCase):
    def test_months_of_period(self):
        today = datetime(2026, 4, 15, tzinfo=timezone.utc)
        self.assertEqual(ksef_sync.months_of_period("2026-03", today), ["2026-03"])
        self.assertEqual(ksef_sync.months_of_period("2026", today),
                         ["2026-01", "2026-02", "2026-03", "2026-04"])
        self.assertEqual(len(ksef_sync.months_of_period("2025", today)), 12)
        self.assertEqual(ksef_sync.months_of_period("2027", today), [])

    def test_whole_year_is_downloaded_month_by_month(self):
        calls = []

        def fake_sync(start, end, **kwargs):
            calls.append((start, end, kwargs.get("buyer_nip")))
            return {"dopasowane": [f"{start:%m}"], "utworzone": [], "zaktualizowane": [], "pominiete": [],
                    "niedopasowane": [], "niedopasowane_szczegoly": [], "znalezione": 2}

        with mock.patch.object(ksef_sync, "sync_issued_invoices", side_effect=fake_sync), \
                mock.patch.object(ksef_sync, "client_from_settings", return_value=object()), \
                mock.patch.object(ksef_sync, "months_of_period", return_value=["2025-01", "2025-02", "2025-03"]):
            result = ksef_sync.sync_month("2025", buyer_nip=SELLER_NIP)
        self.assertEqual(len(calls), 3)
        for start, end, nip in calls:
            self.assertEqual((start.year, start.month), (end.year, end.month))
            self.assertEqual(nip, SELLER_NIP)
        self.assertEqual(result["znalezione"], 6)
        self.assertEqual(result["dopasowane"], ["01", "02", "03"])

    def test_single_month_is_one_request(self):
        with mock.patch.object(ksef_sync, "sync_issued_invoices", return_value={"znalezione": 0}) as sync:
            ksef_sync.sync_month("2026-02", client=object())
        sync.assert_called_once()


if __name__ == "__main__":
    unittest.main()
