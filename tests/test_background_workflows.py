"""Durable commands, replaceable reads and streaming work at GUI boundaries."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from PyQt6.QtCore import QCoreApplication, QEvent, QTimer
from PyQt6.QtWidgets import QApplication, QWidget

from app.config import get_schema, schema_context
from app.utils.background import CommandRunner, TaskRunner, _pool
from app.reports.excel_export import write_report
from app.reports.import_parse import iter_work_rows, iter_invoice_rows
from tests.test_import_parse import WORK_HEADER, INV_HEADER

_app = QApplication.instance() or QApplication([])


def wait(predicate, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _app.processEvents()
        if predicate():
            return True
        threading.Event().wait(.005)
    return predicate()


class BackgroundWorkflowTests(unittest.TestCase):
    def test_commands_run_fifo_deliver_every_result_and_keep_submission_schema(self):
        owner = QWidget()
        runner = CommandRunner(owner)
        started, release = threading.Event(), threading.Event()
        order, results = [], []
        main_thread = threading.get_ident()
        def first():
            started.set()
            release.wait(3)
            order.append('first')
            return get_schema(), threading.get_ident()
        with schema_context('original_schema'):
            runner.submit(first, lambda r: results.append((r, threading.get_ident())))
        self.assertTrue(started.wait(1))
        with schema_context('second_schema'):
            runner.submit(lambda: (order.append('second'), get_schema())[1], results.append)
        self.assertEqual(order, [])
        release.set()
        self.assertTrue(wait(lambda: len(results) == 2))
        self.assertEqual(order, ['first', 'second'])
        self.assertEqual(results[0][0][0], 'original_schema')
        self.assertNotEqual(results[0][0][1], main_thread)
        self.assertEqual(results[0][1], main_thread)
        self.assertEqual(results[1], 'second_schema')
        owner.deleteLater()

    def test_destroying_page_suppresses_ui_callbacks_but_finishes_accepted_writes(self):
        owner = QWidget()
        runner = CommandRunner(owner)
        started, release = threading.Event(), threading.Event()
        writes, callbacks = [], []
        def slow():
            started.set()
            release.wait(3)
            writes.append(1)
        runner.submit(slow, callbacks.append)
        self.assertTrue(started.wait(1))
        runner.submit(lambda: writes.append(2), callbacks.append)
        owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        release.set()
        self.assertTrue(wait(lambda: writes == [1, 2] and not runner._jobs))
        self.assertEqual(callbacks, [])

    def test_queue_capacity_rejects_excess_work_without_cancelling_accepted_commands(self):
        runner = CommandRunner(capacity=2)
        release = threading.Event()
        results = []
        runner.submit(lambda: release.wait(3), results.append)
        runner.submit(lambda: 'second', results.append)
        with self.assertRaises(RuntimeError):
            runner.submit(lambda: self.fail('Rejected command ran'), results.append)
        release.set()
        self.assertTrue(wait(lambda: len(results) == 2))
        self.assertEqual(results[1], 'second')

    def test_replaceable_read_queue_removes_superseded_pending_jobs(self):
        runner = TaskRunner()
        signals, started = [], []
        original_threads = _pool.maxThreadCount()
        _pool.setMaxThreadCount(1)
        gate, entered = threading.Event(), threading.Event()
        def blocked():
            entered.set()
            gate.wait(3)
        try:
            runner.submit(blocked, signals.append)
            self.assertTrue(entered.wait(1))
            for value in range(200):
                runner.submit(lambda v=value: (started.append(v), v)[1], signals.append)
            self.assertLessEqual(len(runner._jobs), 2)
            gate.set()
            self.assertTrue(wait(lambda: signals == [199]))
            self.assertEqual(started, [199])
        finally:
            gate.set()
            _pool.setMaxThreadCount(original_threads)

    def test_failed_command_does_not_drop_following_work(self):
        runner, results = CommandRunner(), []
        def fail():
            raise ValueError('failed write')
        runner.submit(fail, results.append, lambda exc: results.append(str(exc)))
        runner.submit(lambda: 'next write', results.append)
        self.assertTrue(wait(lambda: len(results) == 2))
        self.assertEqual(results, ['failed write', 'next write'])

    def test_background_command_leaves_qt_timer_responsive(self):
        runner, release, ticks = CommandRunner(), threading.Event(), []
        timer = QTimer()
        timer.setInterval(5)
        timer.timeout.connect(lambda: ticks.append(1))
        timer.start()
        runner.submit(lambda: release.wait(3))
        try:
            self.assertTrue(wait(lambda: len(ticks) >= 3))
        finally:
            release.set()
            timer.stop()
            self.assertTrue(wait(lambda: not runner._jobs))

    def test_parsers_yield_the_first_row_without_consuming_the_rest(self):
        def work_source():
            yield WORK_HEADER
            yield ('Firma', '1234567890', 'Jan Kowalski', '', '2026-11-04', '08:00', '09:00',
                   '1:00:00', 60, '0:00:00', 0, 'Księgowość', '')
            raise AssertionError('Parser consumed another row before yielding')
        work = iter_work_rows(work_source())
        self.assertEqual(next(work)['firma_nazwa'], 'Firma')
        work.close()
        def invoice_source():
            yield INV_HEADER
            yield (1, 'Firma', '1234567890', 'FV/11/2026', 'Usługa księgowa', '', 100, 'PLN')
            raise AssertionError('Parser consumed another row before yielding')
        invoices = iter_invoice_rows(invoice_source())
        self.assertEqual(next(invoices)['kontrahent'], 'Firma')
        invoices.close()

    def test_excel_failure_preserves_previous_file_and_success_neutralizes_formulas(self):
        import openpyxl
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report.xlsx'
            path.write_bytes(b'previous successful export')
            def broken():
                yield ['first']
                raise ValueError('query failed')
            with self.assertRaises(ValueError):
                write_report(path, 'Report', ['Title'], broken())
            self.assertEqual(path.read_bytes(), b'previous successful export')
            self.assertEqual(list(Path(directory).iterdir()), [path])
            write_report(path, 'Report', ['Title'], [['=HYPERLINK("https://example.com")'], [42]])
            book = openpyxl.load_workbook(path)
            try:
                self.assertEqual(book.active['A2'].data_type, 's')
                self.assertTrue(book.active['A2'].value.startswith("'="))
                self.assertEqual(book.active['A3'].value, 42)
            finally:
                book.close()
