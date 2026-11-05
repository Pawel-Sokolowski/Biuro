"""Regression coverage for mailbox identity, bounded work and database round trips."""
import base64
import datetime
import hashlib
import io
import threading
import unittest
from unittest import mock

from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication, QWidget
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from psycopg_pool import PoolTimeout

from app.db.dao_conn import get_conn
from app.email.email_storage import (
    get_known_uids, get_mail_page, get_message_row_id, load_raw_message,
    record_mailbox_generation, save_folder, save_message_header, save_raw_message,
    set_employee_assignment, get_assignment_map,
)
from tests.base import DbTestCase


def wait_until(predicate, timeout=3000):
    if predicate():
        return True
    loop = QEventLoop()
    poll = QTimer()
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
    poll.start(10)
    QTimer.singleShot(timeout, loop.quit)
    loop.exec()
    poll.stop()
    return bool(predicate())


class MailGenerationTests(DbTestCase):
    def setUp(self):
        super().setUp()
        with get_conn() as conn:
            conn.execute("INSERT INTO konta_email(id,email) VALUES (1,'test@example.com')")
        self.folder = save_folder(1, "INBOX")

    def add_message(self, uid, subject="Test", recipient="client@example.com"):
        save_message_header(1, self.folder, str(uid), f"<{subject}-{uid}@example.com>",
                            "sender@example.com", recipient, subject, "2026-11-01 10:00", False, False)
        return get_message_row_id(1, self.folder, str(uid))

    def test_reused_uid_does_not_reuse_body_or_destroy_assignment_history(self):
        record_mailbox_generation(1, "INBOX", "10")
        old_id = self.add_message(42, "Old")
        save_raw_message(old_id, b"old body")
        set_employee_assignment([old_id], 1, True)
        self.assertTrue(record_mailbox_generation(1, "INBOX", "11"))
        self.assertNotIn("42", get_known_uids(1, self.folder))
        new_id = self.add_message(42, "New")
        self.assertNotEqual(old_id, new_id)
        self.assertIsNone(load_raw_message(new_id))
        self.assertEqual(load_raw_message(old_id), b"old body")
        self.assertEqual(get_assignment_map([old_id])[old_id], [1])
        self.assertEqual([r["id"] for r in get_mail_page(1, "INBOX")[0]], [new_id])
        save_raw_message(new_id, b"new body")
        from app.email.email_sync import fetch_message_from_imap
        with mock.patch("app.email.email_sync._shared_session", side_effect=AssertionError("stale selection must not fetch")):
            self.assertIsNone(fetch_message_from_imap(1, "INBOX", "42", expected_row_id=old_id))
        from app.email.email_sync import delete_email_remote, move_emails, mark_read_remote
        session = mock.Mock()
        session.select.return_value = True
        with mock.patch("app.email.email_sync._shared_session") as shared:
            shared.return_value.__enter__.return_value = session
            for operation in (
                lambda: delete_email_remote(1, "INBOX", "42", expected_row_id=old_id),
                lambda: move_emails(1, "INBOX", ["42"], "Archive", expected_rows={"42": old_id}),
                lambda: mark_read_remote(1, "INBOX", "42", expected_row_id=old_id),
            ):
                with self.assertRaisesRegex(RuntimeError, "identyfikator"):
                    operation()
            session.conn.uid.assert_not_called()

    def test_same_generation_preserves_rows_and_first_adoption_retires_unknown_uids(self):
        old = self.add_message(7)
        self.assertTrue(record_mailbox_generation(1, "INBOX", "5"))
        current = self.add_message(7)
        self.assertFalse(record_mailbox_generation(1, "INBOX", "5"))
        self.assertEqual(get_message_row_id(1, self.folder, "7"), current)
        self.assertNotEqual(old, current)

    def test_keyset_pages_are_stable_when_dates_tie_and_recipient_search_precedes_limit(self):
        ids = [self.add_message(i, recipient="older@example.com" if i == 1 else "other@example.com") for i in range(1, 8)]
        first, more, cursor = get_mail_page(1, "INBOX", limit=3)
        second, more2, cursor2 = get_mail_page(1, "INBOX", limit=3, before=cursor)
        third, more3, _ = get_mail_page(1, "INBOX", limit=3, before=cursor2)
        self.assertTrue(more and more2)
        self.assertFalse(more3)
        self.assertEqual([r["id"] for r in first + second + third], list(reversed(ids)))
        self.assertEqual([r["id"] for r in get_mail_page(1, "INBOX", phrase="older@", limit=1)[0]], [ids[0]])

    def test_search_treats_wildcards_as_literal_text(self):
        literal = self.add_message(1, "100%_done")
        self.add_message(2, "100anythingdone")
        self.assertEqual([r["id"] for r in get_mail_page(1, "INBOX", phrase="%_")[0]], [literal])

    def test_reused_uid_clears_old_preview_and_invalidates_body_callbacks(self):
        from app.email import email_ui
        from PyQt6.QtWidgets import QTextBrowser
        record_mailbox_generation(1, "INBOX", "10")
        old_id = self.add_message(42, "Old")
        with mock.patch.object(email_ui, "get_email_accounts_for_employee", return_value=[{"id": 1, "email": "test@example.com"}]), \
                mock.patch.object(email_ui.EmailPage, "start_background_sync"), \
                mock.patch.object(email_ui, "MailBrowser", QTextBrowser):
            page = email_ui.EmailPage(1)
            page.current_folder_local = "INBOX"
            page.load_mail_list_from_db("INBOX")
            page.current_uid = "42"
            page._current_message_row_id = old_id
            page.cached_message = object()
            token = page._load_token
            record_mailbox_generation(1, "INBOX", "11")
            self.add_message(42, "New")
            page.load_mail_list_from_db("INBOX", keep_view=True)
            self.assertIsNone(page.cached_message)
            self.assertIsNone(page.current_uid)
            self.assertGreater(page._load_token, token)
            page.stop_background_sync()
            page.deleteLater()


class DatabaseOptimizationTests(DbTestCase):
    def test_pool_bounds_total_connections_and_return_rolls_back(self):
        leases = []
        try:
            first = get_conn()
            leases.append(first)
            pool = first._pool
            leases.extend(get_conn() for _ in range(pool.max_size - 1))
            with self.assertRaises(PoolTimeout):
                pool.getconn(timeout=0.02)
            first.execute("INSERT INTO firmy(nazwa) VALUES ('uncommitted review row')")
            first.close()
            leases.pop(0)
            with get_conn() as conn:
                self.assertIsNone(conn.execute("SELECT 1 FROM firmy WHERE nazwa='uncommitted review row'").fetchone())
        finally:
            for conn in leases:
                conn.close()

    def test_warm_year_end_has_constant_query_count_and_preserves_snapshot(self):
        from app.db import dao_year_end
        with get_conn() as conn:
            conn.execute("INSERT INTO firmy(nazwa,rozliczenie_roczne) SELECT 'Review ' || n, 'PIT-36' FROM generate_series(1,20) n")
        original = dao_year_end.get_conn
        dao_year_end.list_year_end(2026)
        statements = []
        class Counting:
            def __init__(self): self.real = original()
            def __enter__(self): return self
            def __exit__(self, *args): return self.real.__exit__(*args)
            def execute(self, sql, *args):
                statements.append(sql)
                return self.real.execute(sql, *args)
            def __getattr__(self, key): return getattr(self.real, key)
        with get_conn() as conn:
            conn.execute("UPDATE firmy SET rozliczenie_roczne='PIT-28' WHERE nazwa LIKE 'Review %%'")
        with mock.patch.object(dao_year_end, "get_conn", Counting):
            rows = dao_year_end.list_year_end(2026)
        self.assertLessEqual(len(statements), 4)
        self.assertEqual(len(rows), 21)
        self.assertTrue(all(r["deklaracje"][0]["kod"] == "PIT-36" for r in rows if r["nazwa"].startswith("Review ")))

    def test_timer_labels_are_read_once_per_active_timer(self):
        from app.monthly_view.monthly_view_logic import MonthlyViewLogic
        company = self.make_company("Cached label")
        with mock.patch.object(MonthlyViewLogic, "_auto_check_suspension"):
            logic = MonthlyViewLogic(1)
        logic.aktywna_firma_stoper_ks = company
        logic.czas_start_ks = datetime.datetime.now()
        logic.aktywna_firma_stoper_ks_activity = {"task_title": "Work"}
        with mock.patch("app.monthly_view.monthly_view_logic.get_conn", wraps=get_conn) as connect:
            for _ in range(10):
                self.assertEqual(logic.get_active_timer_details()[0]["company"], "Cached label")
        self.assertEqual(connect.call_count, 1)

    def test_migration_rerun_does_not_replay_legacy_bootstrap(self):
        from app.db import schema_baseline
        from app.db.schema import upgrade_database
        with mock.patch.object(schema_baseline, "init_db", wraps=schema_baseline.init_db) as bootstrap:
            upgrade_database()
            upgrade_database()
        self.assertEqual(bootstrap.call_count, 1)
        with get_conn() as conn:
            self.assertEqual(conn.execute("SELECT version_num FROM alembic_version").fetchone()[0], "20261104_02")


class BackgroundAndStreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_stale_job_cannot_replace_new_result_and_worker_keeps_schema(self):
        from app.utils.background import TaskRunner
        from app.config import get_schema, schema_context
        owner = QWidget()
        runner = TaskRunner(owner)
        release, started, finished = threading.Event(), threading.Event(), threading.Event()
        results = []
        def slow():
            started.set()
            release.wait(2)
            finished.set()
            return "stale"
        try:
            runner.submit(slow, results.append)
            self.assertTrue(started.wait(1))
            with schema_context("origin_schema"):
                runner.submit(lambda: (get_schema(), threading.get_ident()), results.append)
            self.assertTrue(wait_until(lambda: len(results) == 1))
            self.assertEqual(results[0][0], "origin_schema")
            self.assertNotEqual(results[0][1], threading.get_ident())
        finally:
            release.set()
            self.assertTrue(finished.wait(2))
            self.app.processEvents()
            owner.deleteLater()
        self.assertEqual(len(results), 1)

    def test_export_part_streams_without_reading_response_content(self):
        from app.invoices.ksef_client import _stream_export_part, KsefError
        key, iv = b"K" * 32, b"I" * 16
        raw = b"invoice data" * 1000
        padder = PKCS7(128).padder()
        padded = padder.update(raw) + padder.finalize()
        encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        encrypted = encryptor.update(padded) + encryptor.finalize()
        part = {"partSize": len(raw), "encryptedPartSize": len(encrypted),
                "partHash": base64.b64encode(hashlib.sha256(raw).digest()).decode(),
                "encryptedPartHash": base64.b64encode(hashlib.sha256(encrypted).digest()).decode()}
        class Response:
            @property
            def content(self): raise AssertionError("Full response must not be buffered")
            def iter_content(self, chunk_size):
                for i in range(0, len(encrypted), 17):
                    yield encrypted[i:i + 17]
        target = io.BytesIO()
        _stream_export_part(Response(), part, key, iv, target)
        self.assertEqual(target.getvalue(), raw)
        with self.assertRaises(KsefError):
            _stream_export_part(Response(), {**part, "encryptedPartSize": 16}, key, iv, io.BytesIO())
        with self.assertRaises(KsefError):
            _stream_export_part(Response(), {**part, "partHash": "invalid"}, key, iv, io.BytesIO())

    def test_xml_money_rounding_is_independent_of_decimal_context(self):
        from decimal import localcontext, ROUND_HALF_EVEN, Decimal
        from app.invoices.fa3_builder import _amount
        from app.invoices.ksef_sync import _decimal
        with localcontext() as context:
            context.rounding = ROUND_HALF_EVEN
            self.assertEqual(_amount("1.005"), Decimal("1.01"))
            self.assertEqual(_decimal("-1.005", "test"), Decimal("-1.01"))
