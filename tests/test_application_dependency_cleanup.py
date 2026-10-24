"""Deletion-scope, backup and retry safeguards; no live cloud operations."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

import requests

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('task11_dependency_cleanup', SCRIPTS / 'cleanup_application_dependencies.py')
    setup = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(setup)
finally:
    sys.path.remove(str(SCRIPTS))


def scope():
    run_id = 'b' * 32
    records = {label: {field: str(uuid4()) for field in ('project_id', 'user_id', 'network_id', 'role_id')}
               for label in ('a', 'b')}
    original = {'run_id': run_id, 'project_id': str(uuid4()), 'image_id': str(uuid4()),
                'ids': {'server': str(uuid4()), 'network': str(uuid4())},
                'results': {'tenant_api': {'tenants': records}}}
    prefix = 'task11-app-' + run_id[:12]
    state = {'run_id': run_id, 'image_id': str(uuid4()),
             'ids': {field: str(uuid4()) for field in ('server', 'subnet', 'security_group', 'flavor')}, 'results': {}}
    state['ids']['keypair'] = prefix + '-key'
    plan = []
    def add(kind, identifier, name, project, marker, **extra):
        plan.append(dict(kind=kind, id=identifier, name=name, project_id=project, marker=marker, **extra))
    project = records['a']['project_id']
    marker = 'Task 11 application run ' + run_id
    add('subnet', state['ids']['subnet'], prefix + '-subnet', project, marker)
    for label in ('a', 'b'):
        record = records[label]
        add('network', record['network_id'], 'task11-' + run_id + '-' + label + '-net', record['project_id'], setup.tenants.marker(original))
        add('security_group', str(uuid4()), 'default', record['project_id'], 'Default security group')
    add('security_group', state['ids']['security_group'], prefix + '-sg', project, marker)
    add('image', state['image_id'], prefix + '-image', project, run_id)
    add('flavor', state['ids']['flavor'], prefix + '-tiny', project, run_id)
    add('keypair', state['ids']['keypair'], prefix + '-key', project, run_id, user_id=records['a']['user_id'])
    for label in ('a', 'b'):
        record = records[label]
        name = 'task11-' + run_id + '-' + label
        add('user', record['user_id'], name, record['project_id'], setup.tenants.marker(original), role_id=record['role_id'])
        add('project', record['project_id'], name, record['project_id'], setup.tenants.marker(original))
    return original, state, plan


class DependencyCleanupTests(unittest.TestCase):
    def setUp(self):
        # Other suites explicitly verify that fixture execution imports no
        # cloud clients. Keep actual-SDK contract imports local to this test.
        modules = set(sys.modules)
        def restore_sdk_imports():
            for name in set(sys.modules) - modules:
                if name.split('.')[0] in {'openstack', 'keystoneauth1'}:
                    sys.modules.pop(name, None)
        self.addCleanup(restore_sdk_imports)

    def sdk_admin(self, responses):
        """Run the installed SDK/HTTP status path with a socket-free transport."""
        from keystoneauth1.session import Session
        from keystoneauth1.token_endpoint import Token
        from openstack.compute.v2._proxy import Proxy
        from openstack import exceptions
        self.sdk_exceptions = exceptions
        class Transport(requests.adapters.BaseAdapter):
            def __init__(self):
                self.pending = list(responses)
                self.observed = []

            def send(self, request, **kwargs):
                self.observed.append(request)
                if not self.pending:
                    raise AssertionError('unexpected additional HTTP request')
                status, payload = self.pending.pop(0)
                response = requests.Response()
                response.status_code = status
                response.url = request.url
                response.request = request
                response.headers['Content-Type'] = 'application/json'
                response._content = json.dumps(payload).encode() if payload is not None else b''
                return response

            def close(self):
                pass
        transport = Transport()
        session = requests.Session()
        session.mount('http://', transport)
        session.mount('https://', transport)
        self.addCleanup(session.close)
        auth = Session(auth=Token('http://task11.invalid/v2.1', 'offline-test-token'), session=session)
        compute = Proxy(auth, service_type='compute', endpoint_override='http://task11.invalid/v2.1')
        compute._connection = NS(_global_request_id=None, cache_enabled=False, _api_cache_keys=set())
        return NS(compute=compute), transport

    def test_installed_sdk_keypair_delete_observes_404_without_parsing_error_as_keypair(self):
        item = {'kind': 'keypair', 'id': 'task-key', 'name': 'task-key', 'user_id': 'task-user'}
        public = 'ssh-ed25519 AAAA'
        admin, transport = self.sdk_admin([
            (200, {'keypair': {'name': 'task-key', 'user_id': 'task-user', 'public_key': public}}),
            (204, None), (404, {'itemNotFound': {'message': 'absent', 'code': 404}})])
        setup.delete_item(admin, item, public)
        self.assertEqual([request.method for request in transport.observed], ['GET', 'DELETE', 'GET'])
        for request in transport.observed:
            self.assertTrue(request.url.endswith('/os-keypairs/task-key?user_id=task-user'))
            self.assertEqual(request.headers['X-OpenStack-Nova-API-Version'], '2.10')
        self.assertFalse(transport.pending)

    def test_installed_sdk_already_absent_keypair_does_not_repeat_delete(self):
        admin, transport = self.sdk_admin([(404, {'itemNotFound': {'message': 'absent'}})] * 2)
        setup.delete_item(admin, {'kind': 'keypair', 'id': 'task-key', 'user_id': 'task-user'}, '')
        self.assertEqual([request.method for request in transport.observed], ['GET', 'GET'])

    def test_installed_sdk_error_status_never_becomes_absence_or_deletion(self):
        for status in (401, 403, 409, 500):
            admin, transport = self.sdk_admin([(status, {'error': {'message': 'provider error'}})])
            with self.assertRaises(self.sdk_exceptions.HttpException) as raised:
                setup.get_existing(lambda name: setup.keypair(admin, name, 'task-user'), 'task-key')
            self.assertEqual(raised.exception.status_code, status)
            self.assertEqual([request.method for request in transport.observed], ['GET'])

    def test_success_status_with_malformed_keypair_envelope_blocks_mutation(self):
        for payload in ({'error': {'message': 'wrong envelope'}}, {'keypair': []}, []):
            admin, transport = self.sdk_admin([(200, payload)])
            with self.assertRaisesRegex(RuntimeError, 'response shape'):
                setup.delete_item(admin, {'kind': 'keypair', 'id': 'task-key', 'user_id': 'task-user'}, '')
            self.assertEqual([request.method for request in transport.observed], ['GET'])
        admin, _ = self.sdk_admin([(202, {})])
        with self.assertRaisesRegex(RuntimeError, 'HTTP status'):
            setup.get_existing(lambda name: setup.keypair(admin, name, 'task-user'), 'task-key')

    def test_keypair_list_checks_http_and_envelope_before_interpreting_emptiness(self):
        for payload in ({}, {'keypairs': {}}, {'keypairs': [{'keypair': None}]}):
            admin, _ = self.sdk_admin([(200, payload)])
            with self.assertRaisesRegex(RuntimeError, 'list response shape'):
                setup.keypairs(admin, 'task-user')
        admin, _ = self.sdk_admin([(403, {'forbidden': {'message': 'denied'}})])
        with self.assertRaises(self.sdk_exceptions.ForbiddenException):
            setup.keypairs(admin, 'task-user')
        admin, transport = self.sdk_admin([(200, {'keypairs': []})])
        self.assertEqual(setup.keypairs(admin, 'task-user'), [])
        self.assertTrue(transport.observed[0].url.endswith('/os-keypairs?user_id=task-user'))

    def test_retry_plan_binds_every_target_to_protected_scope(self):
        original, state, plan = scope()
        setup.validate_plan(plan, original, state)
        for field, value in (('id', str(uuid4())), ('project_id', str(uuid4())),
                             ('marker', 'other-run'), ('name', 'other-resource')):
            changed = copy.deepcopy(plan)
            changed[0][field] = value
            with self.assertRaises(RuntimeError):
                setup.validate_plan(changed, original, state)
        for changed in (plan + [plan[0]], plan[1:], plan[-4:] + plan[:-4]):
            with self.assertRaises(RuntimeError):
                setup.validate_plan(changed, original, state)

    def test_protected_demo_ids_and_extra_default_groups_are_rejected(self):
        original, state, plan = scope()
        original['ids']['network'] = plan[1]['id']
        with self.assertRaises(RuntimeError):
            setup.validate_plan(plan, original, state)
        original, state, plan = scope()
        extra = dict(plan[2], id=str(uuid4()))
        with self.assertRaises(RuntimeError):
            setup.validate_plan([extra] + plan, original, state)

    def test_absence_requires_404_not_authentication_or_network_failure(self):
        class Error(Exception):
            def __init__(self, status):
                self.status_code = status
        self.assertIsNone(setup.get_existing(Mock(side_effect=Error(404)), 'id'))
        for error in (Error(401), Error(403), Error(500), TimeoutError()):
            with self.assertRaises(type(error)):
                setup.get_existing(Mock(side_effect=error), 'id')

    def test_metadata_ports_allow_only_exact_system_consumer(self):
        record = {'project_id': 'project-a', 'network_id': 'network-a'}
        port = NS(project_id='project-a', network_id='network-a', device_owner='network:distributed',
                  device_id='ovnmeta-network-a', fixed_ips=[{'subnet_id': 'subnet-a', 'ip_address': '172.30.241.2'}])
        expected = {('subnet-a', '172.30.241.2')}
        setup.validate_metadata_ports([port], record, expected)
        for ports in ([port, port], [NS(**(vars(port) | {'project_id': 'foreign'}))],
                      [NS(**(vars(port) | {'device_owner': 'compute:nova'}))],
                      [NS(**(vars(port) | {'fixed_ips': []}))]):
            with self.assertRaises(RuntimeError):
                setup.validate_metadata_ports(ports, record, expected)
        port.fixed_ips = []
        setup.validate_metadata_ports([port], record, set())

    def test_changed_owner_and_shared_network_prevent_deletion(self):
        original, state, plan = scope()
        item = plan[1]
        resource = NS(id=item['id'], name=item['name'], project_id='foreign',
                      description=item['marker'], is_shared=False, is_router_external=False)
        get, delete = Mock(return_value=resource), Mock()
        with patch.object(setup, 'clients', return_value=(get, delete)), self.assertRaises(RuntimeError):
            setup.delete_item(None, item, '')
        delete.assert_not_called()
        resource.project_id = item['project_id']
        resource.is_shared = True
        with self.assertRaises(RuntimeError):
            setup.assert_resource(resource, item, '')

    def test_keypair_requires_correct_user_and_matching_public_material(self):
        item = {'kind': 'keypair', 'name': 'task-key', 'user_id': 'task-user'}
        resource = {'name': 'task-key', 'user_id': 'task-user', 'public_key': 'ssh-ed25519 AAAA comment'}
        setup.assert_resource(resource, item, 'ssh-ed25519 AAAA other-comment')
        for changed in (dict(resource, user_id='operator'), dict(resource, public_key='ssh-ed25519 BBBB')):
            with self.assertRaises(RuntimeError):
                setup.assert_resource(changed, item, 'ssh-ed25519 AAAA')

    def test_project_consumers_prevent_identity_deletion(self):
        admin = MagicMock()
        admin.network.ports.return_value = [NS(id='unexpected-port')]
        with self.assertRaises(RuntimeError):
            setup.project_empty(admin, 'test-project')
        admin.network.ports.return_value = []
        admin.compute.servers.return_value = [NS(project_id='test-project')]
        with self.assertRaises(RuntimeError):
            setup.project_empty(admin, 'test-project')
        admin.compute.servers.return_value = []
        admin.image.images.return_value = [NS(id='unexpected-image')]
        with self.assertRaises(RuntimeError):
            setup.project_empty(admin, 'test-project')

    def test_user_keypairs_and_project_assignments_prevent_identity_deletion(self):
        original, state, plan = scope()
        admin = MagicMock()
        admin.compute.get.return_value.status_code = 200
        admin.compute.get.return_value.json.return_value = {'keypairs': [{'keypair': {'name': 'unexpected'}}]}
        for item in (plan[-4], plan[-3]):
            resource = NS(id=item['id'], name=item['name'], domain_id='default', description=item['marker'])
            get, delete = Mock(return_value=resource), Mock()
            admin.identity.role_assignments.return_value = [object()]
            with patch.object(setup, 'clients', return_value=(get, delete)), \
                 patch.object(setup, 'project_empty'), self.assertRaises(RuntimeError):
                setup.delete_item(admin, item, '')
            delete.assert_not_called()
        admin.identity.unassign_project_role_from_user.assert_not_called()

    def workflow(self, *, backup_error=None, verify_error=None, done=None, existing_backup=False):
        original, state, plan = scope()
        state['results']['dependency_cleanup'] = dict(plan=plan, done=done or [], verified=False)
        if existing_backup:
            state['results']['dependency_cleanup']['backup_directory'] = '/protected/existing-snapshot'
        admin = Mock()
        with patch.object(setup, 'cleanup_backup', return_value=Path('/protected/snapshot'), side_effect=backup_error), \
             patch.object(setup, 'verify_cleanup_backup', side_effect=verify_error) as verify, \
             patch.object(setup, 'delete_item') as delete, patch.object(setup, 'get_existing', return_value=None), \
             patch.object(setup, 'clients', return_value=(Mock(), Mock())), \
             patch.object(setup, 'verify_networks_gone'), patch.object(setup, 'preserved_demo'), \
             patch.object(setup.launch, 'atomic_state'), patch.object(setup, 'progress'):
            try:
                setup.execute(admin, original, state, Path('/protected/application.json'), {}, '')
            except RuntimeError:
                pass
            return state, plan, delete, verify

    def test_backup_creation_or_integrity_failure_prevents_any_deletion(self):
        for kwargs in ({'backup_error': RuntimeError('dump failed')}, {'verify_error': RuntimeError('hash differs')}):
            state, plan, delete, verify = self.workflow(**kwargs)
            delete.assert_not_called()
            self.assertFalse(state['results']['dependency_cleanup']['verified'])

    def test_partial_retry_skips_observed_deletions_but_verifies_every_absence(self):
        state, plan, delete, verify = self.workflow(done=[0, 1])
        self.assertEqual(delete.call_count, len(plan) - 2)
        self.assertNotIn(plan[0], [call.args[1] for call in delete.call_args_list])
        self.assertTrue(state['results']['dependency_cleanup']['verified'])
        self.assertEqual(verify.call_count, 2)

    def test_retry_after_eight_removals_reuses_verified_backup_and_starts_at_keypair(self):
        state, plan, delete, verify = self.workflow(done=list(range(8)), existing_backup=True,
            backup_error=AssertionError('must retain existing backup without creating another'))
        self.assertEqual(delete.call_count, len(plan) - 8)
        self.assertEqual(delete.call_args_list[0].args[1]['kind'], 'keypair')
        self.assertEqual(verify.call_count, 1)
        self.assertTrue(state['results']['dependency_cleanup']['verified'])
        state, plan, delete, verify = self.workflow(done=list(range(8)), existing_backup=True,
            verify_error=RuntimeError('existing backup damaged'))
        delete.assert_not_called()
        self.assertFalse(state['results']['dependency_cleanup']['verified'])

    def test_already_absent_resource_does_not_repeat_delete(self):
        class NotFound(Exception):
            status_code = 404
        get, delete = Mock(side_effect=NotFound()), Mock()
        with patch.object(setup, 'clients', return_value=(get, delete)):
            setup.delete_item(None, {'kind': 'image', 'id': str(uuid4())}, '')
        delete.assert_not_called()


if __name__ == '__main__':
    unittest.main()
