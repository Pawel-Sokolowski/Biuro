"""Interleavings at the actual commit/network boundaries, with isolated PostgreSQL schemas."""
import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import requests

from app.config import schema_context
from app.db import dao_calendar as events, dao_calendar_sync as outbox
from app.db.dao_app_settings import get_setting, set_setting
from app.db.dao_conn import get_conn
from app.kalendarz import google_sync, caldav_sync
from tests.base import DbTestCase
from tests.test_calendar_caldav import FakeCalendar


class CalendarOutboxTests(DbTestCase):
    def event(self, **extra):
        data = dict(tytul="Original", start="2026-11-04T09:00", koniec="2026-11-04T10:00", **extra)
        return events.get_event(events.save_event(1, data))

    def google(self):
        stack = __import__('contextlib').ExitStack()
        stack.enter_context(mock.patch.object(google_sync, '_require_access', side_effect=lambda actor, scope: scope))
        stack.enter_context(mock.patch.object(google_sync, '_headers', return_value={}))
        self.addCleanup(stack.close)

    def test_google_timeout_retry_uses_one_remote_identity(self):
        self.google()
        event = self.event(google_calendar_id='cal')
        remote = {}
        def patch(url, **kw):
            rid = url.rsplit('/', 1)[1]
            if rid not in remote:
                return mock.Mock(status_code=404)
            remote[rid] = kw['json']
            return mock.Mock(status_code=200)
        def post(url, **kw):
            body = kw['json']
            remote[body['id']] = body
            raise requests.Timeout('Committed remotely; response lost')
        with mock.patch.object(google_sync.requests, 'patch', side_effect=patch), \
                mock.patch.object(google_sync.requests, 'post', side_effect=post) as create:
            with self.assertRaises(requests.Timeout):
                google_sync.push_event(1, event)
            self.assertEqual(len(outbox.operations(1, 'google')), 1)
            google_sync.push_event(1, events.get_event(event['id']))
        self.assertEqual(create.call_count, 1)
        self.assertEqual(len(remote), 1)
        self.assertEqual(events.get_event(event['id'])['google_event_id'], next(iter(remote)))
        self.assertEqual(outbox.operations(1, 'google'), [])

    def test_google_conflicting_create_reconciles_the_stable_id(self):
        self.google()
        event = self.event(google_calendar_id='cal')
        with mock.patch.object(google_sync.requests, 'patch', side_effect=[mock.Mock(status_code=404), mock.Mock(status_code=200)]) as patch, \
                mock.patch.object(google_sync.requests, 'post', return_value=mock.Mock(status_code=409)) as post:
            google_sync.push_event(1, event)
        self.assertEqual(patch.call_count, 2)
        self.assertIn(post.call_args.kwargs['json']['id'], patch.call_args.args[0])
        self.assertEqual(outbox.operations(1, 'google'), [])

    def test_caldav_timeout_retry_finds_the_committed_event(self):
        caldav_sync.save_config('https://fake/', 'u', 'secret')
        event, calendar = self.event(), FakeCalendar()
        real_save = calendar.save_event
        def lost_response(data):
            real_save(data)
            raise TimeoutError('Committed remotely; response lost')
        with mock.patch.object(calendar, 'save_event', side_effect=lost_response):
            with self.assertRaises(caldav_sync.CaldavError):
                caldav_sync.push_event(1, event, calendar)
        caldav_sync.push_event(1, events.get_event(event['id']), calendar)
        self.assertEqual(len(calendar.events), 1)
        self.assertEqual(outbox.operations(1, 'caldav'), [])
        self.assertTrue(events.get_event(event['id'])['caldav_uid'])

    def test_network_error_during_lookup_never_creates_an_event(self):
        caldav_sync.save_config('https://fake/', 'u', 'secret')
        event, calendar = self.event(), mock.Mock()
        calendar.event_by_uid.side_effect = TimeoutError('offline')
        with self.assertRaises(caldav_sync.CaldavError):
            caldav_sync.push_event(1, event, calendar)
        calendar.save_event.assert_not_called()
        self.assertEqual(len(outbox.operations(1, 'caldav')), 1)

    def test_late_ack_and_pull_cannot_overwrite_newer_edit(self):
        self.google()
        event = self.event(google_calendar_id='cal', google_event_id='gid')
        def write(*args, **kw):
            updated = events.get_event(event['id'])
            updated['tytul'] = 'New edit'
            events.save_event(1, updated)
            return mock.Mock(status_code=200)
        with mock.patch.object(google_sync.requests, 'patch', side_effect=write):
            google_sync.push_event(1, event)
        pending = outbox.operations(1, 'google')
        self.assertEqual(pending[0]['payload']['tytul'], 'New edit')
        items = [{'id': 'gid', 'summary': 'Old remote edit', 'start': {'dateTime': event['start']},
                  'end': {'dateTime': event['koniec']}}, {'id': 'gid', 'status': 'cancelled'}]
        with mock.patch.object(google_sync.requests, 'get', return_value=mock.Mock(status_code=200, json=lambda: {'items': items})):
            google_sync._pull_calendar(1, 'cal', {}, {}, {'pobrane': 0, 'usuniete': 0})
        self.assertEqual(events.get_event(event['id'])['tytul'], 'New edit')
        # The DAO also guards callers racing after the provider's initial check.
        events.save_event(1, {**event, 'tytul': 'Old'}, from_remote=True)
        events.delete_event(event['id'], 1, from_remote=True)
        self.assertEqual(events.get_event(event['id'])['tytul'], 'New edit')

    def test_delete_during_push_retains_a_durable_tombstone(self):
        self.google()
        event = self.event(google_calendar_id='cal', google_event_id='gid')
        def write(*args, **kw):
            events.delete_event(event['id'], 1)
            return mock.Mock(status_code=200)
        with mock.patch.object(google_sync.requests, 'patch', side_effect=write):
            google_sync.push_event(1, event)
        self.assertIsNone(events.get_event(event['id']))
        operation = outbox.operations(1, 'google')[0]
        self.assertIsNone(operation['event_id'])
        self.assertEqual(operation['action'], 'delete')
        with mock.patch.object(google_sync.requests, 'delete', side_effect=requests.Timeout):
            with self.assertRaises(requests.Timeout):
                google_sync.delete_remote(1, 'cal', 'gid')
        self.assertEqual(outbox.operations(1, 'google'), [operation])
        with mock.patch.object(google_sync.requests, 'delete', return_value=mock.Mock(status_code=404)):
            google_sync.delete_remote(1, 'cal', 'gid')
        self.assertEqual(outbox.operations(1, 'google'), [])

    def test_ack_does_not_discard_another_calendar_or_concurrent_event(self):
        a = self.event(google_calendar_id='a', google_event_id='same-id')
        b = self.event(google_calendar_id='b', google_event_id='same-id')
        original = outbox.operations(1, 'google', 'a')[0]
        with ThreadPoolExecutor(1) as pool:
            def concurrent():
                with schema_context(self.schema_name):
                    return self.event(google_calendar_id='a')
            c = pool.submit(concurrent).result(3)
        self.assertTrue(outbox.acknowledge(original))
        self.assertEqual({op['event_id'] for op in outbox.operations(1, 'google')}, {b['id'], c['id']})
        self.assertEqual(events.get_event_by_google_id(1, 'same-id', 'a')['id'], a['id'])
        self.assertEqual(events.get_event_by_google_id(1, 'same-id', 'b')['id'], b['id'])

    def test_legacy_json_migration_keeps_newer_transactional_changes(self):
        event = self.event(google_calendar_id='a', google_event_id='gid')
        op = outbox.operations(1, 'google')[0]
        key = google_sync._pending_key(1)
        set_setting(key, json.dumps({'gid': {'kalendarz': 'a', 'akcja': 'delete'},
                                     'old-deleted': {'kalendarz': 'b', 'akcja': 'delete'}}))
        outbox.migrate_google(1)
        self.assertIsNone(get_setting(key))
        self.assertEqual(outbox.operations(1, 'google', 'a'), [op])
        self.assertEqual(outbox.operations(1, 'google', 'b')[0]['action'], 'delete')
        self.assertIsNotNone(events.get_event(event['id']))
        set_setting(key, 'invalid JSON')
        with self.assertRaises(ValueError):
            outbox.migrate_google(1)
        self.assertEqual(get_setting(key), 'invalid JSON')

    def test_local_save_and_queue_commit_atomically(self):
        event = self.event(google_calendar_id='a')
        with mock.patch.object(outbox, 'queue_event', side_effect=RuntimeError('queue unavailable')):
            with self.assertRaises(RuntimeError):
                events.save_event(1, {**event, 'tytul': 'Must roll back'})
        self.assertEqual(events.get_event(event['id'])['tytul'], 'Original')
        self.assertEqual(outbox.operations(1, 'google')[0]['payload']['tytul'], 'Original')

    def test_calendar_lock_serializes_pushes_without_blocking_local_edits(self):
        self.google()
        event = self.event(google_calendar_id='a', google_event_id='gid')
        entered, release = threading.Event(), threading.Event()
        bodies = []
        def remote(*args, **kw):
            bodies.append(copy.deepcopy(kw['json']))
            if len(bodies) == 1:
                entered.set()
                if not release.wait(4):
                    raise TimeoutError('test did not release remote')
            return mock.Mock(status_code=200)
        def push():
            with schema_context(self.schema_name):
                google_sync.push_event(1, event)
        with mock.patch.object(google_sync.requests, 'patch', side_effect=remote), ThreadPoolExecutor(2) as pool:
            first = pool.submit(push)
            try:
                self.assertTrue(entered.wait(2))
                events.save_event(1, {**event, 'tytul': 'Newer'})
                second = pool.submit(push)
            finally:
                release.set()
            first.result(4)
            second.result(4)
        self.assertEqual([b['summary'] for b in bodies], ['Original', 'Newer'])
        self.assertEqual(outbox.operations(1, 'google'), [])

    def test_calendar_change_during_push_cannot_restore_the_old_calendar_link(self):
        self.google()
        event = self.event(google_calendar_id='old', google_event_id='gid')
        def write(*args, **kw):
            events.save_event(1, {**events.get_event(event['id']), 'google_calendar_id': 'new'})
            return mock.Mock(status_code=200)
        with mock.patch.object(google_sync.requests, 'patch', side_effect=write):
            google_sync.push_event(1, event)
        changed = events.get_event(event['id'])
        self.assertEqual(changed['google_calendar_id'], 'new')
        self.assertIsNone(changed['google_event_id'])
        self.assertEqual(outbox.operations(1, 'google', 'old')[0]['action'], 'delete')
        self.assertEqual(outbox.operations(1, 'google', 'new')[0]['action'], 'upsert')

    def test_caldav_offline_edits_are_pushed_on_the_next_sync(self):
        caldav_sync.save_config('https://fake/', 'u', 'secret')
        event, calendar = self.event(), FakeCalendar()
        with mock.patch.object(caldav_sync, '_selected_calendar', return_value=calendar), \
                mock.patch.object(caldav_sync, '_connect'):
            caldav_sync.sync(1)
            events.save_event(1, {**events.get_event(event['id']), 'tytul': 'Offline edit'})
            self.assertEqual(len(outbox.operations(1, 'caldav')), 1)
            caldav_sync.sync(1)
        self.assertEqual(len(calendar.events), 1)
        self.assertIn('Offline edit', calendar.events[0].data)
        self.assertEqual(outbox.operations(1, 'caldav'), [])

    def test_caldav_switch_does_not_delete_or_overwrite_another_calendar(self):
        from tests.test_calendar_caldav import _today_ical
        caldav_sync.save_config('https://first/', 'u', 'secret')
        first = FakeCalendar([_today_ical()])
        caldav_sync.pull_events(1, first)
        scope_a = outbox.caldav_scope()
        caldav_sync.save_config('https://second/', 'u', 'secret')
        second = FakeCalendar([_today_ical().replace('Spotkanie z klientem', 'Other calendar')])
        caldav_sync.pull_events(1, second)
        scope_b = outbox.caldav_scope()
        self.assertNotEqual(scope_a, scope_b)
        self.assertEqual(len(events.list_caldav_events(1)), 2)
        self.assertEqual(events.list_caldav_events(1, scope_a)[0]['tytul'], 'Spotkanie z klientem')
        self.assertEqual(events.list_caldav_events(1, scope_b)[0]['tytul'], 'Other calendar')
        second.events.clear()
        caldav_sync.pull_events(1, second)
        self.assertEqual(len(events.list_caldav_events(1, scope_a)), 1)
        self.assertEqual(events.list_caldav_events(1, scope_b), [])

    def test_unavailable_selected_calendar_is_not_replaced_by_another_calendar(self):
        cfg = {'kalendarz': 'https://missing/calendar/'}
        with mock.patch.object(caldav_sync, '_calendars', return_value=[FakeCalendar()]):
            with self.assertRaises(caldav_sync.CaldavError):
                caldav_sync._selected_calendar(mock.Mock(), cfg)

    def test_configuration_change_during_connection_uses_the_captured_scope(self):
        caldav_sync.save_config('https://first/', 'u', 'secret')
        event, calendar = self.event(), FakeCalendar()
        first_scope = outbox.caldav_scope()
        def connect(cfg):
            caldav_sync.save_config('https://second/', 'u', 'secret')
            return mock.Mock()
        with mock.patch.object(caldav_sync, '_connect', side_effect=connect), \
                mock.patch.object(caldav_sync, '_selected_calendar', return_value=calendar):
            caldav_sync.sync(1)
        self.assertEqual(len(calendar.events), 1)
        self.assertEqual(events.get_event(event['id'])['caldav_calendar_id'], first_scope)
        self.assertEqual(outbox.operations(1, 'caldav', first_scope), [])

    def test_an_old_ack_cannot_clear_a_reenqueued_operation_after_an_earlier_ack(self):
        event = self.event(google_calendar_id='a', google_event_id='gid')
        original = outbox.operations(1, 'google')[0]
        self.assertTrue(outbox.acknowledge(original))
        events.save_event(1, {**event, 'tytul': 'Another edit'})
        self.assertFalse(outbox.acknowledge(original))
        self.assertEqual(outbox.operations(1, 'google')[0]['payload']['tytul'], 'Another edit')
