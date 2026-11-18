"""Ownership, date ranges and monthly accounting; executed by GitHub CI."""
from datetime import datetime
import json
from unittest import mock

from tests.base import DbTestCase
from app.db.dao_conn import get_conn
from app.db.dao_timers import SOURCE_IMPORT, SOURCE_TIMER, add_manual_time, list_employee_timers, update_company_timer, delete_company_timers


class EmployeeTimersTests(DbTestCase):
    def setUp(self):
        super().setUp()
        self.employee = self.make_employee('timer@example.com')
        self.other = self.make_employee('other@example.com')
        self.company = self.make_company()
        self.second = self.make_company('Druga')

    def timer(self, company, employee=None, seconds=3600):
        with get_conn() as conn:
            return conn.execute("""INSERT INTO czasomierze
                (firma_id,pracownik_id,miesiac,typ,rozpoczecie,zakonczenie,sekundy)
                VALUES (%s,%s,'2026-10','ksiegowosc','2026-10-31T10:00:00','2026-10-31T11:00:00',%s) RETURNING id""",
                (company,employee or self.employee,seconds)).fetchone()[0]

    def test_own_view_contains_all_companies_and_general_but_not_another_employee(self):
        own = [self.timer(company) for company in (self.company,self.second,None)]
        self.timer(self.company,self.other)
        rows = list_employee_timers(self.employee,self.employee,'2026-10')
        self.assertCountEqual([row['id'] for row in rows],own)
        self.assertEqual(list_employee_timers(self.employee,self.employee,'2026-11'),[])
        with self.assertRaises(PermissionError):
            list_employee_timers(self.other,self.employee)
        self.assertEqual(len(list_employee_timers(self.other,1)),1)

    def test_employee_cannot_update_delete_or_list_another_employees_time(self):
        record = self.timer(self.company,self.other)
        with self.assertRaises(PermissionError):
            update_company_timer(self.company,SOURCE_TIMER,record,'kadry','',60,self.employee)
        with self.assertRaises(PermissionError):
            delete_company_timers(self.company,[(SOURCE_TIMER,record)],self.employee)
        self.assertEqual(list_employee_timers(self.other,1)[0]['sekundy'],3600)

    def test_range_moves_month_and_department_and_creates_an_audit_entry(self):
        record = self.timer(self.company)
        with get_conn() as conn:
            conn.execute("INSERT INTO statusy_miesieczne(firma_id,miesiac,sekundy_ksiegowosc) VALUES (%s,'2026-10',3600)",(self.company,))

    def test_explicit_accounting_month_is_independent_from_calendar_date(self):
        record = add_manual_time(self.employee, self.company, 'ksiegowosc', 'Październik', 900,
                                 self.employee, started_at='2026-11-18T10:00:00', miesiac='2026-10')
        october = list_employee_timers(self.employee, self.employee, '2026-10')
        self.assertEqual(october[0]['id'], record)
        self.assertEqual(october[0]['start'], '2026-11-18T10:00:00')

    def test_duration_only_edit_can_move_record_between_accounting_months(self):
        record = self.timer(self.company)
        with get_conn() as conn:
            conn.execute("INSERT INTO statusy_miesieczne(firma_id,miesiac,sekundy_ksiegowosc) VALUES (%s,'2026-10',3600)",(self.company,))
        update_company_timer(self.company, SOURCE_TIMER, record, 'ksiegowosc', '', 1800,
                             self.employee, miesiac='2026-11')
        self.assertEqual(list_employee_timers(self.employee, self.employee, '2026-10'), [])
        self.assertEqual(list_employee_timers(self.employee, self.employee, '2026-11')[0]['sekundy'], 1800)
        update_company_timer(self.company,SOURCE_TIMER,record,'kadry','Korekta',123,self.employee,
                             started_at='2026-11-01T08:15:00',ended_at='2026-11-01T10:45:00')
        rows = list_employee_timers(self.employee,self.employee,'2026-11')
        self.assertEqual((rows[0]['sekundy'],rows[0]['start'],rows[0]['koniec']),(9000,'2026-11-01T08:15:00','2026-11-01T10:45:00'))
        with get_conn() as conn:
            totals = conn.execute('SELECT miesiac,sekundy_ksiegowosc,sekundy_kadry FROM statusy_miesieczne WHERE firma_id=%s ORDER BY miesiac',(self.company,)).fetchall()
            audit = conn.execute("SELECT uzytkownik_id,szczegoly FROM audit_log WHERE tabela='czasomierze' AND rekord_id=%s",(record,)).fetchone()
        self.assertEqual((totals[0][1],totals[1][2]),(0,9000))
        self.assertEqual(audit[0],self.employee)
        details = json.loads(audit[1]) if isinstance(audit[1],str) else audit[1]
        self.assertEqual(details['before']['sekundy'],3600)
        self.assertEqual(details['after']['sekundy'],9000)

    def test_general_time_can_be_edited_without_a_company(self):
        record = self.timer(None)
        self.assertTrue(update_company_timer(None,SOURCE_TIMER,record,'ogolne','',1,self.employee,
            started_at='2026-10-01T23:30:00',ended_at='2026-10-02T00:30:00'))
        self.assertEqual(list_employee_timers(self.employee,self.employee)[0]['sekundy'],3600)

    def test_bad_interval_rolls_back_and_cannot_target_a_different_company(self):
        record = self.timer(self.company)
        for kwargs in ({'started_at':'2026-10-01T10:00:00'},
                       {'started_at':'2026-10-01T10:00:00','ended_at':'2026-10-01T09:00:00'},
                       {'started_at':'bad','ended_at':'bad'}):
            with self.assertRaises(ValueError):
                update_company_timer(self.company,SOURCE_TIMER,record,'ogolne','',60,self.employee,**kwargs)
        self.assertFalse(update_company_timer(self.second,SOURCE_TIMER,record,'ogolne','',60,self.employee))
        self.assertEqual(list_employee_timers(self.employee,self.employee)[0]['sekundy'],3600)

    def test_imported_ranges_keep_breaks_and_cannot_cross_day_boundaries(self):
        with get_conn() as conn:
            record = conn.execute("""INSERT INTO import_czas_pracy(hash,firma_id,pracownik_id,data,miesiac,od,do_,sekundy,pauza_sekundy,dzial)
                VALUES ('range',%s,%s,'2026-10-01','2026-10','08:00','10:00',5400,1800,'ksiegowosc') RETURNING id""",
                (self.company,self.employee)).fetchone()[0]
        update_company_timer(self.company,SOURCE_IMPORT,record,'kadry','Opis',0,self.employee,
            started_at='2026-10-02T08:00:00',ended_at='2026-10-02T11:00:00')
        self.assertEqual(list_employee_timers(self.employee,self.employee)[0]['sekundy'],9000)
        with self.assertRaises(ValueError):
            update_company_timer(self.company,SOURCE_IMPORT,record,'kadry','',0,self.employee,
                started_at='2026-10-02T23:00:00',ended_at='2026-10-03T01:00:00')

    def test_dialog_accepts_start_end_times_and_keeps_duration_option(self):
        from PyQt6.QtCore import QDateTime
        from app.monthly_view.timer_records_dialog import EditTimerRecordDialog,TimerRecordsDialog
        self.timer(self.company)
        entry = list_employee_timers(self.employee,self.employee)[0]
        with mock.patch('app.monthly_view.timer_records_dialog.list_extra_service_catalogue',return_value=[]):
            dialog = EditTimerRecordDialog(entry,actor_id=self.employee)
        self.addCleanup(dialog.close)
        dialog.started_at.setDateTime(QDateTime(datetime(2026,10,31,8,15)))
        dialog.ended_at.setDateTime(QDateTime(datetime(2026,10,31,9,45)))
        self.assertEqual(dialog.values()[2],5400)
        self.assertIn('08:15',dialog.timestamps()['started_at'])
        dialog.time_mode.setCurrentIndex(1)
        dialog.hours.setValue(2)
        dialog.minutes.setValue(5)
        self.assertEqual(dialog.values()[2],7500)
        self.assertEqual(dialog.timestamps(),{})
        view = TimerRecordsDialog(None,self.employee,employee_view=True)
        self.addCleanup(view.close)
        view.month.setCurrentText('2026-10')
        self.assertEqual(view.employee.currentData(),self.employee)
        self.assertEqual(view.table.rowCount(),1)

    def test_description_edit_preserves_seconds_in_a_duration_only_record(self):
        from app.monthly_view.timer_records_dialog import EditTimerRecordDialog
        entry = {'dzial':'ogolne','opis':'Stary opis','sekundy':95,'start':'','koniec':''}
        with mock.patch('app.monthly_view.timer_records_dialog.list_extra_service_catalogue',return_value=[]):
            dialog = EditTimerRecordDialog(entry,actor_id=self.employee)
        self.addCleanup(dialog.close)
        dialog.description.setText('Nowy opis')
        self.assertEqual(dialog.values()[2],95)
