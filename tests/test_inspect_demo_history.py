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


if __name__ == '__main__':
    unittest.main()
