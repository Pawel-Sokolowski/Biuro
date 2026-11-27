from datetime import datetime
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from psycopg import sql
from psycopg.errors import InsufficientPrivilege
from psycopg.types.json import Jsonb

from app.db.dao_conn import get_conn
from app.integrations.platnik.gui_agent import queue_target
from app.services.platnik_gui import read_checks, request_checks, request_daily_checks
from tests.base import DbTestCase

NIP = '1234567890'
MONTH = '2026-11'
FINDING = {
    'form': 'ZUS DRA', 'report': '01', 'status': 'accepted',
    'set_id': 'ZESTAW-9', 'receipt': 'POTW-9', 'sent_at': None,
}


class PlatnikGuiQueueTests(DbTestCase):
    def setUp(self):
        super().setUp()
        with get_conn() as conn:
            self.admin = conn.execute('SELECT id FROM pracownicy WHERE is_admin=1').fetchone()[0]
            self.target = queue_target(conn)
        self.company = self.make_company('Test DRA', nip=NIP)

    def request(self):
        with patch('app.services.checking_refresh.assigned_company_ids', return_value={self.company}):
            return request_checks(self.admin, [self.company], MONTH)

    def test_bulk_check_uses_the_database_and_respects_company_access(self):
        self.make_company('Another company', nip='9999999999')
        without_nip = self.make_company('Missing NIP')
        with patch('app.services.platnik_gui.assigned_company_ids',
                   return_value={self.company, without_nip}):
            result = request_checks(self.admin, None, MONTH)
        self.assertEqual(result, {'queued': 1, 'existing': 0, 'skipped': 1})
        with get_conn() as conn:
            queued = conn.execute("SELECT company_id,nip,period FROM platnik_gui_checks").fetchall()
        self.assertEqual(queued, [(self.company, NIP, MONTH)])

    def test_daily_run_records_both_periods_once_and_respects_open_jobs(self):
        self.request()
        self.make_company('Another company', nip='9999999999')
        day = datetime(2026, 11, 22, 16, tzinfo=ZoneInfo('Europe/Warsaw'))
        with patch('app.services.platnik_gui.datetime') as clock, \
             patch('app.services.platnik_gui.assigned_company_ids', return_value={self.company}):
            clock.now.return_value = day
            self.assertEqual(request_daily_checks(self.admin, None, self.target), {
                'queued': 1, 'periods': ['2026-10', '2026-11'], 'skipped': 0,
            })
            with get_conn() as conn:
                rows = conn.execute("""
                    SELECT company_id, period, actor_id FROM platnik_gui_checks ORDER BY period
                """).fetchall()
                self.assertEqual(rows, [
                    (self.company, '2026-10', self.admin), (self.company, '2026-11', self.admin),
                ])
                conn.execute("""
                    UPDATE platnik_gui_checks
                    SET state=CASE WHEN period=%s THEN 'completed' ELSE 'failed' END, finished_at=%s
                """, (MONTH, day))
            self.assertEqual(request_daily_checks(self.admin, None, self.target)['queued'], 1)

    def test_daily_run_can_retry_after_enqueue_failure(self):
        with patch('app.services.platnik_gui.assigned_company_ids', return_value={self.company}):
            with patch('app.services.platnik_gui._enqueue', side_effect=RuntimeError('failed')), \
                 self.assertRaisesRegex(RuntimeError, 'failed'):
                request_daily_checks(self.admin, None, self.target)
            self.assertEqual(request_daily_checks(self.admin, None, self.target)['queued'], 2)

    def test_daily_run_refuses_a_queue_in_a_different_database(self):
        with patch('app.services.platnik_gui.assigned_company_ids', return_value={self.company}), \
             self.assertRaisesRegex(ValueError, 'inną bazę'):
            request_daily_checks(self.admin, None, ['other_database', *self.target[1:]])
        with get_conn() as conn:
            self.assertIsNone(conn.execute('SELECT id FROM platnik_gui_checks').fetchone())

    def test_queue_deduplicates_and_one_agent_finishes_a_lease(self):
        self.assertEqual(self.request(), {'queued': 1, 'existing': 0})
        self.assertEqual(self.request(), {'queued': 0, 'existing': 1})
        with get_conn() as conn:
            job_id, lease, nip, month = conn.execute('SELECT * FROM platnik_gui_claim()').fetchone()
            self.assertEqual((nip, month), (NIP, MONTH))
        with get_conn() as conn:
            self.assertIsNone(conn.execute('SELECT * FROM platnik_gui_claim()').fetchone())
            finish = 'SELECT platnik_gui_finish(%s, %s, %s, %s)'
            self.assertFalse(conn.execute(finish, (job_id, uuid4(), Jsonb([FINDING]), None)).fetchone()[0])
            self.assertTrue(conn.execute(finish, (job_id, lease, Jsonb([FINDING]), None)).fetchone()[0])
            self.assertFalse(conn.execute(finish, (job_id, lease, Jsonb([]), None)).fetchone()[0])
        with patch('app.services.platnik_gui.assigned_company_ids', return_value={self.company}):
            rows = read_checks(self.admin, MONTH)
        self.assertEqual(rows[0]['findings'], [FINDING])
        with patch('app.services.platnik_gui.assigned_company_ids', return_value=set()):
            self.assertEqual(read_checks(self.admin, MONTH), [])

    def test_expired_lease_can_be_reclaimed_and_locked_desktop_requeues(self):
        self.request()
        with get_conn() as conn:
            job_id, lease, *_ = conn.execute('SELECT * FROM platnik_gui_claim()').fetchone()
            conn.execute("UPDATE platnik_gui_checks SET lease_until=now()-interval '1 second' WHERE id=%s",
                         (job_id,))
        with get_conn() as conn:
            reclaimed_job, renewed_lease, *_ = conn.execute('SELECT * FROM platnik_gui_claim()').fetchone()
            self.assertEqual(reclaimed_job, job_id)
            self.assertNotEqual(renewed_lease, lease)
            finish = 'SELECT platnik_gui_finish(%s, %s, %s, %s)'
            self.assertFalse(conn.execute(finish, (job_id, lease, Jsonb([]), None)).fetchone()[0])
            self.assertTrue(conn.execute(finish, (job_id, renewed_lease, Jsonb([]), 'DesktopUnavailable')).fetchone()[0])
            self.assertEqual(conn.execute('SELECT state,attempts FROM platnik_gui_checks').fetchone(), ('queued', 1))

    def test_agent_role_receives_via_functions_without_table_access(self):
        self.request()
        role = sql.Identifier('test_agent_' + uuid4().hex[:12])
        schema = sql.Identifier(self.schema_name)
        with get_conn() as conn:
            conn.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(role))
            conn.execute(sql.SQL('GRANT USAGE ON SCHEMA {} TO {}').format(schema, role))
            conn.execute(sql.SQL('SET LOCAL ROLE {}').format(role))
            with self.assertRaises(InsufficientPrivilege), conn.transaction():
                conn.execute('SELECT * FROM platnik_gui_claim()')
            conn.execute('RESET ROLE')
            conn.execute(sql.SQL('GRANT EXECUTE ON FUNCTION platnik_gui_claim() TO {}').format(role))
            conn.execute(sql.SQL('GRANT EXECUTE ON FUNCTION platnik_gui_finish(uuid, uuid, jsonb, text) TO {}').format(role))
            conn.execute(sql.SQL('SET LOCAL ROLE {}').format(role))
            with self.assertRaises(InsufficientPrivilege), conn.transaction():
                conn.execute('SELECT * FROM platnik_gui_checks')
            job_id, lease, *_ = conn.execute('SELECT * FROM platnik_gui_claim()').fetchone()
            self.assertTrue(conn.execute('SELECT platnik_gui_finish(%s, %s, %s, %s)',
                                         (job_id, lease, Jsonb([FINDING]), None)).fetchone()[0])
            conn.execute('RESET ROLE')
            conn.execute(sql.SQL('DROP OWNED BY {}').format(role))
            conn.execute(sql.SQL('DROP ROLE {}').format(role))
