"""Retained guest identity, live-state and durable start-replay boundaries."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('return_to_service', SCRIPTS / 'return_poc_to_service.py')
    service = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(service)
finally:
    sys.path.remove(str(SCRIPTS))


class ReturnToServiceTests(unittest.TestCase):
    def state(self):
        return {'ids': {'server': service.readiness.DEMO_SERVER}, 'project_id': 'owned-project'}

    def test_foreign_server_is_rejected_before_any_start_or_journal(self):
        cloud = NS(compute=Mock())
        with self.assertRaisesRegex(service.attachment.GuardError, 'unexpected_retained_vm'):
            service.submit_start(cloud, {'ids': {'server': 'foreign'}}, NS(status='SHUTOFF'))
        cloud.compute.start_server.assert_not_called()

    def test_already_running_guest_is_never_started_again(self):
        cloud = NS(compute=Mock())
        with patch.object(service.lifecycle, 'settled', return_value=True):
            self.assertFalse(service.submit_start(cloud, self.state(), NS())['start_requested'])
        cloud.compute.start_server.assert_not_called()

    def test_transitional_or_error_guest_is_never_started(self):
        cloud = NS(compute=Mock())
        with patch.object(service.lifecycle, 'settled', return_value=False):
            with self.assertRaisesRegex(service.attachment.GuardError, 'not_settled_shutoff'):
                service.submit_start(cloud, self.state(), NS())
        cloud.compute.start_server.assert_not_called()

    def test_ambiguous_provider_response_leaves_intent_and_cannot_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'intent.json'
            cloud = NS(compute=NS(start_server=Mock(side_effect=TimeoutError)))
            with patch.object(service, 'REPORT', report), \
                 patch.object(service.lifecycle, 'settled', side_effect=lambda server, status, power: status == 'SHUTOFF'):
                with self.assertRaises(TimeoutError):
                    service.submit_start(cloud, self.state(), NS())
                self.assertEqual(json.loads(report.read_text())['phase'], 'vm_start_intent')
                with self.assertRaises(FileExistsError):
                    service.submit_start(cloud, self.state(), NS())
            self.assertEqual(cloud.compute.start_server.call_count, 1)


if __name__ == '__main__':
    unittest.main()
