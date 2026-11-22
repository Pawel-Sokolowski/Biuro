from unittest.mock import patch
from uuid import uuid4

from psycopg import sql
from psycopg.errors import InsufficientPrivilege
from psycopg.types.json import Jsonb

from app.db.dao_conn import get_conn
from app.services.platnik_gui import read_checks, request_checks
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
