"""Offline tenant acceptance safety checks; no endpoint contact."""
import importlib.util
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
    SPEC = importlib.util.spec_from_file_location('task11_tenants', SCRIPTS / 'validate_tenants.py')
    setup = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(setup)
finally:
    sys.path.remove(str(SCRIPTS))


class ProviderError(Exception):
    def __init__(self, status):
        self.status_code = status


class TenantAcceptanceTests(unittest.TestCase):
    def test_only_explicit_hiding_status_is_denial_evidence(self):
        for status in (403, 404):
            self.assertEqual(setup.expect_hidden(Mock(side_effect=ProviderError(status))), status)
        for failure in (ProviderError(401), ProviderError(500), TimeoutError(), RuntimeError()):
            with self.assertRaisesRegex(RuntimeError, 'required provider response'):
                setup.expect_hidden(Mock(side_effect=failure))
        with self.assertRaisesRegex(RuntimeError, 'unexpectedly succeeded'):
            setup.expect_hidden(Mock(return_value=None))

    def test_wrong_project_token_accepts_only_auth_denial(self):
        self.assertEqual(setup.expect_hidden(Mock(side_effect=ProviderError(401)), accepted=(401, 403)), 401)
        with self.assertRaises(RuntimeError):
            setup.expect_hidden(Mock(side_effect=ProviderError(404)), accepted=(401, 403))

    def test_resource_recovery_requires_unique_exact_marker_id_and_owner(self):
        item = NS(id='net', name='name', description='marker', project_id='project')
        self.assertIs(setup.select_owned([item], 'net', 'name', 'marker', project_id='project'), item)
        for items, saved, marker, project in (([item, item], None, 'marker', 'project'),
                ([item], 'other', 'marker', 'project'), ([item], None, 'foreign', 'project'),
                ([item], None, 'marker', 'foreign'), ([], 'net', 'marker', 'project')):
            with self.assertRaises(RuntimeError):
                setup.select_owned(items, saved, 'name', marker, project_id=project)
        self.assertIsNone(setup.select_owned([], None, 'name', 'marker'))

    def test_role_assignment_rejects_admin_other_project_and_system_scope(self):
        good = NS(user={'id': 'user'}, role={'id': 'member'}, scope={'project': {'id': 'project'}})
        self.assertTrue(setup.validate_assignments([good], 'user', 'project', 'member'))
        self.assertFalse(setup.validate_assignments([], 'user', 'project', 'member'))
        for assignment in (NS(user=good.user, role={'id': 'admin'}, scope=good.scope),
                NS(user=good.user, role=good.role, scope={'system': {'all': True}}),
                NS(user=good.user, role=good.role, scope={'project': {'id': 'other'}})):
            with self.assertRaises(RuntimeError):
                setup.validate_assignments([good, assignment], 'user', 'project', 'member')

    def test_token_requires_exact_identity_project_and_member_without_admin(self):
        record = {'user_id': 'user', 'project_id': 'project'}
        access = NS(user_id='user', project_id='project', project_scoped=True,
                    system_scoped=False, role_names=['member', 'reader'])
        cloud = NS(session=NS(auth=NS(get_access=Mock(return_value=access))))
        setup.check_token(cloud, record)
        for field, value in (('user_id', 'other'), ('project_id', 'other'),
                             ('role_names', ['admin', 'member']), ('role_names', ['reader']),
                             ('system_scoped', True)):
            with patch.object(access, field, value), self.assertRaises(RuntimeError):
                setup.check_token(cloud, record)

    def test_filtered_lists_must_contain_own_network_and_hide_foreign_assets(self):
        cloud = NS(network=NS(networks=Mock(return_value=[NS(id='own')])),
                   compute=NS(servers=Mock(return_value=[])), image=NS(images=Mock(return_value=[])))
        args = (cloud, {'network_id': 'own'}, {'network_id': 'foreign'},
                {'ids': {'network': 'admin-net', 'server': 'admin-vm'}, 'image_id': 'private'})
        setup.check_hidden_lists(*args)
        for method, value in ((cloud.network.networks, []),
                              (cloud.network.networks, [NS(id='own'), NS(id='foreign')]),
                              (cloud.compute.servers, [NS(id='admin-vm')]),
                              (cloud.image.images, [NS(id='private')])):
            original = method.return_value
            try:
                method.return_value = value
                with self.assertRaises(RuntimeError):
                    setup.check_hidden_lists(*args)
            finally:
                method.return_value = original

    def test_recovered_network_does_not_recreate_or_modify_it(self):
        state = {'run_id': 'run', 'results': {}}
        record = {'name': 'tenant', 'project_id': 'project', 'network_id': 'net'}
        network = NS(id='net', name='tenant-net', description=setup.marker(state),
                     project_id='project', is_shared=False, is_router_external=False)
        admin = NS(network=NS(networks=Mock(return_value=[network])))
        cloud = NS(network=NS(create_network=Mock(), get_network=Mock(return_value=network),
                               networks=Mock(return_value=[network])))
        with tempfile.TemporaryDirectory() as directory:
            setup.ensure_network(admin, cloud, state, Path(directory) / 'state.json', record)
        cloud.network.create_network.assert_not_called()

    def test_create_uses_member_session_and_records_returned_provider_id(self):
        state = {'run_id': 'run', 'results': {}}
        record = {'name': 'tenant', 'project_id': 'project'}
        network = NS(id='real-id', description=setup.marker(state), project_id='project',
                     is_shared=False, is_router_external=False)
        admin = NS(network=NS(networks=Mock(return_value=[])))
        cloud = NS(network=NS(create_network=Mock(return_value=network),
                             get_network=Mock(return_value=network), networks=Mock(return_value=[network])))
        with tempfile.TemporaryDirectory() as directory:
            setup.ensure_network(admin, cloud, state, Path(directory) / 'state.json', record)
        self.assertEqual(record['network_id'], 'real-id')
        cloud.network.create_network.assert_called_once()
        self.assertNotIn('project_id', cloud.network.create_network.call_args.kwargs)

    def test_password_retry_preserves_existing_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'secrets.json'
            values = setup.tenant_passwords(path)
            with patch.object(setup.guest, 'protected_file'):
                self.assertEqual(values, setup.tenant_passwords(path))
            self.assertEqual(json.loads(path.read_text()), values)
            self.assertEqual(set(values), {'a', 'b'})


if __name__ == '__main__':
    unittest.main()
