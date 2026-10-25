"""WSL checkpoint interruption, shutdown scope and observed boot recovery."""
import contextlib
import importlib.util
import io
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
    SPEC = importlib.util.spec_from_file_location('task11_wsl_restart', SCRIPTS / 'manage_wsl_restart.py')
    setup = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(setup)
finally:
    sys.path.remove(str(SCRIPTS))


class WslRestartTests(unittest.TestCase):
    def boot(self, ticks=100, kernel='2f4e0f4a-c38b-465c-abfb-3c69ee2a9906'):
        return {'distro': 'Ubuntu', 'init_start_ticks': ticks, 'kernel_boot_id': kernel, 'pid1_is_systemd': True}

    def record(self, phase='baseline'):
        profile = {'LoadState': 'loaded', 'ActiveState': 'active', 'UnitFileState': 'enabled'}
        return {'phase': phase, 'server_id': setup.readiness.DEMO_SERVER, 'distro': 'Ubuntu',
                'project_id': 'project', 'run_id': 'marker', 'boot_before': self.boot(),
                'guest_before': {'boot_id': 'before'}, 'kvm_before': [{'device': '/dev/kvm'}],
                'units_before': {unit: dict(profile) for unit in setup.operations.UNITS},
                'workers_before': {unit: dict(profile) for unit in setup.cycle.STOP_UNITS},
                'shared_before': {unit: dict(profile) for unit in setup.cycle.SHARED_UNITS}}

    def patch_prepare(self, stack, record):
        stack.enter_context(patch.object(setup.review, 'boot_identity', return_value=self.boot()))
        stack.enter_context(patch.object(setup.cycle, 'shared_unchanged', return_value={}))
        stack.enter_context(patch.object(setup.cycle, 'wait_workers', return_value={}))
        stack.enter_context(patch.object(setup.readiness, 'exclusive_demo'))
        stack.enter_context(patch.object(setup.cycle, 'assert_vm'))
        stack.enter_context(patch.object(setup.cycle, 'wait_vm', return_value={'nova_status': 'SHUTOFF', 'libvirt_confirmed': True}))
        stack.enter_context(patch.object(setup, 'assert_offline'))
        stack.enter_context(patch.object(setup, 'verified_backup', return_value=True))
        stack.enter_context(patch.object(setup.backup, 'checksum', return_value='hash'))
        stack.enter_context(patch.object(setup.os, 'sync'))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def test_preparation_saves_intents_and_preserves_exact_vm_and_shared_services(self):
        record = self.record()
        cloud = NS(compute=NS(stop_server=Mock(), start_server=Mock()))
        calls = []
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            path = Path(directory) / 'checkpoint.json'
            self.patch_prepare(stack, record)
            def stop(identifier):
                self.assertEqual(identifier, setup.readiness.DEMO_SERVER)
                self.assertEqual(json.loads(path.read_text())['phase'], 'stop_intent')
                calls.append('demo_stop')
            def command(arguments, **kwargs):
                self.assertEqual(arguments, ['systemctl', '--no-pager', '--no-block', 'stop', *setup.cycle.STOP_UNITS])
                self.assertFalse(set(arguments[4:]) & set(setup.cycle.SHARED_UNITS))
                self.assertEqual(json.loads(path.read_text())['phase'], 'workers_stop_intent')
                calls.append('workers_stop')
            def snapshot(saved, location):
                self.assertEqual(json.loads(location.read_text())['phase'], 'workers_stopped')
                calls.append('snapshot')
                return '/protected/fresh-bundle'
            cloud.compute.stop_server.side_effect = stop
            stack.enter_context(patch.object(setup.common, 'run', side_effect=command))
            stack.enter_context(patch.object(setup, 'snapshot', side_effect=snapshot))
            setup.prepare(cloud, {}, record, path)
            self.assertEqual(json.loads(path.read_text())['phase'], 'prepared')
            self.assertFalse(record['wsl_restart_verified'])
            self.assertTrue(record['shutdown_prepared'])
        self.assertEqual(calls, ['demo_stop', 'workers_stop', 'snapshot'])
        cloud.compute.start_server.assert_not_called()

    def test_uncertain_vm_stop_is_observed_on_retry_without_replay(self):
        record = self.record()
        cloud = NS(compute=NS(stop_server=Mock(side_effect=TimeoutError()), start_server=Mock()))
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            path = Path(directory) / 'checkpoint.json'
            self.patch_prepare(stack, record)
            command = stack.enter_context(patch.object(setup.common, 'run'))
            snapshot = stack.enter_context(patch.object(setup, 'snapshot'))
            with self.assertRaises(TimeoutError):
                setup.prepare(cloud, {}, record, path)
            self.assertEqual(json.loads(path.read_text())['phase'], 'stop_intent')
            command.assert_not_called()
            snapshot.assert_not_called()
            with patch.object(setup.cycle, 'wait_vm', side_effect=RuntimeError('unobserved')):
                with self.assertRaises(RuntimeError):
                    setup.prepare(cloud, {}, record, path)
            cloud.compute.stop_server.assert_called_once()
            self.assertEqual(record['phase'], 'stop_intent')

    def test_worker_stop_uncertainty_keeps_intent_and_never_submits_another_stop(self):
        record = self.record('stopped')
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            path = Path(directory) / 'checkpoint.json'
            self.patch_prepare(stack, record)
            command = stack.enter_context(patch.object(setup.common, 'run', side_effect=TimeoutError()))
            with self.assertRaises(TimeoutError):
                setup.prepare(Mock(), {}, record, path)
            self.assertEqual(record['phase'], 'workers_stop_intent')
            with patch.object(setup.cycle, 'wait_workers', side_effect=RuntimeError('not stopped')):
                with self.assertRaises(RuntimeError):
                    setup.prepare(Mock(), {}, record, path)
            command.assert_called_once()

    def test_preparation_rejects_changed_distro_boot_before_any_operation(self):
        with patch.object(setup.review, 'boot_identity', return_value=self.boot(200)), \
             patch.object(setup.common, 'run') as run:
            with self.assertRaises(setup.readiness.ReadinessError):
                setup.prepare(Mock(), {}, self.record(), Path('unused'))
            run.assert_not_called()

    def test_offline_gate_rejects_other_domains_and_autostart(self):
        target = setup.readiness.DEMO_SERVER + '\n'
        for domains, stopped, autostart in ((target + 'other\n', target, False),
                                           (target, '', False), (target, target, True)):
            with self.subTest(domains=domains, stopped=stopped, autostart=autostart), \
                 patch.object(setup.common, 'run', side_effect=[domains, stopped]), \
                 patch.object(setup.review, 'autostart', return_value={'demo_autostart_enabled': autostart}):
                with self.assertRaises(setup.readiness.ReadinessError):
                    setup.assert_offline()

    def test_boot_observation_accepts_new_distribution_init_with_same_shared_kernel(self):
        record = self.record('prepared')
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(setup.review, 'boot_identity', return_value=self.boot(200)))
            stack.enter_context(patch.object(setup, 'verified_backup', return_value=True))
            stack.enter_context(patch.object(setup, 'wait_boot_units', return_value={'observed': True}))
            stack.enter_context(patch.object(setup.readiness, 'addressing', return_value={'match': True}))
            stack.enter_context(patch.object(setup.readiness, 'kvm_devices', return_value=record['kvm_before']))
            stack.enter_context(patch.object(setup.review, 'boot_policy', return_value={'wsl_systemd_configured': True}))
            stack.enter_context(patch.object(setup, 'assert_offline'))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            setup.observe_boot(record, Path(directory) / 'checkpoint.json')
        self.assertEqual(record['phase'], 'boot_observed')
        self.assertFalse(record['kernel_restart_observed'])
        self.assertEqual(record['boot_after']['init_start_ticks'], 200)

    def test_no_restart_or_second_restart_does_not_run_recovery(self):
        with patch.object(setup.review, 'boot_identity', return_value=self.boot()), \
             patch.object(setup, 'wait_boot_units') as wait:
            with self.assertRaises(setup.readiness.ReadinessError):
                setup.observe_boot(self.record('prepared'), Path('unused'))
            wait.assert_not_called()
        record = self.record('vm_start_intent')
        record['boot_after'] = self.boot(200)
        with patch.object(setup.review, 'boot_identity', return_value=self.boot(300)):
            with self.assertRaises(setup.readiness.ReadinessError):
                setup.observe_boot(record, Path('unused'))

    def test_boot_address_failure_leaves_guest_stopped_and_phase_prepared(self):
        record = self.record('prepared')
        with patch.object(setup.review, 'boot_identity', return_value=self.boot(200)), \
             patch.object(setup, 'verified_backup'), patch.object(setup, 'wait_boot_units'), \
             patch.object(setup.readiness, 'addressing', side_effect=setup.readiness.ReadinessError('address drift')), \
             patch.object(setup.common, 'run') as command:
            with self.assertRaises(setup.readiness.ReadinessError):
                setup.observe_boot(record, Path('unused'))
            command.assert_not_called()
            self.assertEqual(record['phase'], 'prepared')

    def test_boot_missing_worker_or_enablement_drift_never_gets_manual_start(self):
        record = self.record('prepared')
        missing = json.loads(json.dumps(record['units_before']))
        missing['nova-compute.service']['ActiveState'] = 'inactive'
        with patch.object(setup.cycle, 'profiles', return_value=missing), \
             patch.object(setup.common, 'run') as command:
            with self.assertRaises(setup.readiness.ReadinessError):
                setup.wait_boot_units(record, timeout=0)
            command.assert_not_called()
        missing['nova-compute.service']['UnitFileState'] = 'disabled'
        with patch.object(setup.cycle, 'profiles', return_value=missing):
            with self.assertRaises(setup.readiness.ReadinessError):
                setup.wait_boot_units(record)

    def patch_resume(self, stack, record):
        stack.enter_context(patch.object(setup.cycle, 'wait_controls', return_value={'ready': True}))
        stack.enter_context(patch.object(setup.readiness, 'exclusive_demo'))
        stack.enter_context(patch.object(setup.cycle, 'assert_vm'))
        stack.enter_context(patch.object(setup, 'assert_offline'))
        stack.enter_context(patch.object(setup.readiness, 'addressing'))
        stack.enter_context(patch.object(setup.operations, 'capacity', return_value={'meminfo_kib': {'MemAvailable': 1024 * 1024}}))
        stack.enter_context(patch.object(setup.cycle, 'wait_vm', return_value={'nova_status': 'ACTIVE', 'libvirt_confirmed': True}))
        stack.enter_context(patch.object(setup.cycle, 'wait_cloud_after_start', return_value={}))
        stack.enter_context(patch.object(setup.cycle, 'pinned_guest', return_value={'boot_id': 'after', 'mtu': 1442}))
        stack.enter_context(patch.object(setup.lifecycle, 'verify_acceleration'))
        stack.enter_context(patch.object(setup.guest, 'probe_ping'))
        stack.enter_context(patch.object(setup.operations, 'cpu_smoke', return_value={'verified': True}))
        stack.enter_context(patch.object(setup, 'wait_boot_units', return_value=record['units_before']))
        stack.enter_context(patch.object(setup, 'verified_backup', return_value=True))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def test_post_boot_starts_only_retained_demo_after_observed_service_recovery(self):
        record = self.record('boot_observed')
        record.update(boot_after=self.boot(200), units_at_boot=record['units_before'],
                      stop={'nova_status': 'SHUTOFF', 'libvirt_confirmed': True})
        cloud = NS(compute=NS(start_server=Mock()))
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            self.patch_resume(stack, record)
            command = stack.enter_context(patch.object(setup.common, 'run'))
            path = Path(directory) / 'checkpoint.json'
            def start(identifier):
                self.assertEqual(identifier, setup.readiness.DEMO_SERVER)
                self.assertEqual(json.loads(path.read_text())['phase'], 'vm_start_intent')
            cloud.compute.start_server.side_effect = start
            setup.resume(cloud, {}, record, path)
            command.assert_not_called()
            cloud.compute.start_server.assert_called_once()
        self.assertTrue(record['wsl_restart_verified'])
        setup.assert_complete(record)

    def test_uncertain_start_is_not_replayed_and_same_guest_boot_blocks_pass(self):
        record = self.record('vm_start_intent')
        record['stop'] = {'nova_status': 'SHUTOFF', 'libvirt_confirmed': True}
        cloud = NS(compute=NS(start_server=Mock()))
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            self.patch_resume(stack, record)
            stack.enter_context(patch.object(setup.cycle, 'pinned_guest', return_value={'boot_id': 'before'}))
            with self.assertRaises(setup.readiness.ReadinessError):
                setup.resume(cloud, {}, record, Path(directory) / 'checkpoint.json')
            cloud.compute.start_server.assert_not_called()
            self.assertEqual(record['phase'], 'vm_active')
            self.assertNotEqual(record.get('verified'), True)

    def test_cancellation_recovers_workers_but_never_claims_restart_acceptance(self):
        record = self.record('cancel_intent')
        record['stop'] = {'nova_status': 'SHUTOFF', 'libvirt_confirmed': True}
        cloud = NS(compute=NS(start_server=Mock()))
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            self.patch_resume(stack, record)
            stack.enter_context(patch.object(setup.cycle, 'shared_unchanged'))
            stack.enter_context(patch.object(setup.cycle, 'wait_workers', return_value={}))
            command = stack.enter_context(patch.object(setup.common, 'run'))
            setup.resume(cloud, {}, record, Path(directory) / 'checkpoint.json', cancel=True)
            command.assert_called_once_with(['systemctl', '--no-pager', '--no-block', 'start', *setup.cycle.START_UNITS], timeout=20)
        self.assertFalse(record['wsl_restart_verified'])
        setup.assert_complete(record, cancel=True)
        with self.assertRaises(setup.readiness.ReadinessError):
            setup.assert_complete(record)

    def test_poweroff_is_scoped_graceful_and_saved_before_request(self):
        record = dict(self.record('prepared'), shutdown_prepared=True)
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            self.patch_prepare(stack, record)
            stack.enter_context(patch.object(setup.review, 'boot_policy', return_value={'wsl_systemd_configured': True}))
            path = Path(directory) / 'checkpoint.json'
            def command(arguments, **kwargs):
                self.assertEqual(arguments, ['systemctl', 'poweroff'])
                self.assertEqual(json.loads(path.read_text())['phase'], 'poweroff_intent')
                raise TimeoutError()
            mock = stack.enter_context(patch.object(setup.common, 'run', side_effect=command))
            with self.assertRaises(TimeoutError):
                setup.poweroff(record, path)
            self.assertEqual(record['phase'], 'poweroff_intent')
            with self.assertRaises(setup.readiness.ReadinessError):
                setup.poweroff(record, path)
            mock.assert_called_once()

    def test_boot_policy_rejects_custom_commands_and_nonpersistent_service_policy(self):
        for policy in ({}, {'wsl_systemd_configured': True, 'custom_boot_command_present': True},
                       {'wsl_systemd_configured': True, 'active_units_needing_boot_policy_review': ['nova-api.service']}):
            with self.assertRaises(setup.readiness.ReadinessError):
                setup.boot_policy_ready(policy)

    def test_new_incomplete_or_cross_project_review_blocks_preparation(self):
        state = {'project_id': 'project'}
        valid = {'verified': True, 'checks': {'readiness': {'status': 'passed'}},
                 'boot_identity': self.boot(), 'service_cycle': {'cycle_verified': True},
                 'current_readiness': {'cloud': {'project_id': 'project', 'retained_server': setup.readiness.DEMO_SERVER}}}
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.launch, 'DATA_DIR', Path(directory)), \
             patch.object(setup.guest, 'protected_file'):
            old = Path(directory) / ('wsl-restart-review-' + 'a' * 32 + '.json')
            new = Path(directory) / ('wsl-restart-review-' + 'b' * 32 + '.json')
            old.write_text(json.dumps(valid))
            new.write_text(json.dumps(dict(valid, verified=False)))
            import os
            os.utime(old, ns=(1_000_000_000, 1_000_000_000))
            os.utime(new, ns=(2_000_000_000, 2_000_000_000))
            with self.assertRaises(setup.readiness.ReadinessError):
                setup.latest_review(state, self.boot())
            new.write_text(json.dumps(valid))
            self.assertEqual(setup.latest_review(state, self.boot())['path'], str(new))
            with self.assertRaises(setup.readiness.ReadinessError):
                setup.latest_review({'project_id': 'other'}, self.boot())

    def test_snapshot_failure_retains_stopped_phase_and_new_partial_backup(self):
        record = self.record('workers_stopped')
        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            stack.enter_context(patch.object(setup.launch, 'DATA_DIR', root))
            stack.enter_context(patch.object(setup, 'assert_offline'))
            stack.enter_context(patch.object(setup.cycle, 'wait_workers'))
            stack.enter_context(patch.object(setup.cycle, 'shared_unchanged'))
            stack.enter_context(patch.object(setup.shutil, 'disk_usage', return_value=NS(free=4 * 1024 ** 3)))
            stack.enter_context(patch.object(setup.common, 'run', return_value='<domain/>'))
            stack.enter_context(patch.object(setup.backup, 'disk_from_xml', return_value=(root / 'disk', {})))
            partials = []
            def fail(disk, output):
                output.write_bytes(b'partial')
                partials.append(output)
                raise TimeoutError()
            stack.enter_context(patch.object(setup.backup, 'flatten_disk', side_effect=fail))
            for unused in range(2):
                with self.assertRaises(TimeoutError):
                    setup.snapshot(record, root / 'checkpoint.json')
            self.assertEqual(record['phase'], 'workers_stopped')
            self.assertEqual(len(set(record['backup_attempts'])), 2)
            self.assertTrue(all(path.read_bytes() == b'partial' for path in partials))
            self.assertNotIn('backup', record)

    def test_backup_manifest_itself_is_pinned_and_cross_project_backups_rejected(self):
        record = dict(self.record('prepared'), backup='/protected/backup', backup_record_sha256='original')
        saved = {'project_id': 'project', 'run_id': 'marker', 'role': 'pre_wsl_restart'}
        with patch.object(setup.backup, 'verify_bundle', return_value=saved), \
             patch.object(setup.backup, 'checksum', return_value='changed'):
            with self.assertRaises(setup.readiness.ReadinessError):
                setup.verified_backup(record)
        saved['project_id'] = 'other'
        with patch.object(setup.backup, 'verify_bundle', return_value=saved), \
             patch.object(setup.backup, 'checksum', return_value='original'):
            with self.assertRaises(setup.readiness.ReadinessError):
                setup.verified_backup(record)

    def test_manifest_startup_retries_503_but_not_authentication_failure(self):
        for status in (401, 403, 404, 503):
            error = RuntimeError('private HTTP response')
            error.http_status = status
            with self.subTest(status=status), patch.object(setup.guest, 'load_run', side_effect=[error, ({}, None)]) as load, \
                 patch.object(setup.time, 'sleep'):
                if status == 503:
                    self.assertEqual(setup.wait_manifest(Mock()), ({}, None))
                    self.assertEqual(load.call_count, 2)
                else:
                    with self.assertRaises(RuntimeError):
                        setup.wait_manifest(Mock())
                    self.assertEqual(load.call_count, 1)

    def test_non_root_and_wrong_target_cannot_operate(self):
        with patch.object(setup.os, 'geteuid', return_value=1000), \
             patch.object(setup.common, 'run') as run, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(setup.main(['--distro', 'Ubuntu', '--prepare-demo', setup.readiness.DEMO_SERVER]), 1)
            with self.assertRaises(SystemExit):
                setup.main(['--distro', 'Ubuntu', '--prepare-demo', 'wrong'])
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
