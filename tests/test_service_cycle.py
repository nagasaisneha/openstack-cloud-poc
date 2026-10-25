"""Scoped service-cycle ordering, durable intent and observed recovery."""
import contextlib
import importlib.util
import io
import fcntl
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('task11_service_cycle', SCRIPTS / 'validate_service_cycle.py')
    setup = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(setup)
finally:
    sys.path.remove(str(SCRIPTS))


class ServiceCycleTests(unittest.TestCase):
    def state(self):
        return {'ids': {'server': setup.readiness.DEMO_SERVER}, 'project_id': 'project',
                'run_id': 'marker', 'namespace': 'ovnmeta-test'}

    def server(self, status='ACTIVE', power=1, task=None, project='project'):
        return NS(id=setup.readiness.DEMO_SERVER, status=status, power_state=power, task_state=task,
                  project_id=project, metadata={'task11_run_id': 'marker'})

    def cloud(self, server=None):
        return NS(compute=NS(get_server=Mock(return_value=server or self.server()),
                             stop_server=Mock(), start_server=Mock()))

    def record(self, phase='baseline'):
        profile = {'LoadState': 'loaded', 'ActiveState': 'active', 'UnitFileState': 'enabled'}
        return {'phase': phase, 'verified': False, 'guest_before': {'boot_id': 'before'},
                'workers_before': {unit: dict(profile) for unit in setup.STOP_UNITS},
                'shared_before': {unit: dict(profile) for unit in setup.SHARED_UNITS}}

    def test_full_cycle_persists_each_intent_and_operates_only_on_dedicated_workers(self):
        cloud = self.cloud()
        record = self.record()
        calls = []
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            path = Path(directory) / 'cycle.json'
            def stop(identifier):
                self.assertEqual(identifier, setup.readiness.DEMO_SERVER)
                self.assertEqual(json.loads(path.read_text())['phase'], 'stop_intent')
                cloud.compute.get_server.return_value = self.server('SHUTOFF', 4)
                calls.append('vm_stop')
            def start(identifier):
                self.assertEqual(identifier, setup.readiness.DEMO_SERVER)
                self.assertEqual(json.loads(path.read_text())['phase'], 'vm_start_intent')
                cloud.compute.get_server.return_value = self.server()
                calls.append('vm_start')
            def command(arguments, **kwargs):
                self.assertEqual(arguments[:3], ['systemctl', '--no-pager', '--no-block'])
                action = arguments[3]
                expected = setup.STOP_UNITS if action == 'stop' else setup.START_UNITS
                self.assertEqual(tuple(arguments[4:]), expected)
                self.assertFalse(set(expected) & set(setup.SHARED_UNITS))
                self.assertEqual(json.loads(path.read_text())['phase'], 'workers_' + action + '_intent')
                calls.append('workers_' + action)
                return ''
            cloud.compute.stop_server.side_effect = stop
            cloud.compute.start_server.side_effect = start
            stack.enter_context(patch.object(setup.common, 'run', side_effect=command))
            stack.enter_context(patch.object(setup.readiness, 'exclusive_demo'))
            stack.enter_context(patch.object(setup.readiness, 'addressing'))
            stack.enter_context(patch.object(setup, 'shared_unchanged', return_value={}))
            stack.enter_context(patch.object(setup, 'wait_workers', return_value={}))
            stack.enter_context(patch.object(setup, 'wait_vm', side_effect=lambda c, s, status, power:
                {'nova_status': status, 'power_state': power, 'libvirt_confirmed': True}))
            stack.enter_context(patch.object(setup, 'wait_controls', return_value={'ready': True}))
            stack.enter_context(patch.object(setup, 'wait_cloud_after_start', return_value={}))
            stack.enter_context(patch.object(setup, 'pinned_guest', return_value={'boot_id': 'after', 'mtu': 1442}))
            stack.enter_context(patch.object(setup.lifecycle, 'verify_acceleration'))
            stack.enter_context(patch.object(setup.guest, 'probe_ping'))
            stack.enter_context(patch.object(setup.operations, 'cpu_smoke', return_value={'verified': True}))
            stack.enter_context(patch.object(setup.operations, 'capacity', return_value={'meminfo_kib': {'MemAvailable': 1024 * 1024}}))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            setup.execute(cloud, self.state(), record, path)
            self.assertTrue(json.loads(path.read_text())['verified'])
        self.assertEqual(calls, ['vm_stop', 'workers_stop', 'workers_start', 'vm_start'])
        self.assertFalse(record['wsl_restart_tested'])

    def test_stop_uncertainty_keeps_intent_and_retry_does_not_resubmit(self):
        cloud = self.cloud()
        cloud.compute.stop_server.side_effect = RuntimeError('private response')
        record = self.record()
        with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'shared_unchanged'), \
             patch.object(setup, 'wait_workers'), patch.object(setup.readiness, 'exclusive_demo'), \
             contextlib.redirect_stdout(io.StringIO()):
            path = Path(directory) / 'cycle.json'
            with self.assertRaises(RuntimeError):
                setup.execute(cloud, self.state(), record, path)
            self.assertEqual(json.loads(path.read_text())['phase'], 'stop_intent')
            cloud.compute.stop_server.reset_mock()
            with patch.object(setup, 'wait_vm', side_effect=RuntimeError('still running')), \
                 patch.object(setup.common, 'run') as command:
                with self.assertRaises(RuntimeError):
                    setup.execute(cloud, self.state(), record, path)
                cloud.compute.stop_server.assert_not_called()
                cloud.compute.start_server.assert_not_called()
                command.assert_not_called()

    def test_partial_worker_stop_retains_phase_without_start_or_rollback(self):
        cloud = self.cloud(self.server('SHUTOFF', 4))
        record = self.record('stopped')
        with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'shared_unchanged'), \
             patch.object(setup.readiness, 'exclusive_demo'), \
             patch.object(setup.common, 'run', side_effect=RuntimeError('partial stop')) as command, \
             contextlib.redirect_stdout(io.StringIO()):
            path = Path(directory) / 'cycle.json'
            with self.assertRaises(RuntimeError):
                setup.execute(cloud, self.state(), record, path)
            self.assertEqual(json.loads(path.read_text())['phase'], 'workers_stop_intent')
            self.assertEqual(command.call_count, 1)
            self.assertIn('stop', command.call_args.args[0])
            cloud.compute.start_server.assert_not_called()
            command.reset_mock()
            with patch.object(setup, 'wait_workers', side_effect=RuntimeError('target unverified')):
                with self.assertRaises(RuntimeError):
                    setup.execute(cloud, self.state(), record, path)
            command.assert_not_called()

    def test_start_uncertainty_is_observed_without_another_start(self):
        cloud = self.cloud(self.server('SHUTOFF', 4))
        cloud.compute.start_server.side_effect = RuntimeError('uncertain start')
        record = self.record('workers_started')
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.readiness, 'exclusive_demo'), \
             patch.object(setup.operations, 'capacity', return_value={'meminfo_kib': {'MemAvailable': 1024 * 1024}}), \
             contextlib.redirect_stdout(io.StringIO()):
            path = Path(directory) / 'cycle.json'
            with self.assertRaises(RuntimeError):
                setup.execute(cloud, self.state(), record, path)
            self.assertEqual(json.loads(path.read_text())['phase'], 'vm_start_intent')
            cloud.compute.start_server.reset_mock()
            with patch.object(setup, 'wait_vm', side_effect=RuntimeError('not active')):
                with self.assertRaises(RuntimeError):
                    setup.execute(cloud, self.state(), record, path)
            cloud.compute.start_server.assert_not_called()

    def test_shared_service_or_enablement_drift_is_a_hard_failure(self):
        record = self.record()
        changed = {unit: dict(value) for unit, value in record['shared_before'].items()}
        changed['mariadb.service']['ActiveState'] = 'inactive'
        with patch.object(setup, 'profiles', return_value=changed):
            with self.assertRaises(setup.readiness.ReadinessError):
                setup.shared_unchanged(record)
        workers = {unit: dict(value) for unit, value in record['workers_before'].items()}
        workers['nova-api.service']['UnitFileState'] = 'disabled'
        with patch.object(setup, 'profiles', return_value=workers):
            with self.assertRaises(setup.readiness.ReadinessError):
                setup.wait_workers('active', record['workers_before'])

    def test_no_cycle_completion_with_unchanged_guest_boot_id(self):
        record = self.record('vm_active')
        with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'wait_cloud_after_start', return_value={}), \
             patch.object(setup, 'pinned_guest', return_value={'boot_id': 'before', 'mtu': 1442}), \
             patch.object(setup.guest, 'probe_ping') as ping:
            with self.assertRaises(setup.readiness.ReadinessError):
                setup.execute(self.cloud(), self.state(), record, Path(directory) / 'cycle.json')
            self.assertFalse(record['verified'])
            self.assertEqual(record['phase'], 'vm_active')
            ping.assert_not_called()

    def test_nova_acceptance_without_libvirt_state_never_passes(self):
        cloud = self.cloud(self.server('SHUTOFF', 4))
        with patch.object(setup.common, 'run', return_value=''), \
             patch.object(setup.time, 'monotonic', side_effect=[0, 1, 181]), patch.object(setup.time, 'sleep'):
            with self.assertRaises(setup.readiness.ReadinessError):
                setup.wait_vm(cloud, self.state(), 'SHUTOFF', 4)

    def test_foreign_project_or_pending_vm_state_prevents_operation(self):
        for server in (self.server(project='foreign'), self.server(task='powering-off')):
            cloud = self.cloud(server)
            with self.assertRaises(RuntimeError):
                setup.assert_vm(cloud, self.state(), 'ACTIVE', 1)
            cloud.compute.start_server.assert_not_called()
            cloud.compute.stop_server.assert_not_called()

    def test_guest_command_uses_existing_pinned_key_and_does_not_modify_trust(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.launch, 'DATA_DIR', Path(directory)), \
             patch.object(setup.guest, 'protected_file'), \
             patch.object(setup.guest, 'validate_attachment', return_value=(NS(mtu=1442), NS(mac_address='mac'))), \
             patch.object(setup.guest, 'ssh_command', return_value=['ssh', '-o', 'StrictHostKeyChecking=accept-new']), \
             patch.object(setup.subprocess, 'run', return_value=NS(returncode=0, stdout='probe')) as command, \
             patch.object(setup.guest, 'parse_guest_report', return_value={'boot_id': 'after'}):
            trust = Path(directory) / 'known_hosts'
            trust.write_text('retained host key')
            self.assertEqual(setup.pinned_guest(self.cloud(), self.state()), {'boot_id': 'after'})
            self.assertIn('StrictHostKeyChecking=yes', command.call_args.args[0])
            self.assertNotIn('StrictHostKeyChecking=accept-new', command.call_args.args[0])
            self.assertEqual(trust.read_text(), 'retained host key')

    def test_maintenance_lock_blocks_new_worker_without_changing_journal(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.launch, 'DATA_DIR', Path(directory)), \
             patch.object(setup.guest, 'protected_file'):
            source = Path(directory) / 'application-jobs.sqlite'
            lock = Path(str(source) + '.lock')
            lock.touch(mode=0o600)
            with sqlite3.connect(source) as database:
                database.execute('CREATE TABLE jobs (state TEXT)')
                database.execute("INSERT INTO jobs VALUES ('succeeded')")
            original = source.read_bytes()
            with setup.journal_maintenance_lock(), lock.open() as other:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with lock.open() as other:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(source.read_bytes(), original)

    def test_control_wait_does_not_retry_authorization_or_scope_failures(self):
        denied = RuntimeError('private API denial')
        denied.status_code = 403
        with patch.object(setup.readiness, 'exclusive_demo', side_effect=denied) as observe, \
             patch.object(setup.time, 'sleep') as sleep:
            with self.assertRaises(RuntimeError):
                setup.wait_controls(self.cloud(), self.state())
            observe.assert_called_once()
            sleep.assert_not_called()

    def test_incomplete_verified_checkpoint_cannot_claim_acceptance(self):
        with self.assertRaises(setup.readiness.ReadinessError):
            setup.assert_complete({'phase': 'verified', 'verified': True})
        record = self.record('invalid_phase')
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(setup.readiness.ReadinessError):
                setup.execute(self.cloud(), self.state(), record, Path(directory) / 'cycle.json')

    def test_successful_readiness_never_falls_back_to_an_older_pass(self):
        import os
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.launch, 'DATA_DIR', Path(directory)), \
             patch.object(setup.guest, 'protected_file'):
            root = Path(directory)
            report = {'verified': True, 'service_cycle_prerequisites_passed': True,
                      'checks': {'cloud': {'status': 'passed'}},
                      'cloud': {'retained_server': setup.readiness.DEMO_SERVER, 'project_id': 'project'},
                      'guest_cpu': {'verified': True}}
            older = root / ('restart-readiness-' + 'a' * 32 + '.json')
            older.write_text(json.dumps(report))
            os.utime(older, ns=(1_000_000_000, 1_000_000_000))
            self.assertEqual(setup.successful_readiness(self.state()), older)
            newer = root / ('restart-readiness-' + 'b' * 32 + '.json')
            report['verified'] = False
            newer.write_text(json.dumps(report))
            os.utime(newer, ns=(2_000_000_000, 2_000_000_000))
            with self.assertRaises(setup.readiness.ReadinessError):
                setup.successful_readiness(self.state())

    def test_metadata_namespace_creation_waits_without_changing_host_networks(self):
        with patch.object(setup.common, 'run', side_effect=['', 'ovnmeta-test (id: 0)\n']) as command, \
             patch.object(setup.readiness, 'wait_cloud_state', return_value={'ready': True}) as cloud_check, \
             patch.object(setup.time, 'sleep'):
            self.assertEqual(setup.wait_cloud_after_start(self.cloud(), self.state()), {'ready': True})
            self.assertTrue(all(call.args[0] == ['ip', 'netns', 'list'] for call in command.call_args_list))
            cloud_check.assert_called_once()

    def test_nonroot_and_wrong_uuid_reject_before_any_host_or_cloud_operations(self):
        with patch.object(setup.os, 'geteuid', return_value=1000), \
             patch.object(setup.readiness.recovery, 'source_bundle') as source, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(setup.main(['--apply-demo', setup.readiness.DEMO_SERVER]), 1)
            with self.assertRaises(SystemExit) as error:
                setup.main(['--apply-demo', 'foreign-id'])
            self.assertEqual(error.exception.code, 2)
            source.assert_not_called()

    def test_low_memory_after_worker_recovery_preserves_stopped_vm_without_start_intent(self):
        cloud = self.cloud(self.server('SHUTOFF', 4))
        record = self.record('workers_started')
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.readiness, 'exclusive_demo'), \
             patch.object(setup.operations, 'capacity', return_value={'meminfo_kib': {'MemAvailable': 128 * 1024}}):
            path = Path(directory) / 'cycle.json'
            with self.assertRaises(setup.readiness.ReadinessError):
                setup.execute(cloud, self.state(), record, path)
            cloud.compute.start_server.assert_not_called()
            self.assertFalse(path.exists())
            self.assertEqual(record['phase'], 'workers_started')


if __name__ == '__main__':
    unittest.main()
