"""Slow refreshes remain useful and do not multiply read work."""
import threading
from unittest import TestCase

from PyQt6.QtWidgets import QApplication
from app.utils.background import TaskRunner
from tests.test_review_optimizations import wait_until

_app = QApplication.instance() or QApplication(['read-coalescing'])


class ReadCoalescingTests(TestCase):
    def test_identical_requests_deliver_inflight_then_one_rerun(self):
        runner = TaskRunner()
        entered, release = threading.Event(), threading.Event()
        calls, delivered = [], []

        def slow():
            calls.append('first')
            entered.set()
            if not release.wait(10):
                raise TimeoutError('read barrier')
            return 1

        def latest():
            calls.append('rerun')
            return 2

        runner.submit(slow, delivered.append, key=('invoices', 1))
        try:
            self.assertTrue(entered.wait(10))
            for _ in range(20):
                runner.submit(latest, delivered.append, key=('invoices', 1))
        finally:
            release.set()
        wait_until(lambda: delivered == [1, 2])
        self.assertEqual(calls, ['first', 'rerun'])

    def test_context_change_discards_old_result_and_pending_rerun(self):
        runner = TaskRunner()
        entered, release = threading.Event(), threading.Event()
        calls, delivered = [], []

        def slow():
            entered.set()
            release.wait(10)
            return 'old'

        runner.submit(slow, delivered.append, key='old')
        try:
            self.assertTrue(entered.wait(10))
            runner.submit(lambda: calls.append('obsolete rerun'), delivered.append, key='old')
            runner.submit(lambda: 'new', delivered.append, key='new')
        finally:
            release.set()
        wait_until(lambda: not runner._jobs)
        self.assertEqual(delivered, ['new'])
        self.assertEqual(calls, [])
