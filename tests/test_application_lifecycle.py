"""Application lifecycle ordering and failure gates using provider doubles."""
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('task11_application_lifecycle', SCRIPTS / 'validate_application_lifecycle.py')
    setup = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(setup)
finally:
    sys.path.remove(str(SCRIPTS))


class ApplicationLifecycleTests(unittest.TestCase):
    def test_start_waits_for_attachment_recovery_before_guest_checks(self):
        with patch.object(setup.application, 'application_attachment', side_effect=[
                RuntimeError('port not ready'), ('network', 'port')]) as attachment, patch.object(setup.time, 'sleep'):
            self.assertEqual(setup.wait_attachment(None, None, {}, {}), ('network', 'port'))
            self.assertEqual(attachment.call_count, 2)

    def test_only_404_counts_as_operator_resource_absence(self):
        class ProviderError(Exception):
            def __init__(self, status):
                self.status_code = status
        self.assertTrue(setup.is_absent(Mock(side_effect=ProviderError(404)), 'vm'))
        self.assertFalse(setup.is_absent(Mock(return_value=object()), 'vm'))
        for error in (ProviderError(401), ProviderError(500), TimeoutError()):
            with self.assertRaises(RuntimeError):
                setup.is_absent(Mock(side_effect=error), 'vm')

    def test_independent_cleanup_requires_empty_placement_and_zero_project_usage(self):
        server_id, port_id = str(uuid4()), str(uuid4())
        class NotFound(Exception):
            status_code = 404
        response = Mock()
        response.json.return_value = {'allocations': {}}
        admin = NS(compute=NS(get_server=Mock(side_effect=NotFound())),
                   network=NS(get_port=Mock(side_effect=NotFound()), ports=Mock(return_value=[])),
                   placement=NS(get=Mock(return_value=response)))
        cloud = NS(compute=NS(get_limits=Mock(return_value=NS(absolute=NS(
            total_instances_used=0, total_cores_used=0, total_ram_used=0)))))
        state = {'ids': {'server': server_id, 'port': port_id}}
        recovery = {'source_disk': '/var/lib/nova/instances/' + server_id + '/disk'}
        with patch.object(setup.common, 'run', return_value=''), patch.object(Path, 'exists', return_value=False), \
             patch.object(Path, 'is_symlink', return_value=False):
            self.assertTrue(setup.verify_deleted(admin, cloud, state, recovery)['placement_allocations_empty'])
            response.json.return_value = {'allocations': None}
            with patch.object(setup.time, 'monotonic', side_effect=[0, 1, 121]), patch.object(setup.time, 'sleep'), self.assertRaises(RuntimeError):
                setup.verify_deleted(admin, cloud, state, recovery)

    def workflow(self, *, backup_error=None, verification_error=None, phase=None):
        record = {'verified': False}
        if phase:
            record.update(phase=phase, backup_directory='/protected/backup', backup_verified=True,
                          stop={'verified': True}, start={'verified': True})
        state = {'run_id': 'b' * 32, 'project_id': 'project-a', 'user_id': 'user-a',
                 'server_name': 'application-vm', 'ids': {'server': str(uuid4())},
                 'results': {'application_lifecycle': record}}
        original = {'run_id': 'b' * 32, 'project_id': 'admin-project', 'ids': {'server': str(uuid4())}}
        preserved = NS(project_id='admin-project', metadata={'task11_run_id': 'b' * 32},
                       status='ACTIVE', task_state=None, power_state=1)
        admin = NS(compute=NS(get_server=Mock(return_value=preserved)))
        service = MagicMock()
        service.__enter__.return_value = service
        recovery = {'source_disk': '/protected/source'}
        observed = []
        with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'progress'), \
             patch.object(setup, 'RealLifecycleManagementService', return_value=service), \
             patch.object(setup, 'RealOpenStackAdapter'), \
             patch.object(setup.application, 'application_attachment', return_value=(NS(mtu=1442), NS(mac_address='mac'))), \
             patch.object(setup.application, 'trust_file', return_value='/protected/trust'), \
             patch.object(setup.guest, 'inspect_guest', side_effect=[{'boot_id': 'before'}, {'boot_id': 'after'}]), \
             patch.object(setup.guest, 'probe_ping'), patch.object(setup.infrastructure, 'verify_acceleration'), \
             patch.object(setup, 'assert_stopped'), \
             patch.object(setup, 'is_absent', return_value=True), \
             patch.object(setup, 'run_job', side_effect=lambda svc, st, path, op, key: observed.append((op, key))), \
             patch.object(setup.backup, 'create_backup', side_effect=backup_error, return_value=(Path('/protected/backup'), recovery)), \
             patch.object(setup.backup, 'verify_bundle', side_effect=verification_error, return_value=recovery), \
             patch.object(setup, 'verify_deleted', return_value={'nova_admin_404': True}):
            path = Path(directory) / 'application.json'
            try:
                setup.execute(admin, None, original, state, path)
            except RuntimeError:
                return state, observed, False
            return state, observed, True

    def test_application_stop_start_backup_then_delete_in_dependency_order(self):
        state, observed, passed = self.workflow()
        self.assertTrue(passed)
        self.assertEqual([operation for operation, key in observed],
                         ['compute.stop', 'compute.start', 'compute.stop', 'compute.delete'])
        self.assertEqual(state['results']['application_lifecycle']['phase'], 'deleted')
        self.assertTrue(state['results']['application_lifecycle']['verified'])

    def test_backup_failure_prevents_any_delete_request_and_preserves_stopped_phase(self):
        state, observed, passed = self.workflow(backup_error=RuntimeError('disk comparison failed'))
        self.assertFalse(passed)
        self.assertNotIn('compute.delete', [operation for operation, key in observed])
        self.assertEqual(state['results']['application_lifecycle']['phase'], 'backup_stopped')
        self.assertFalse(state['results']['application_lifecycle']['verified'])

    def test_tampered_bundle_prevents_delete_even_after_backup_was_created(self):
        state, observed, passed = self.workflow(verification_error=RuntimeError('checksum mismatch'))
        self.assertFalse(passed)
        self.assertNotIn('compute.delete', [operation for operation, key in observed])
        self.assertFalse(state['results']['application_lifecycle']['verified'])

    def test_interrupted_deletion_resumes_existing_delete_job_without_stop_start_replay(self):
        state, observed, passed = self.workflow(phase='delete_intent')
        self.assertTrue(passed)
        self.assertEqual(observed, [('compute.delete', 'task11-application-delete')])


if __name__ == '__main__':
    unittest.main()
