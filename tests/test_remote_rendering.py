"""Rendering received snapshots must stay local; background RPCs leave UI capacity."""
import threading
import unittest
from unittest import mock

from app.remote.client import OfficeClient


class RemoteRenderingTests(unittest.TestCase):
    def test_formatters_and_declaration_filters_do_not_call_the_server_per_row(self):
        from app.db.dao_dashboard import format_duration,format_event_time
        from app.integrations.enova.accounting import documents,active_types,declaration_entries,declarations
        from app.companies.checking_obligations import profile,report_applies
        connection = mock.Mock()
        connection.call.side_effect = AssertionError('Rendering must not make an RPC')
        row = {'checking_period':'2026-11','vat_czynny':True,
               'checking':{'documents':{'accounting':{'SPU':3}},
                           'declarations':[{'form':'JPK_VAT','department':'accounting','status':'accepted','upo':'yes'}]},
               'history':[{'documents':{'accounting':{'SPU':2}}}]}
        with mock.patch('app.remote.state._client',connection):
            for _ in range(100):
                self.assertEqual(format_duration(3600),'1 h 00 min')
                self.assertEqual(format_event_time({'start':'2026-11-16T09:30:00'}),'16.11  09:30')
                self.assertEqual(documents(row,'accounting'),{'SPU':3})
                self.assertEqual(active_types(row,'accounting'),{'SPU'})
                self.assertEqual(len(declaration_entries(row,'accounting')),1)
                self.assertIn('JPK_VAT',declarations(row,'accounting'))
                self.assertTrue(report_applies(row,'JPK_VAT'))
                self.assertEqual(profile({},'2026-11')['vat_period'],'auto')
        connection.call.assert_not_called()

    def test_ui_hints_are_copied_and_only_apply_to_the_logged_in_employee(self):
        client = OfficeClient('https://localhost')
        client.employee_id,client.token = 7,'session-token'
        client._ui_authorization = {'administrator':False,'permissions':{'can_view_finances':True},'tabs':['Pulpit']}
        with mock.patch.object(client,'_call',return_value='server') as remote:
            tabs = client.call('app.security.authorization.allowed_tabs',(7,))
            tabs.append('Ustawienia')
            self.assertEqual(client.call('app.security.authorization.allowed_tabs',(7,)),['Pulpit'])
            self.assertTrue(client.call('app.security.authorization.can',(7,'can_view_finances')))
            self.assertEqual(client.call('app.security.authorization.allowed_tabs',(8,)),'server')
            self.assertEqual(client.call('app.db.dao_employees.list_employees'),'server')
            self.assertEqual(remote.call_count,2)

    def test_background_operations_leave_capacity_for_foreground_without_retries(self):
        client = OfficeClient('https://localhost')
        release = threading.Event()
        two_started = threading.Event()
        lock = threading.Lock()
        running = 0
        maximum = 0
        errors = []
        def operation(*args,**kwargs):
            nonlocal running,maximum
            if threading.current_thread() is threading.main_thread():
                return 'foreground'
            with lock:
                running += 1
                maximum = max(maximum,running)
                if running == 2:
                    two_started.set()
            try:
                if not release.wait(10):
                    raise AssertionError('background operation did not finish')
            finally:
                with lock:
                    running -= 1
        def background():
            try:
                client.call('background')
            except Exception as error:
                errors.append(error)
        with mock.patch.object(client,'_call',side_effect=operation) as call:
            threads = [threading.Thread(target=background) for _ in range(4)]
            try:
                for thread in threads:
                    thread.start()
                self.assertTrue(two_started.wait(5))
                self.assertEqual(client.call('foreground'),'foreground')
            finally:
                release.set()
                for thread in threads:
                    thread.join(10)
            self.assertEqual(call.call_count,5)
        self.assertFalse(errors)
        self.assertEqual(maximum,2)
        self.assertFalse(any(thread.is_alive() for thread in threads))
