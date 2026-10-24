"""Offline checks that provider request acceptance cannot fake VM completion."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('task11_real_lifecycle', SCRIPTS / 'validate_lifecycle.py')
    setup = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(setup)
finally:
    sys.path.remove(str(SCRIPTS))


class RealLifecycleTests(unittest.TestCase):
    def state(self, record=None):
        return {'run_id': 'run-marker', 'project_id': 'project-id', 'ids': {'server': 'server-id'},
                'results': {'lifecycle': {} if record is None else record}}

    def server(self, status='ACTIVE', power=1, task=None):
        return SimpleNamespace(id='server-id', project_id='project-id',
            metadata={'task11_run_id': 'run-marker'}, status=status, power_state=power, task_state=task)

    def cloud(self, server):
        return SimpleNamespace(compute=SimpleNamespace(get_server=Mock(return_value=server),
            stop_server=Mock(), start_server=Mock(), reboot_server=Mock()))

    def test_settled_requires_status_power_and_no_pending_task(self):
        self.assertTrue(setup.settled(self.server(), 'ACTIVE', 1))
        for server in (self.server(power=4), self.server(task='rebooting'), self.server(status='BUILD')):
            self.assertFalse(setup.settled(server, 'ACTIVE', 1))

    def test_cross_project_instance_is_rejected(self):
        server = self.server()
        server.project_id = 'other-project'
        with self.assertRaisesRegex(RuntimeError, 'another project'):
            setup.owned_server(self.cloud(server), self.state())

    def test_stop_acceptance_is_persisted_but_not_marked_completed_on_timeout(self):
        record = {'phase': 'baseline', 'baseline_boot_id': 'before-id'}
        state = self.state(record)
        cloud = self.cloud(self.server())
        with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'progress'), \
             patch.object(setup, 'wait_server', side_effect=RuntimeError('still running')):
            path = Path(directory) / 'state.json'
            with self.assertRaisesRegex(RuntimeError, 'still running'):
                setup.stop_start(cloud, state, path, record)
            saved = json.loads(path.read_text())['results']['lifecycle']
        cloud.compute.stop_server.assert_called_once_with('server-id')
        self.assertEqual(saved['phase'], 'stop_submitted')
        self.assertNotIn('stop', saved)
        self.assertFalse(saved.get('verified', False))

    def test_provider_submission_failure_keeps_intent_for_reconciliation(self):
        record = {'phase': 'baseline', 'baseline_boot_id': 'before-id'}
        state = self.state(record)
        cloud = self.cloud(self.server())
        cloud.compute.stop_server.side_effect = RuntimeError('submission outcome unknown')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            with self.assertRaises(RuntimeError):
                setup.stop_start(cloud, state, path, record)
            self.assertEqual(json.loads(path.read_text())['results']['lifecycle']['phase'], 'stop_intent')

    def test_observed_shutoff_resumes_without_duplicate_stop_and_requires_libvirt(self):
        record = {'phase': 'stop_intent', 'baseline_boot_id': 'before-id'}
        state = self.state(record)
        cloud = self.cloud(self.server('SHUTOFF', 4))
        with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'wait_server'), \
             patch.object(setup.common, 'run', return_value='unrelated-domain\n'):
            with self.assertRaisesRegex(RuntimeError, 'libvirt does not confirm'):
                setup.stop_start(cloud, state, Path(directory) / 'state.json', record)
        cloud.compute.stop_server.assert_not_called()
        self.assertNotIn('stop', record)

    def test_start_recovery_does_not_replay_and_requires_changed_boot_id(self):
        record = {'phase': 'start_submitted', 'baseline_boot_id': 'same-boot'}
        state = self.state(record)
        cloud = self.cloud(self.server())
        with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'wait_server'), \
             patch.object(setup, 'wait_attachment', return_value=(SimpleNamespace(mtu=1442), SimpleNamespace(mac_address='mac'))), \
             patch.object(setup.guest, 'inspect_guest', return_value={'boot_id': 'same-boot'}):
            with self.assertRaisesRegex(RuntimeError, 'boot ID did not change'):
                setup.stop_start(cloud, state, Path(directory) / 'state.json', record)
        cloud.compute.start_server.assert_not_called()
        self.assertNotIn('start', record)

    def test_reboot_intent_is_observed_without_automatic_replay(self):
        record = {'phase': 'reboot_intent', 'reboot_before_boot_id': 'before-id'}
        state = self.state(record)
        cloud = self.cloud(self.server())
        with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'wait_reboot', side_effect=RuntimeError('no changed boot')):
            with self.assertRaisesRegex(RuntimeError, 'no changed boot'):
                setup.reboot(cloud, state, Path(directory) / 'state.json', record)
        cloud.compute.reboot_server.assert_not_called()
        self.assertNotIn('reboot', record)

    def test_active_with_unchanged_boot_id_is_not_reboot_success(self):
        cloud = self.cloud(self.server())
        with patch.object(setup.time, 'monotonic', side_effect=[0, 1, 181]), \
             patch.object(setup.time, 'sleep'), patch.object(setup, 'progress'), \
             patch.object(setup, 'read_boot_id', return_value='unchanged'):
            with self.assertRaisesRegex(RuntimeError, 'reboot completion is unverified'):
                setup.wait_reboot(cloud, self.state(), 'unchanged')

    def test_complete_phase_cannot_skip_missing_operation_evidence(self):
        record = {'phase': 'verified', 'verified': True, 'stop': {'verified': True}}
        with self.assertRaisesRegex(RuntimeError, 'incomplete'):
            setup.reboot(self.cloud(self.server()), self.state(record), None, record)

    def test_stop_start_records_real_observation_before_advancing_phase(self):
        record = {'phase': 'baseline', 'baseline_boot_id': 'before-id', 'verified': False}
        state = self.state(record)
        server = self.server()
        cloud = self.cloud(server)
        def stop(_):
            server.status, server.power_state = 'SHUTOFF', 4
        def start(_):
            server.status, server.power_state = 'ACTIVE', 1
        def settled(*args):
            self.assertTrue(setup.settled(server, args[2], args[3]))
            return server
        cloud.compute.stop_server.side_effect = stop
        cloud.compute.start_server.side_effect = start
        with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'progress'), \
             patch.object(setup, 'wait_server', side_effect=settled), \
             patch.object(setup.common, 'run', return_value='server-id\n'), \
             patch.object(setup, 'wait_attachment', return_value=(SimpleNamespace(mtu=1442), SimpleNamespace(mac_address='mac'))), \
             patch.object(setup.guest, 'inspect_guest', return_value={'boot_id': 'after-id'}), \
             patch.object(setup.guest, 'record_ping'), patch.object(setup, 'verify_acceleration'):
            path = Path(directory) / 'state.json'
            setup.stop_start(cloud, state, path, record)
            stored = json.loads(path.read_text())['results']['lifecycle']
        self.assertEqual(stored['phase'], 'started')
        self.assertTrue(stored['stop']['libvirt_shutoff'])
        self.assertTrue(stored['start']['verified'])
        self.assertFalse(stored['verified'])
        cloud.compute.stop_server.assert_called_once_with('server-id')
        cloud.compute.start_server.assert_called_once_with('server-id')

    def test_reboot_success_requires_changed_identity_and_recovered_guest_checks(self):
        record = {'phase': 'started', 'verified': False,
                  'stop': {'verified': True}, 'start': {'verified': True}}
        state = self.state(record)
        cloud = self.cloud(self.server())
        with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'progress'), \
             patch.object(setup, 'read_boot_id', return_value='before-id'), \
             patch.object(setup, 'wait_reboot', return_value='after-id'), \
             patch.object(setup, 'wait_attachment', return_value=(SimpleNamespace(mtu=1442), SimpleNamespace(mac_address='mac'))), \
             patch.object(setup.guest, 'inspect_guest', return_value={'boot_id': 'after-id'}) as inspect, \
             patch.object(setup.guest, 'record_ping') as ping, \
             patch.object(setup, 'verify_acceleration') as kvm:
            path = Path(directory) / 'state.json'
            setup.reboot(cloud, state, path, record)
            stored = json.loads(path.read_text())['results']['lifecycle']
        cloud.compute.reboot_server.assert_called_once_with('server-id', reboot_type='SOFT')
        self.assertTrue(stored['verified'])
        self.assertEqual(stored['phase'], 'verified')
        self.assertEqual(stored['reboot']['boot_id'], 'after-id')
        inspect.assert_called_once()
        ping.assert_called_once()
        kvm.assert_called_once()


if __name__ == '__main__':
    unittest.main()
