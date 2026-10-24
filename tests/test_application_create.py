"""Offline safety checks for the operator-run application acceptance stage."""
import importlib.util
import contextlib
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('task11_application_create', SCRIPTS / 'validate_application_create.py')
    setup = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(setup)
finally:
    sys.path.remove(str(SCRIPTS))


class ApplicationCreateSafetyTests(unittest.TestCase):
    def image(self, data=b'verified image bytes', status='active'):
        return NS(id='image-id', owner_id='member-project', visibility='private', name='app-image',
                  disk_format='qcow2', container_format='bare', properties={'poc_run_id': 'run'},
                  status=status, size=len(data), checksum=hashlib.md5(data, usedforsecurity=False).hexdigest(),
                  hash_algo='sha512', hash_value=hashlib.sha512(data).hexdigest(), upload=Mock())

    @contextlib.contextmanager
    def image_context(self, image=None):
        data = b'verified image bytes'
        image = image or self.image(data)
        response = NS(status_code=200, iter_content=Mock(return_value=[data[:4], data[4:]]), close=Mock())
        cloud = NS(image=NS(get_image=Mock(return_value=image), images=Mock(return_value=[]),
                           download_image=Mock(return_value=response), create_image=Mock(), delete_image=Mock()))
        state = {'image_id': 'image-id', 'project_id': 'member-project', 'run_id': 'run', 'results': {}, 'ids': {}}
        with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'SOURCE_SIZE', len(data)), \
             patch.object(setup, 'SOURCE_SHA256', hashlib.sha256(data).hexdigest()), patch.object(setup, 'progress'):
            path = Path(directory) / 'state.json'
            source = Path(directory) / 'source.qcow2'
            source.write_bytes(data)
            with patch.object(setup, 'source_image', return_value=source):
                yield cloud, state, path, source, image, response

    def test_active_image_reused_only_after_secure_hash_and_full_readback(self):
        with self.image_context() as (cloud, state, path, source, image, response):
            result = setup.ensure_application_image(None, cloud, state, path, 'app-image')
            self.assertIs(result, image)
            self.assertEqual(state['image_upload']['phase'], 'verified')
            self.assertTrue(state['image_upload']['readback_verified'])
            image.upload.assert_not_called()
            cloud.image.create_image.assert_not_called()
            cloud.image.delete_image.assert_not_called()
            response.close.assert_called_once()

    def test_legacy_md5_is_not_compared_against_sha256(self):
        with self.image_context() as (cloud, state, path, source, image, response):
            self.assertNotEqual(image.checksum, setup.SOURCE_SHA256)
            setup.verify_image_hashes(image, source)
            image.hash_algo, image.hash_value = 'sha256', setup.SOURCE_SHA256
            setup.verify_image_hashes(image, source)
            image.checksum = None  # Legacy field can be absent; secure verification remains required.
            setup.verify_image_hashes(image, source)

    def test_wrong_md5_or_secure_hash_or_unknown_algorithm_is_rejected(self):
        with self.image_context() as (cloud, state, path, source, image, response):
            for field, value in (('checksum', '0' * 32), ('hash_value', '0' * 128),
                                 ('hash_algo', 'md5'), ('hash_algo', None), ('hash_value', None)):
                with patch.object(image, field, value), self.assertRaises(RuntimeError):
                    setup.verify_image_hashes(image, source)

    def test_unexpected_source_bytes_or_inactive_wrong_size_image_cannot_pass(self):
        with self.image_context() as (cloud, state, path, source, image, response):
            for field, value in (('status', 'queued'), ('size', 1)):
                with patch.object(image, field, value), self.assertRaises(RuntimeError):
                    setup.verify_image_hashes(image, source)
            source.write_bytes(b'corrupt source')
            with self.assertRaisesRegex(RuntimeError, 'source no longer matches'):
                setup.verify_image_hashes(image, source)

    def test_readback_requires_200_full_size_and_matching_bytes_and_always_closes(self):
        for status, chunks in ((500, [b'verified image bytes']), (200, [b'short']),
                               (200, [b'x' * 20]), (200, [b'verified image bytes', b'extra'])):
            with self.image_context() as (cloud, state, path, source, image, response):
                response.status_code, response.iter_content.return_value = status, chunks
                with self.assertRaises(RuntimeError):
                    setup.verify_image_readback(cloud, image)
                response.close.assert_called_once()

    def test_metadata_creation_and_upload_are_separate_without_sdk_delete_handler(self):
        image = self.image(status='queued')
        class NotFound(Exception):
            status_code = 404
        with self.image_context(image) as (cloud, state, path, source, image, response), \
             patch.object(setup, 'register_image_metadata', return_value=image) as register:
            cloud.image.get_image.side_effect = [NotFound(), image, image]
            def upload(*args, **kwargs):
                self.assertEqual(kwargs['data'].read(), b'verified image bytes')
                image.status = 'active'
                return NS(status_code=204)
            image.upload.side_effect = upload
            setup.ensure_application_image(None, cloud, state, path, 'app-image')
            register.assert_called_once_with(cloud, state, 'app-image')
            self.assertEqual(json.loads(path.read_text())['image_upload']['phase'], 'verified')
            cloud.image.create_image.assert_not_called()
            cloud.image.delete_image.assert_not_called()

    def test_explicit_registration_id_conflict_preserves_attempt_and_uses_one_fresh_uuid(self):
        class NotFound(Exception):
            status_code = 404
        class Conflict(Exception):
            status_code = 409
        with self.image_context(self.image(status='queued')) as (cloud, state, path, source, image, response):
            cloud.image.get_image.side_effect = [NotFound(), image, image]
            attempts = []
            def register(client, current, name):
                attempts.append(current['image_id'])
                if len(attempts) == 1:
                    raise Conflict()
                image.id = current['image_id']
                return image
            def upload(*args, **kwargs):
                image.status = 'active'
                return NS(status_code=204)
            image.upload.side_effect = upload
            with patch.object(setup, 'register_image_metadata', side_effect=register):
                setup.ensure_application_image(None, cloud, state, path, 'app-image')
            self.assertEqual(len(attempts), 2)
            self.assertNotEqual(attempts[0], attempts[1])
            self.assertEqual(state['previous_image_attempts'], [{'image_id': 'image-id', 'lookup_http': 404, 'registration_http': 409}])
            self.assertEqual(state['image_upload']['phase'], 'verified')
            cloud.image.delete_image.assert_not_called()

    def test_registration_timeout_or_server_error_does_not_allocate_or_retry_an_id(self):
        class NotFound(Exception):
            status_code = 404
        class ServerError(Exception):
            status_code = 500
        for failure in (TimeoutError(), ServerError()):
            with self.image_context() as (cloud, state, path, source, image, response), \
                 patch.object(setup, 'register_image_metadata', side_effect=failure) as register:
                cloud.image.get_image.side_effect = NotFound()
                with self.assertRaises(type(failure)):
                    setup.ensure_application_image(None, cloud, state, path, 'app-image')
                register.assert_called_once()
                self.assertEqual(state['image_id'], 'image-id')
                self.assertEqual(state['image_upload']['phase'], 'register_intent')
                cloud.image.delete_image.assert_not_called()

    def test_missing_image_already_used_by_application_job_is_not_recreated(self):
        class NotFound(Exception):
            status_code = 404
        with self.image_context() as (cloud, state, path, source, image, response), \
             patch.object(setup, 'register_image_metadata') as register:
            cloud.image.get_image.side_effect = NotFound()
            state['create_job_id'] = 'existing-job'
            with self.assertRaisesRegex(RuntimeError, 'unresolved'):
                setup.ensure_application_image(None, cloud, state, path, 'app-image')
            register.assert_not_called()

    def test_readback_deadline_is_bounded_and_closes_response(self):
        with self.image_context() as (cloud, state, path, source, image, response), \
             patch.object(setup.time, 'monotonic', side_effect=[0, 61]):
            with self.assertRaisesRegex(RuntimeError, 'bounded deadline'):
                setup.verify_image_readback(cloud, image)
            response.close.assert_called_once()

    def test_upload_error_preserves_queued_image_and_uncertain_intent(self):
        image = self.image(status='queued')
        with self.image_context(image) as (cloud, state, path, source, image, response):
            state['image_upload'] = {'phase': 'registered'}
            image.upload.side_effect = TimeoutError('response lost')
            with self.assertRaises(TimeoutError):
                setup.ensure_application_image(None, cloud, state, path, 'app-image')
            self.assertEqual(json.loads(path.read_text())['image_upload']['phase'], 'upload_intent')
            cloud.image.delete_image.assert_not_called()

    def test_queued_image_with_unknown_or_submitted_upload_is_never_replayed(self):
        for phase in (None, 'upload_intent', 'submitted'):
            with self.image_context(self.image(status='queued')) as (cloud, state, path, source, image, response):
                if phase:
                    state['image_upload'] = {'phase': phase}
                with self.assertRaisesRegex(RuntimeError, 'uncertain prior upload'):
                    setup.ensure_application_image(None, cloud, state, path, 'app-image')
                image.upload.assert_not_called()
                cloud.image.delete_image.assert_not_called()

    def test_missing_recorded_direct_upload_is_not_automatically_replaced(self):
        class NotFound(Exception):
            status_code = 404
        with self.image_context() as (cloud, state, path, source, image, response), \
             patch.object(setup, 'register_image_metadata') as register:
            cloud.image.get_image.side_effect = NotFound()
            state['image_upload'] = {'phase': 'upload_intent'}
            with self.assertRaisesRegex(RuntimeError, 'unresolved'):
                setup.ensure_application_image(None, cloud, state, path, 'app-image')
            register.assert_not_called()

    def test_foreign_image_identity_is_not_uploaded_or_modified(self):
        for field, value in (('owner_id', 'foreign'), ('name', 'other'), ('id', 'different'),
                             ('properties', {'poc_run_id': 'another-run'})):
            with self.image_context() as (cloud, state, path, source, image, response):
                setattr(image, field, value)
                with self.assertRaisesRegex(RuntimeError, 'identity/ownership conflict'):
                    setup.ensure_application_image(None, cloud, state, path, 'app-image')
                image.upload.assert_not_called()
                cloud.image.delete_image.assert_not_called()

    def test_hash_failure_clears_current_verification_but_preserves_image(self):
        with self.image_context() as (cloud, state, path, source, image, response):
            state['image_upload'] = {'phase': 'verified', 'readback_verified': True}
            image.hash_value = 'bad'
            with self.assertRaises(RuntimeError):
                setup.ensure_application_image(None, cloud, state, path, 'app-image')
            saved = json.loads(path.read_text())['image_upload']
            self.assertEqual(saved['phase'], 'verifying')
            self.assertNotIn('readback_verified', saved)
            cloud.image.delete_image.assert_not_called()

    def inventory(self, ports, subnets=()):
        network = NS(id='ac0d6dd9-ccae-4a7a-9754-713a2a3aa466')
        admin = NS(network=NS(ports=Mock(return_value=ports), subnets=Mock(return_value=subnets)))
        state = {'project_id': 'member-project'}
        return admin, network, state

    def metadata_port(self):
        return NS(id=str(uuid4()), network_id='ac0d6dd9-ccae-4a7a-9754-713a2a3aa466',
                  project_id='member-project', device_owner='network:distributed',
                  device_id='ovnmeta-ac0d6dd9-ccae-4a7a-9754-713a2a3aa466', fixed_ips=[])

    def test_empty_ovn_metadata_port_is_preserved_and_inventory_persisted(self):
        port = self.metadata_port()
        admin, network, state = self.inventory([port])
        with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'progress'):
            setup.pre_subnet_inventory(admin, network, state, Path(directory) / 'state.json')
        self.assertEqual(state['pre_subnet_inventory']['ports'][0]['port_id'], port.id)
        self.assertEqual(port.fixed_ips, [])
        admin.network.ports.assert_called_once_with(network_id=network.id)
        admin.network.subnets.assert_called_once_with(network_id=network.id)

    def test_network_without_ports_remains_allowed(self):
        admin, network, state = self.inventory([])
        with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'progress'):
            setup.pre_subnet_inventory(admin, network, state, Path(directory) / 'state.json')
        self.assertEqual(state['pre_subnet_inventory'], {'ports': [], 'subnet_ids': []})

    def test_workload_router_and_unbound_ports_are_not_metadata_exemptions(self):
        for owner in ('compute:nova', 'network:router_interface', ''):
            port = self.metadata_port()
            port.device_owner = owner
            admin, network, state = self.inventory([port])
            with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'progress'), self.assertRaises(RuntimeError):
                setup.pre_subnet_inventory(admin, network, state, Path(directory) / 'state.json')
            self.assertNotIn('pre_subnet_inventory', state)

    def test_wrong_project_network_or_metadata_device_is_rejected(self):
        for field in ('project_id', 'network_id', 'device_id', 'id'):
            port = self.metadata_port()
            setattr(port, field, 'unexpected-sensitive-sentinel')
            admin, network, state = self.inventory([port])
            with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'progress') as output, self.assertRaises(RuntimeError):
                setup.pre_subnet_inventory(admin, network, state, Path(directory) / 'state.json')
            self.assertNotIn('sensitive-sentinel', str(output.call_args_list))

    def test_allocated_or_unknown_metadata_ips_cannot_be_ignored(self):
        for addresses in ([{'subnet_id': str(uuid4()), 'ip_address': '172.30.241.2'}], None):
            port = self.metadata_port()
            port.fixed_ips = addresses
            admin, network, state = self.inventory([port])
            with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'progress'), self.assertRaises(RuntimeError):
                setup.pre_subnet_inventory(admin, network, state, Path(directory) / 'state.json')

    def test_duplicate_metadata_or_any_existing_subnet_still_blocks(self):
        for ports, subnets in (([self.metadata_port(), self.metadata_port()], []),
                               ([self.metadata_port()], [NS(id=str(uuid4()))])):
            admin, network, state = self.inventory(ports, subnets)
            with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'progress'), self.assertRaises(RuntimeError):
                setup.pre_subnet_inventory(admin, network, state, Path(directory) / 'state.json')

    def test_only_real_404_allows_new_image_creation(self):
        class ProviderError(Exception):
            def __init__(self, status):
                self.status_code = status
        self.assertIsNone(setup.missing_only(Mock(side_effect=ProviderError(404)), 'id'))
        for failure in (ProviderError(403), ProviderError(500), TimeoutError()):
            with self.assertRaises(type(failure)):
                setup.missing_only(Mock(side_effect=failure), 'id')

    def test_incomplete_source_file_is_preserved_and_not_downloaded_again(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.launch, 'DATA_DIR', Path(directory)), \
             patch.object(setup.guest, 'protected_file'):
            path = Path(directory) / 'application-cirros-source.qcow2'
            path.write_bytes(b'incomplete')
            admin = NS(image=NS(get_image=Mock(), download_image=Mock()))
            with self.assertRaisesRegex(RuntimeError, 'failed integrity'):
                setup.source_image(admin)
            self.assertEqual(path.read_bytes(), b'incomplete')
            admin.image.download_image.assert_not_called()

    def rule(self, protocol='tcp', source='172.30.241.2/32'):
        return NS(security_group_id='group', direction='ingress', ether_type='IPv4',
                  protocol=protocol, remote_ip_prefix=source, port_range_min=22 if protocol == 'tcp' else None,
                  port_range_max=22 if protocol == 'tcp' else None, description='marker',
                  remote_group_id=None, remote_address_group_id=None)

    def test_unexpected_broad_rule_is_rejected_before_any_rule_change(self):
        cloud = NS(network=NS(security_group_rules=Mock(return_value=[self.rule(source='0.0.0.0/0')])))
        with patch.object(setup.launch, 'allow_metadata_probe') as change, self.assertRaisesRegex(RuntimeError, 'policy drift'):
            setup.ensure_probe_rules(cloud, NS(id='group'), '172.30.241.2', 'marker')
        change.assert_not_called()

    def test_duplicate_rule_is_not_silently_accepted(self):
        cloud = NS(network=NS(security_group_rules=Mock(return_value=[self.rule(), self.rule()])))
        with patch.object(setup.launch, 'allow_metadata_probe') as change, self.assertRaises(RuntimeError):
            setup.ensure_probe_rules(cloud, NS(id='group'), '172.30.241.2', 'marker')
        change.assert_not_called()

    def test_two_exact_probe_rules_are_observed(self):
        rules = [self.rule(), self.rule('icmp')]
        cloud = NS(network=NS(security_group_rules=Mock(return_value=rules)))
        with patch.object(setup.launch, 'allow_metadata_probe'):
            setup.ensure_probe_rules(cloud, NS(id='group'), '172.30.241.2', 'marker')

    def test_missing_rule_after_creation_is_not_success(self):
        cloud = NS(network=NS(security_group_rules=Mock(return_value=[])))
        with patch.object(setup.launch, 'allow_metadata_probe'), self.assertRaisesRegex(RuntimeError, 'not observed'):
            setup.ensure_probe_rules(cloud, NS(id='group'), '172.30.241.2', 'marker')


if __name__ == '__main__':
    unittest.main()
