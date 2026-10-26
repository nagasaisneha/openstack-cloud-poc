"""Maintenance guards only; no service, guest, or WSL operations."""
import importlib.util
import contextlib
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('cold_recovery', SCRIPTS / 'prepare_cold_recovery.py')
    cold = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(cold)
finally:
    sys.path.remove(str(SCRIPTS))


class ColdRecoveryGuardTests(unittest.TestCase):
    def test_unreviewed_repository_cannot_prepare_maintenance(self):
        with patch.object(cold.common, 'run', side_effect=['a' * 40, ' M changed.py\n']):
            with self.assertRaisesRegex(RuntimeError, 'clean_reviewed_repository'):
                cold.repository_checkpoint()

    def test_prepare_without_explicit_maintenance_confirmation_never_loads_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(cold.os, 'geteuid', return_value=0), patch.object(cold, 'source_guard'), \
                 patch.object(cold, 'protected'), patch.object(cold, 'DATA', root), \
                 patch.object(cold, 'LOCK', root / 'lock'), patch.object(cold.logging, 'disable'), \
                 patch.object(sys, 'argv', ['cold', '--prepare']), \
                 patch.object(cold.common, 'read_secrets') as auth, patch.object(cold.common, 'run') as run:
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cold.main(), 2)
                auth.assert_not_called()
                run.assert_not_called()

    def test_other_volumes_block_before_any_stop_or_dump(self):
        cloud = NS(block_storage=NS(volumes=Mock(return_value=[NS(id='unrelated-volume')])), compute=Mock())
        with patch.object(cold.readiness, 'exclusive_demo'), patch.object(cold.readiness, 'journal_quiescent'), \
             patch.object(cold.readiness, 'owned_demo', return_value=NS(status='ACTIVE')), \
             patch.object(cold.lifecycle, 'settled', return_value=True), patch.object(cold.common, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'volumes'):
                cold.plan(cloud, {})
            cloud.compute.stop_server.assert_not_called()
            run.assert_not_called()

    def test_unapproved_service_names_cannot_reach_systemctl(self):
        with patch.object(cold.common, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'service_scope'):
                cold.stop_units(('ssh.service',), {}, Path('/unused'))
            run.assert_not_called()

    def test_resume_with_foreign_checkpoint_cannot_start_services(self):
        with patch.object(cold.common, 'run') as run:
            with self.assertRaises(RuntimeError):
                cold.resume(Path('/unused'), {'purpose': 'different_task'})
            run.assert_not_called()

    def test_saved_vm_start_intent_is_not_replayed(self):
        record = {'purpose': 'cold_recovery_maintenance', 'schema': 1, 'server_id': cold.readiness.DEMO_SERVER,
                  'units_before': {unit: {'ActiveState': 'inactive'} for unit in cold.UNITS},
                  'project_id': 'p', 'run_id': 'r', 'initial_guest_status': 'ACTIVE', 'vm_start_intent': True}
        cloud = NS(compute=Mock())
        connection = Mock()
        connection.__enter__ = Mock(return_value=cloud)
        connection.__exit__ = Mock(return_value=None)
        with patch.object(cold.common, 'connection', return_value=connection), \
             patch.object(cold.common, 'read_secrets', return_value={}), \
             patch.object(cold.guest, 'load_run', return_value=({'project_id': 'p', 'run_id': 'r'}, None)), \
             patch.object(cold.cycle, 'wait_controls'), patch.object(cold.readiness, 'owned_demo', return_value=NS(status='SHUTOFF')), \
             patch.object(cold.lifecycle, 'settled', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'not_replay'):
                cold.resume(Path('/unused'), record)
            cloud.compute.start_server.assert_not_called()

    def test_cold_source_guard_refuses_a_wrong_distribution_name(self):
        with patch.dict(cold.os.environ, {'WSL_DISTRO_NAME': 'PocRecovery-owned-test'}):
            with self.assertRaisesRegex(RuntimeError, 'existing_ubuntu'):
                cold.source_guard()
