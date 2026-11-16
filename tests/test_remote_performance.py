"""Navigation timing aggregates short RPCs without retaining the next view's data."""
import unittest
from unittest import mock

from app.remote.performance import navigation_timing,record_request


class NavigationTimingTests(unittest.TestCase):
    def test_short_requests_are_aggregated_and_scope_is_reset_after_failure(self):
        with mock.patch('app.remote.performance.perf_counter',side_effect=[10,11,20,21]):
            with self.assertLogs('app.remote.performance',level='INFO') as logs:
                with self.assertRaises(ValueError):
                    with navigation_timing(3):
                        for _ in range(8):
                            record_request('operations','read_company',.04)
                        raise ValueError('view failed')
                record_request('operations','outside_scope',5)
                with navigation_timing(4):
                    pass
        self.assertIn('page=3',logs.output[0])
        self.assertIn('requests=8',logs.output[0])
        self.assertIn('network_seconds=0.320',logs.output[0])
        self.assertIn('page=4',logs.output[1])
        self.assertIn('requests=0',logs.output[1])
        self.assertNotIn('outside_scope',' '.join(logs.output))
