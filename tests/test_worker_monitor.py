from datetime import datetime, timedelta
from unittest import TestCase
from unittest.mock import patch
from zoneinfo import ZoneInfo
from PyQt6.QtWidgets import QApplication
from app.admin.settings.worker_monitor import WorkerMonitor
from app.integrations.ezla.sync import wait_between
import threading

_app = QApplication.instance() or QApplication(['worker-monitor'])


class WorkerMonitorTests(TestCase):
    def test_panel_shows_status_log_progress_pause_and_company_error(self):
        now = datetime.now(ZoneInfo('Europe/Warsaw'))
        with patch('app.security.authorization.is_administrator', return_value=True):
            panel = WorkerMonitor(1)
        try:
            self.assertEqual([panel.tabs.tabText(i) for i in range(panel.tabs.count())], ['Status', 'e-ZLA', 'Import do Enovy', 'Odczyt z Enovy', 'Poczta', 'Faktury i przypomnienia', 'KSeF', 'Saldeo', 'Logi'])
            panel.render({'heartbeat': {'checked_at': now.isoformat(), 'enabled': True, 'jobs': {
                'ezla': {'state': 'running', 'started_at': now.isoformat()}}},
                'events': [{'at': now.isoformat(), 'job': 'ezla', 'state': 'started'}],
                'progress': {'stage': 'Przerwa między firmami', 'company_id': 1, 'position': 1, 'total': 2,
                             'resume_at': (now + timedelta(seconds=30)).isoformat()},
                'ezla_enabled': True, 'ezla_companies': [1],
                'companies': [{'id': 1, 'nazwa': 'Firma Test', 'nip': '1112223344',
                               'sync': {'state': 'failed', 'error_code': '101'}}]})
            self.assertIn('Worker aktywny', panel.summary.text())
            self.assertIn('Przerwa między firmami', panel.progress.text())
            self.assertIn('Firma Test', panel.progress.text())
            self.assertIn('wznowienie za', panel.progress.text())
            self.assertEqual(panel.logs.rowCount(), 1)
            self.assertEqual(panel.ezla.item(0, 3).text(), 'Błąd')
            self.assertIn('login', panel.ezla.item(0, 7).text())
            self.assertFalse(panel.timer.isActive())  # A hidden panel does not poll.
        finally:
            panel.close()

    def test_stale_heartbeat_is_not_reported_as_a_running_worker(self):
        with patch('app.security.authorization.is_administrator', return_value=True):
            panel = WorkerMonitor(1)
        try:
            panel.render({'heartbeat': {'checked_at': '2000-01-01T00:00:00+01:00', 'enabled': True},
                          'events': [], 'progress': {}, 'ezla_enabled': False, 'ezla_companies': [], 'companies': []})
            self.assertIn('Brak aktualnego sygnału', panel.summary.text())
            self.assertIn('ostatni zapisany stan', panel.progress.text())
        finally:
            panel.close()

    def test_monitor_requires_admin_and_shutdown_interrupts_a_pause(self):
        with patch('app.security.authorization.is_administrator', return_value=False), self.assertRaises(PermissionError):
            WorkerMonitor(1)
        stop = threading.Event()
        stop.set()
        self.assertTrue(wait_between(600, stop))
