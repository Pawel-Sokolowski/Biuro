import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMessageBox

from app.db.dao_conn import get_conn
from app.db.dao_timers import SOURCE_IMPORT, SOURCE_TIMER, delete_company_timers, list_company_timers
from tests.base import DbTestCase

_app = QApplication.instance() or QApplication(["biuro-tests"])


class CompanyTimerRecordsTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.emp = self.make_employee("kasia@x.pl", "Kasia", "Nowak")
        self.firma = self.make_company("Alfa")
        self.other = self.make_company("Beta")
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO statusy_miesieczne (firma_id, miesiac, sekundy_ksiegowosc, sekundy_kadry) "
                "VALUES (%s, '2026-10', 5000, 900)", (self.firma,))
            self.timer_ks = self._timer(conn, self.firma, "ksiegowosc", 3600, "2026-10-05T10:00:00")
            self.timer_kadry = self._timer(conn, self.firma, "kadry", 600, "2026-10-06T10:00:00")
            self.timer_other = self._timer(conn, self.other, "ksiegowosc", 100, "2026-10-07T10:00:00")
            self.imported = conn.execute(
                "INSERT INTO import_czas_pracy (hash, firma_id, pracownik_nazwa, data, miesiac, od, do_, "
                "sekundy, czynnosc, dzial) VALUES ('h1', %s, 'Jan K', '2026-10-01', '2026-10', '08:00', '09:00', "
                "3600, 'Rozmowa', 'ksiegowosc') RETURNING id", (self.firma,)).fetchone()[0]
            conn.commit()
        finally:
            conn.close()

    def _timer(self, conn, firma, typ, seconds, start):
        return conn.execute(
            "INSERT INTO czasomierze (firma_id, pracownik_id, miesiac, typ, rozpoczecie, zakonczenie, sekundy) "
            "VALUES (%s, %s, '2026-10', %s, %s, %s, %s) RETURNING id",
            (firma, self.emp, typ, start, start, seconds)).fetchone()[0]

    def _status_seconds(self):
        conn = get_conn()
        try:
            row = conn.execute(
                "SELECT sekundy_ksiegowosc, sekundy_kadry FROM statusy_miesieczne "
                "WHERE firma_id = %s AND miesiac = '2026-10'", (self.firma,)).fetchone()
            return row[0], row[1]
        finally:
            conn.close()

    def test_lists_only_selected_company_from_both_sources_newest_first(self):
        rows = list_company_timers(self.firma)
        self.assertEqual([(r["zrodlo"], r["id"]) for r in rows], [
            (SOURCE_TIMER, self.timer_kadry), (SOURCE_TIMER, self.timer_ks), (SOURCE_IMPORT, self.imported)])
        self.assertEqual(rows[0]["pracownik"], "Kasia Nowak")
        self.assertEqual(rows[2]["opis"], "Rozmowa")

    def test_deleting_timer_record_subtracts_time_from_monthly_total(self):
        deleted = delete_company_timers(self.firma, [(SOURCE_TIMER, self.timer_ks)], 1)
        self.assertEqual(deleted, 1)
        self.assertEqual(self._status_seconds(), (1400, 900))
        self.assertNotIn((SOURCE_TIMER, self.timer_ks),
                         [(r["zrodlo"], r["id"]) for r in list_company_timers(self.firma)])

    def test_total_never_goes_below_zero(self):
        delete_company_timers(self.firma, [(SOURCE_TIMER, self.timer_kadry)], 1)
        conn = get_conn()
        try:
            conn.execute("UPDATE statusy_miesieczne SET sekundy_kadry = 100 WHERE firma_id = %s", (self.firma,))
            conn.commit()
            other = self._timer(conn, self.firma, "kadry", 500, "2026-10-08T10:00:00")
            conn.commit()
        finally:
            conn.close()
        delete_company_timers(self.firma, [(SOURCE_TIMER, other)], 1)
        self.assertEqual(self._status_seconds()[1], 0)

    def test_imported_record_is_deleted_without_touching_monthly_total(self):
        self.assertEqual(delete_company_timers(self.firma, [(SOURCE_IMPORT, self.imported)], 1), 1)
        self.assertEqual(self._status_seconds(), (5000, 900))

    def test_other_company_records_cannot_be_deleted(self):
        self.assertEqual(delete_company_timers(self.firma, [(SOURCE_TIMER, self.timer_other)], 1), 0)
        self.assertEqual(len(list_company_timers(self.other)), 1)

    def test_employee_can_delete_but_anonymous_cannot(self):
        with self.assertRaises(PermissionError):
            delete_company_timers(self.firma, [(SOURCE_TIMER, self.timer_ks)], None)
        self.assertEqual(len(list_company_timers(self.firma)), 3)
        self.assertEqual(delete_company_timers(self.firma, [(SOURCE_TIMER, self.timer_ks)], self.emp), 1)

    def test_unknown_source_rolls_back_whole_deletion(self):
        with self.assertRaises(ValueError):
            delete_company_timers(self.firma, [(SOURCE_TIMER, self.timer_ks), ("xyz", 1)], 1)
        self.assertEqual(len(list_company_timers(self.firma)), 3)
        self.assertEqual(self._status_seconds(), (5000, 900))

    def test_company_form_tab_is_available_for_employees_and_deletes_selected_rows(self):
        from app.companies.company_form import CompanyForm
        anonymous = CompanyForm(self.firma)
        self.addCleanup(anonymous.close)
        self.assertFalse(hasattr(anonymous, "timers_table"))

        form = CompanyForm(self.firma, actor_id=self.emp)
        self.addCleanup(form.close)
        tabs = [form.section_tabs.tabText(i) for i in range(form.section_tabs.count())]
        self.assertIn("Czasomierz", tabs)
        self.assertEqual(form.timers_table.rowCount(), 3)
        self.assertEqual(form.timers_table.item(1, 6).text(), "1:00:00")
        self.assertIn("Rekordów: 3", form.timers_total.text())
        form.timers_table.selectRow(1)
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            form._delete_timers()
        self.assertEqual(form.timers_table.rowCount(), 2)
        self.assertEqual(self._status_seconds(), (1400, 900))

    def test_company_form_declined_confirmation_keeps_records(self):
        from app.companies.company_form import CompanyForm
        form = CompanyForm(self.firma, actor_id=1)
        self.addCleanup(form.close)
        form.timers_table.selectRow(0)
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            form._delete_timers()
        self.assertEqual(form.timers_table.rowCount(), 3)

    def test_new_company_has_no_timer_tab(self):
        from app.companies.company_form import CompanyForm
        form = CompanyForm(None, actor_id=1)
        self.addCleanup(form.close)
        tabs = [form.section_tabs.tabText(i) for i in range(form.section_tabs.count())]
        self.assertNotIn("Czasomierz", tabs)


class StopAllTimersOnExitTests(DbTestCase):
    def test_running_timers_are_saved_on_exit(self):
        import datetime
        from app.monthly_view.monthly_view_logic import MonthlyViewLogic

        emp = self.make_employee("ola@x.pl", "Ola", "Nowak")
        firma = self.make_company("Gama")
        logic = MonthlyViewLogic(emp)
        logic.toggle_stoper(firma, "ksiegowosc")
        logic.czas_start_ks = datetime.datetime.now() - datetime.timedelta(seconds=120)
        logic.stop_all_timers()

        conn = get_conn()
        try:
            rows = conn.execute(
                "SELECT sekundy FROM czasomierze WHERE firma_id = %s", (firma,)).fetchall()
            total = conn.execute(
                "SELECT sekundy_ksiegowosc FROM statusy_miesieczne WHERE firma_id = %s",
                (firma,)).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(len(rows), 1)
        self.assertGreaterEqual(rows[0][0], 120)
        self.assertGreaterEqual(total, 120)
        self.assertIsNone(logic.aktywna_firma_stoper_ks)


if __name__ == "__main__":
    unittest.main()
