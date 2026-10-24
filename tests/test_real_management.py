"""SDK doubles test the real integration boundary without a cloud connection."""
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from openstack_cloud_poc.real_openstack import (
    RealLifecycleManagementService, RealOpenStackAdapter, RealProviderError,
)


class RealManagementTests(unittest.TestCase):
    def setUp(self):
        self.project, self.user, self.image, self.flavor, self.network = [uuid4().hex for _ in range(5)]
        self.run = uuid4().hex
        self.server_id = str(uuid4())
        self.server = NS(id=self.server_id, project_id=self.project, status='ACTIVE',
                         task_state=None, power_state=1, metadata={})
        self.access = NS(project_id=self.project, user_id=self.user, role_names=['member', 'reader'],
                         project_scoped=True, system_scoped=False)
        self.cloud = NS(session=NS(auth=NS(invalidate=Mock(), get_access=Mock(return_value=self.access))),
            image=NS(get_image=Mock(return_value=NS(status='active')), images=Mock(return_value=[])),
            network=NS(get_network=Mock(return_value=NS(project_id=self.project, is_shared=False,
                is_router_external=False)), networks=Mock(return_value=[])),
            compute=NS(get_flavor=Mock(), flavors=Mock(return_value=[]),
                get_server=Mock(side_effect=lambda _: self.server), create_server=Mock(),
                stop_server=Mock(), start_server=Mock(), delete_server=Mock()))
        def create(**attrs):
            self.server.metadata = attrs['metadata']
            return NS(id=self.server_id)
        self.cloud.compute.create_server.side_effect = create
        self.adapter = RealOpenStackAdapter(self.cloud, self.project, self.user)
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'jobs.sqlite'
        self.service = RealLifecycleManagementService(self.adapter, self.path, self.run, timeout=0.05)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def payload(self, operation='compute.create', project=None):
        result = {'operation': operation, 'project_id': project or self.project}
        if operation == 'compute.create':
            result.update(image_id=self.image, flavor_id=self.flavor, network_id=self.network)
        else:
            result['instance_id'] = self.server_id
        return result

    def submit_wait(self, payload=None, key='create-1'):
        accepted = self.service.submit(payload or self.payload(), key)
        self.service._futures[accepted['job_id']].result(timeout=2)
        return accepted, self.service.get_job(accepted['job_id'], self.project)

    def test_create_acceptance_has_job_not_resource_and_observed_result_preserves_uuid(self):
        accepted, job = self.submit_wait()
        self.assertEqual(accepted['status'], 'accepted')
        self.assertNotIn('instance_id', accepted)
        self.assertTrue(accepted['job_id'].startswith('openstack-job-'))
        self.assertEqual(job['instance_id'], self.server_id)
        self.assertEqual(job['state'], 'succeeded')
        self.assertEqual(job['evidence_mode'], 'real')

    def test_authorization_rechecks_scope_and_rejects_admin_or_revoked_role(self):
        for field, value in (('project_id', uuid4().hex), ('role_names', ['admin', 'member']),
                             ('role_names', []), ('system_scoped', True)):
            with patch.object(self.access, field, value), self.assertRaises(RealProviderError):
                self.service.submit(self.payload(), 'deny')
        self.cloud.compute.create_server.assert_not_called()

    def test_cross_project_rejected_before_provider_metadata(self):
        with self.assertRaises(RealProviderError):
            self.adapter.inspect(uuid4().hex, self.server_id)
        self.cloud.compute.get_server.assert_not_called()

    def test_fixture_reference_rejected_without_provider_call(self):
        payload = self.payload()
        payload['image_id'] = 'fixture:image'
        with self.assertRaises(RealProviderError):
            self.service.submit(payload, 'invalid')
        self.cloud.compute.create_server.assert_not_called()

    def test_idempotent_retry_returns_same_job_and_conflicting_payload_is_rejected(self):
        accepted, _ = self.submit_wait()
        self.assertEqual(self.service.submit(self.payload(), 'create-1'), accepted)
        self.cloud.compute.create_server.assert_called_once()
        changed = self.payload()
        changed['flavor_id'] = uuid4().hex
        with self.assertRaises(RealProviderError):
            self.service.submit(changed, 'create-1')

    def test_create_response_loss_is_unverified_and_not_replayed(self):
        self.cloud.compute.create_server.side_effect = TimeoutError('password/token sentinel')
        accepted, job = self.submit_wait()
        self.assertEqual(job['state'], 'unverified')
        self.assertIsNone(job['instance_id'])
        self.assertNotIn('sentinel', str(job))
        self.service.submit(self.payload(), 'create-1')
        self.cloud.compute.create_server.assert_called_once()

    def test_active_pending_task_cannot_fake_completion_and_known_uuid_is_retained(self):
        self.server.task_state = 'spawning'
        with patch('openstack_cloud_poc.real_openstack.time.sleep'):
            _, job = self.submit_wait()
        self.assertEqual(job['state'], 'unverified')
        self.assertEqual(job['instance_id'], self.server_id)

    def test_observed_nova_error_is_failed_with_known_uuid_for_safe_cleanup(self):
        self.server.status = 'ERROR'
        _, job = self.submit_wait()
        self.assertEqual(job['state'], 'failed')
        self.assertEqual(job['instance_id'], self.server_id)
        self.assertEqual(job['failure_code'], 'provider_error')

    def test_worker_checks_authorization_again_before_cloud_call(self):
        with patch.object(self.adapter, 'authorize', side_effect=[None, RealProviderError('authorization_denied'), None]):
            _, job = self.submit_wait()
        self.assertEqual(job['state'], 'failed')
        self.cloud.compute.create_server.assert_not_called()

    def test_foreign_and_unregistered_servers_cannot_be_mutated(self):
        for owner in (self.project, uuid4().hex):
            self.server.project_id = owner
            _, job = self.submit_wait(self.payload('compute.stop'), key='stop-' + owner)
            self.assertEqual(job['state'], 'failed')
        self.cloud.compute.stop_server.assert_not_called()

    def test_owned_stop_start_require_observed_states_and_no_duplicate_action(self):
        self.submit_wait()
        def stop(_):
            self.server.status, self.server.power_state = 'SHUTOFF', 4
        def start(_):
            self.server.status, self.server.power_state = 'ACTIVE', 1
        self.cloud.compute.stop_server.side_effect = stop
        self.cloud.compute.start_server.side_effect = start
        _, stopped = self.submit_wait(self.payload('compute.stop'), 'stop')
        _, started = self.submit_wait(self.payload('compute.start'), 'start')
        self.assertEqual((stopped['state'], started['state']), ('succeeded', 'succeeded'))
        self.cloud.compute.stop_server.assert_called_once()
        self.cloud.compute.start_server.assert_called_once()

    def test_delete_success_requires_actual_404_after_owned_submission(self):
        self.submit_wait()
        class NotFound(Exception):
            status_code = 404
        self.cloud.compute.delete_server.side_effect = lambda *args, **kwargs: setattr(
            self.cloud.compute.get_server, 'side_effect', NotFound())
        _, job = self.submit_wait(self.payload('compute.delete'), 'delete')
        self.assertEqual(job['state'], 'succeeded')
        self.cloud.compute.delete_server.assert_called_once_with(self.server_id, ignore_missing=False)

    def test_delete_service_error_is_not_success(self):
        self.submit_wait()
        self.cloud.compute.delete_server.side_effect = TimeoutError('secret sentinel')
        _, job = self.submit_wait(self.payload('compute.delete'), 'delete')
        self.assertEqual(job['state'], 'unverified')
        self.assertNotIn('sentinel', str(job))

    def test_catalog_preserves_units_provenance_and_omits_unverified_storage(self):
        self.cloud.compute.flavors.return_value = [NS(id=self.flavor, name='tiny', ram=256, vcpus=1, disk=1)]
        catalog = self.adapter.read_catalog(self.project)
        item = catalog.records[0]
        self.assertEqual(item.attributes['memory_mib'], 256)
        self.assertEqual(item.status.value, 'unverified')
        self.assertTrue(item.source.startswith('openstack:'))
        self.assertEqual(len(catalog.records), 1)

    def test_reopen_preserves_completed_job_and_marks_interrupted_jobs_unverified(self):
        accepted, _ = self.submit_wait()
        self.service._update(accepted['job_id'], state='running')
        self.service.close()
        self.service = RealLifecycleManagementService(self.adapter, self.path, self.run)
        job = self.service.get_job(accepted['job_id'], self.project)
        self.assertEqual(job['state'], 'unverified')
        self.assertEqual(job['instance_id'], self.server_id)
        self.service.submit(self.payload(), 'create-1')
        self.cloud.compute.create_server.assert_called_once()

    def test_journal_is_private_and_contains_no_credentials_or_provider_exception_text(self):
        self.submit_wait()
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(self.path) as database:
            data = repr(database.execute('SELECT * FROM jobs').fetchall())
        self.assertNotIn('password', data)
        self.assertNotIn('token', data)


if __name__ == '__main__':
    unittest.main()
