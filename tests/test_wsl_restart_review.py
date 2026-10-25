"""Protected WSL review evidence and credential-free, read-only inventories."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import Mock, patch
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('task11_wsl_review', SCRIPTS / 'review_wsl_restart.py')
    setup = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(setup)
finally:
    sys.path.remove(str(SCRIPTS))


class WslRestartReviewTests(unittest.TestCase):
    def stat_text(self, comm='systemd', ticks='7788'):
        return '1 (' + comm + ') ' + ' '.join(['S'] + ['0'] * 18 + [ticks] + ['0'] * 30)

    def record(self):
        active = {'LoadState': 'loaded', 'ActiveState': 'active', 'UnitFileState': 'enabled'}
        record = {'phase': 'verified', 'verified': True, 'server_id': setup.readiness.DEMO_SERVER,
                  'project_id': 'project', 'run_id': 'marker',
                  'stop': {'nova_status': 'SHUTOFF', 'libvirt_confirmed': True},
                  'start': {'nova_status': 'ACTIVE', 'libvirt_confirmed': True},
                  'kvm_after': True, 'mtu_ping_after': True, 'guest_cpu_after': {'verified': True},
                  'guest_before': {'boot_id': 'before'}, 'guest_after': {'boot_id': 'after'},
                  'workers_before': {unit: dict(active) for unit in setup.cycle.STOP_UNITS},
                  'workers_paused': {unit: dict(active, ActiveState='inactive') for unit in setup.cycle.STOP_UNITS},
                  'workers_resumed': {unit: dict(active) for unit in setup.cycle.STOP_UNITS}}
        for name in ('shared_before', 'shared_during_pause', 'shared_after'):
            record[name] = {unit: dict(active) for unit in setup.cycle.SHARED_UNITS}
        return record

    def test_init_fingerprint_reads_field_22_even_with_parentheses_in_process_name(self):
        self.assertEqual(setup.init_start_ticks(self.stat_text()), 7788)
        self.assertEqual(setup.init_start_ticks(self.stat_text('systemd (wsl)')), 7788)
        for text in ('short', self.stat_text().replace('1 (', '2 (', 1),
                     self.stat_text(ticks='unknown'), self.stat_text(ticks='0')):
            with self.assertRaises(setup.readiness.ReadinessError):
                setup.init_start_ticks(text)

    def test_boot_identity_keeps_init_time_in_addition_to_shared_kernel_boot_id(self):
        values = {'/proc/sys/kernel/random/boot_id': '2f4e0f4a-c38b-465c-abfb-3c69ee2a9906\n',
                  '/proc/1/stat': self.stat_text(), '/proc/1/comm': 'systemd\n'}
        with patch.dict(os.environ, {'WSL_DISTRO_NAME': 'Ubuntu'}), \
             patch.object(Path, 'read_text', lambda path: values[str(path)]):
            result = setup.boot_identity('Ubuntu')
        self.assertEqual(result['init_start_ticks'], 7788)
        self.assertTrue(result['pid1_is_systemd'])
        with patch.dict(os.environ, {'WSL_DISTRO_NAME': 'different-distro'}):
            with self.assertRaises(setup.readiness.ReadinessError):
                setup.boot_identity('Ubuntu')

    def test_cycle_evidence_requires_worker_pause_and_shared_profile_proof(self):
        state = {'project_id': 'project', 'run_id': 'marker'}
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.launch, 'DATA_DIR', Path(directory)), \
             patch.object(setup.guest, 'protected_file'):
            path = Path(directory) / 'service-cycle.json'
            record = self.record()
            path.write_text(json.dumps(record))
            result = setup.cycle_evidence(state)
            self.assertTrue(result['cycle_verified'])
            self.assertTrue(result['worker_pause_resume_verified'])
            self.assertFalse(result['wsl_restart_verified'])
            record['workers_paused']['nova-api.service']['ActiveState'] = 'active'
            path.write_text(json.dumps(record))
            with self.assertRaises(setup.readiness.ReadinessError):
                setup.cycle_evidence(state)
            record = self.record()
            record['shared_after']['mariadb.service']['ActiveState'] = 'inactive'
            path.write_text(json.dumps(record))
            with self.assertRaises(setup.readiness.ReadinessError):
                setup.cycle_evidence(state)

    def test_boot_policy_does_not_expose_custom_boot_command_or_config_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            conf = Path(directory) / 'wsl.conf'
            conf.write_text('[boot]\nsystemd=true\ncommand=PRIVATE_BOOT_COMMAND_SENTINEL\n')
            def properties(unit):
                return {'ActiveState': 'active', 'UnitFileState': 'enabled-runtime' if unit == 'nova-api.service' else 'enabled'}
            with patch.object(setup, 'WSL_CONF', conf), patch.object(setup.operations, 'systemd_state', side_effect=properties):
                result = setup.boot_policy()
            self.assertTrue(result['wsl_systemd_configured'])
            self.assertTrue(result['custom_boot_command_present'])
            self.assertEqual(result['active_units_needing_boot_policy_review'], ['nova-api.service'])
            self.assertNotIn('PRIVATE_BOOT_COMMAND_SENTINEL', repr(result))

    def test_autostart_is_observed_read_only_and_foreign_domains_are_not_ignored(self):
        with patch.object(setup.common, 'run', return_value='') as run:
            self.assertFalse(setup.autostart()['demo_autostart_enabled'])
            self.assertIn('--readonly', run.call_args.args[0])
            self.assertIn('--autostart', run.call_args.args[0])
        with patch.object(setup.common, 'run', return_value=setup.readiness.DEMO_SERVER + '\n'):
            self.assertTrue(setup.autostart()['demo_autostart_enabled'])
        with patch.object(setup.common, 'run', return_value='foreign-domain\n'):
            with self.assertRaises(setup.readiness.ReadinessError):
                setup.autostart()

    def test_unavailable_inventory_is_unknown_without_sensitive_exception_or_false_zero(self):
        result = setup.optional_inventory(Mock(side_effect=RuntimeError('PRIVATE_DIAGNOSTIC_SENTINEL')))
        self.assertFalse(result['available'])
        self.assertNotIn('running_container_count', result)
        self.assertNotIn('PRIVATE_', repr(result))

    def test_container_inventory_uses_only_explicit_local_socket_and_prints_no_ids(self):
        with patch.object(Path, 'is_socket', return_value=True), patch.object(setup.shutil, 'which', return_value='/usr/bin/docker'), \
             patch.object(setup.common, 'run', return_value='abcdef123456\n123456abcdef\n') as run:
            result = setup.containers()
            self.assertEqual(result, {'running_container_count': 2})
            self.assertEqual(run.call_args.args[0][:3], ['docker', '--host=unix:///var/run/docker.sock', 'ps'])
            self.assertNotIn('abcdef123456', repr(result))

    def test_nonroot_and_wrong_distro_stop_before_credentials_or_cloud_calls(self):
        with patch.object(setup.os, 'geteuid', return_value=1000), \
             patch.object(setup.common, 'connection') as connect, \
             patch.object(setup.readiness.recovery, 'source_bundle') as source, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(setup.main(['--distro', 'Ubuntu']), 1)
            with self.assertRaises(SystemExit) as failure:
                setup.main(['--distro', 'unrelated'])
            self.assertEqual(failure.exception.code, 2)
            connect.assert_not_called()
            source.assert_not_called()


if __name__ == '__main__':
    unittest.main()
