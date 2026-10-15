import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app.db.dao_app_settings import set_setting
from app.utils.backup_status import backup_name, find_log_file, match_company, get_backup_status, parse_backup_log
from tests.base import DbTestCase

SAMPLE = """Connection timed out.
    2026-10-15 01:15 The file "E:\\backup_baz\\_S_C__20261014.BAK" has been compressed
    2026-10-15 01:15 Compressing the file "E:\\backup_baz\\Adamkiewicz_Paweł_20261014.BAK" into "\\\\nas6t\\backup\\x.zip"
    2026-10-15 01:15 The file "E:\\backup_baz\\Paweł_20261014.BAK" has been compressed
    2026-10-15 01:15 Compressing the file "E:\\backup_baz\\Monika_20261014.BAK" into "\\\\nas6t\\backup\\x.zip"
"""


class ParseTests(unittest.TestCase):
    def test_sample_log(self):
        parsed = parse_backup_log(SAMPLE)
        self.assertEqual(parsed["last_success"], datetime(2026, 10, 15, 1, 15))
        self.assertEqual(parsed["files"], 2)
        self.assertIsNone(parsed["error"])

    def test_error_after_success_is_reported(self):
        parsed = parse_backup_log(SAMPLE + "Connection timed out.\n")
        self.assertEqual(parsed["error"], "Connection timed out.")

    def test_per_database_results(self):
        dbs = parse_backup_log(SAMPLE)["databases"]
        self.assertEqual(list(dbs), ["Adamkiewicz_Paweł", "Monika", "Paweł", "S_C"])
        self.assertIsNone(dbs["Adamkiewicz_Paweł"]["last_success"])
        self.assertIsNotNone(dbs["Paweł"]["last_success"])
        self.assertIsNone(dbs["Monika"]["last_success"])

    def test_backup_name_and_company_match(self):
        self.assertEqual(backup_name(r"E:\backup_baz\Adamkiewicz_Paweł_20261014.BAK"), "Adamkiewicz_Paweł")
        firmy = [{"nazwa": "ADAMKIEWICZ PAWEŁ Usługi", "nazwa_skrocona": None}]
        self.assertEqual(match_company("Adamkiewicz_Paweł", firmy), "ADAMKIEWICZ PAWEŁ Usługi")
        self.assertIsNone(match_company("Monika", firmy))

    def test_empty_log(self):
        self.assertIsNone(parse_backup_log("")["last_success"])


class StatusTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.log = Path(self.dir.name) / "backup.log"
        self.log.write_text(SAMPLE, encoding="utf-8")

    def test_not_configured(self):
        self.assertEqual(get_backup_status()["stan"], "brak_konfiguracji")

    def test_fresh_stale_and_folder(self):
        set_setting("backup_log_path", str(self.log))
        self.assertEqual(get_backup_status(datetime(2026, 10, 15, 9, 0))["stan"], "ok")
        self.assertEqual(get_backup_status(datetime(2026, 10, 20, 9, 0))["stan"], "nieaktualna")
        set_setting("backup_log_path", self.dir.name)
        self.assertEqual(find_log_file(self.dir.name), self.log)
        self.assertEqual(get_backup_status(datetime(2026, 10, 15, 9, 0))["stan"], "ok")

    def test_status_lists_databases(self):
        set_setting("backup_log_path", str(self.log))
        status = get_backup_status(datetime(2026, 10, 15, 9, 0))
        states = {b["nazwa"]: b["stan"] for b in status["bazy"]}
        self.assertEqual(states["Paweł"], "ok")
        self.assertEqual(states["Monika"], "blad")

    def test_missing_path(self):
        set_setting("backup_log_path", str(Path(self.dir.name) / "nie_ma.log"))
        self.assertEqual(get_backup_status()["stan"], "brak_logu")


if __name__ == "__main__":
    unittest.main()
