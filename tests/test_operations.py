from datetime import datetime, timedelta, timezone
import unittest
from openstack_cloud_poc.operations import transition, fresh_status


class OperationsTests(unittest.TestCase):
    def test_failure_debounce_deduplication_resolution_and_reopen(self):
        state = {}
        history = []
        for index, good in enumerate((True, False, True, False, False, False, True, True, True, False, False)):
            state, events = transition(state, {'app': {'healthy': good, 'detail': 'ok' if good else 'unavailable'}}, str(index))
            history.extend(events)
        self.assertEqual([(x['event'], x['at']) for x in history], [('opened', '4'), ('resolved', '7'), ('opened', '10')])
        self.assertTrue(state['app']['active'])
        self.assertEqual(state['app']['opened_at'], '10')

    def test_monitoring_snapshot_must_be_fresh_and_not_future_dated(self):
        now = datetime.now(timezone.utc)
        self.assertTrue(fresh_status({'generated_at': now.isoformat()}))
        self.assertFalse(fresh_status({'generated_at': (now - timedelta(seconds=60)).isoformat()}))
        self.assertFalse(fresh_status({'generated_at': (now + timedelta(seconds=5)).isoformat()}))


if __name__ == '__main__':
    unittest.main()
