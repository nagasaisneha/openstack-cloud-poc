"""Deletion-scope, backup and retry safeguards; no live cloud operations."""
import copy
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, Mock, patch
from uuid import uuid4

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

    def workflow(self, *, backup_error=None, verify_error=None, done=None):
        original, state, plan = scope()
        state['results']['dependency_cleanup'] = dict(plan=plan, done=done or [], verified=False)
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

    def test_already_absent_resource_does_not_repeat_delete(self):
        class NotFound(Exception):
            status_code = 404
        get, delete = Mock(side_effect=NotFound()), Mock()
        with patch.object(setup, 'clients', return_value=(get, delete)):
            setup.delete_item(None, {'kind': 'image', 'id': str(uuid4())}, '')
        delete.assert_not_called()


if __name__ == '__main__':
    unittest.main()
