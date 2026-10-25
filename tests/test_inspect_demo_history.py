import importlib.util
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace as NS

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('inspect_demo_history', SCRIPTS / 'inspect_demo_history.py')
    inspect_demo = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(inspect_demo)
finally:
    sys.path.remove(str(SCRIPTS))


class InspectDemoHistoryTests(unittest.TestCase):
    def test_report_contains_only_bounded_state_and_action_fields(self):
        server = NS(status='SHUTOFF', power_state=4, task_state=None,
                    updated_at='2026-10-25T12:08:00Z', fault={'message': 'PRIVATE_SENTINEL'})
        result = inspect_demo.report(server, [
            {'action': 'stop', 'start_time': '2026-10-25T11:57:53Z',
             'updated_at': '2026-10-25T11:57:58Z', 'message': 'PRIVATE_SENTINEL',
             'request_id': 'PRIVATE_SENTINEL'},
            {'action': 'bad action PRIVATE_SENTINEL', 'start_time': 'PRIVATE_SENTINEL'},
        ])
        self.assertEqual(result['server']['status'], 'SHUTOFF')
        self.assertEqual(result['server']['power_state'], 4)
        self.assertEqual(result['recent_actions'], [{
            'action': 'stop', 'start_time': '2026-10-25T11:57:53Z',
            'updated_at': '2026-10-25T11:57:58Z'}])
        self.assertNotIn('PRIVATE_SENTINEL', repr(result))

    def test_stop_detail_classifies_actor_and_omits_private_fields(self):
        result = inspect_demo.safe_action_detail({
            'action': 'stop', 'start_time': '2026-10-25T12:13:01Z',
            'updated_at': '2026-10-25T12:13:02Z', 'user_id': 'admin-id',
            'message': 'PRIVATE_SENTINEL', 'request_id': 'PRIVATE_SENTINEL',
            'events': [{
                'event': 'compute_stop_instance', 'result': 'Success',
                'start_time': '2026-10-25T12:13:01Z', 'finish_time': '2026-10-25T12:13:02Z',
                'host': 'PRIVATE_SENTINEL', 'traceback': 'PRIVATE_SENTINEL',
            }],
        }, 'admin-id')
        self.assertEqual(result['actor_class'], 'authenticated_admin')
        self.assertEqual(result['events'][0]['event'], 'compute_stop_instance')
        self.assertEqual(result['events'][0]['result'], 'Success')
        self.assertNotIn('PRIVATE_SENTINEL', repr(result))

    def test_stop_detail_handles_unattributed_and_unknown_outcomes(self):
        result = inspect_demo.safe_action_detail({
            'action': 'stop', 'user_id': None,
            'events': [{'event': 'compute_stop_instance', 'result': 'PRIVATE'}],
        }, 'admin-id')
        self.assertEqual(result['actor_class'], 'system_or_unattributed')
        self.assertIsNone(result['events'][0]['result'])


if __name__ == '__main__':
    unittest.main()
