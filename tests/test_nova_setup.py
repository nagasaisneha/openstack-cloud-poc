"""Offline safety checks for the privileged Nova setup script; no cloud access."""
import configparser
import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/local-openstack/configure_nova.py'
SPEC = importlib.util.spec_from_file_location('task11_configure_nova', SCRIPT)
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


class NovaSetupTests(unittest.TestCase):
    def test_ini_preserves_unrelated_options_and_comments(self):
        original = '# keep\n[DEFAULT]\n# log_dir example\nlog_dir=/old\nuntouched = yes\n[database]\nconnection=sqlite:////example.db\n'
        changed = setup.edit_ini(original, {'DEFAULT': {'log_dir': None, 'osapi_compute_workers': '1'}, 'database': {'connection': 'mysql+pymysql://example'}})
        self.assertIn('# keep\n', changed)
        self.assertIn('# log_dir example\n', changed)
        self.assertIn('untouched = yes\n', changed)
        self.assertNotIn('log_dir=/old', changed)
        self.assertIn('connection = mysql+pymysql://example', changed)

    def test_ini_repeat_is_stable_and_missing_sections_are_added(self):
        updates = {'DEFAULT': {'workers': '1'}, 'new_section': {'credential': 'test-value'}}
        first = setup.edit_ini('[DEFAULT]\nworkers=8', updates)
        self.assertEqual(first, setup.edit_ini(first, updates))

    def test_ini_rejects_ambiguous_duplicate_option(self):
        with self.assertRaises(configparser.DuplicateOptionError):
            setup.edit_ini('[database]\nconnection=first\nconnection=second\n', {})

    def test_redaction_covers_known_secrets_and_unexpected_url_credentials(self):
        result = setup.redact('password=secret rabbit://x:unexpected@localhost/ X-Auth-Token: hidden-token', {'key': 'secret'})
        self.assertNotIn('secret', result)
        self.assertNotIn('unexpected', result)
        self.assertIn('localhost', result)
        self.assertNotIn('hidden-token', result)

    def test_update_refuses_symlink_before_backup_or_write(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'original'
            target.write_text('[DEFAULT]\nworkers=8\n')
            link = Path(directory) / 'link'
            link.symlink_to(target)
            backup = Path(directory) / 'backup'
            with self.assertRaises(RuntimeError):
                setup.update_config(link, backup, {'DEFAULT': {'workers': '1'}})
            self.assertFalse(backup.exists())
            self.assertIn('workers=8', target.read_text())

    def test_cells_accept_expected_topology_and_do_not_expose_connections(self):
        secrets = {'NOVA_DBPASS': 'db-pass', 'RABBIT_PASS': 'queue-pass'}
        base = 'mysql+pymysql://nova:db-pass@127.0.0.1/'
        rows = [
            ['cell0', '00000000-0000-0000-0000-000000000000', 'none:///', base + 'nova_cell0', '0'],
            ['cell1', 'cell-one-id', 'rabbit://openstack:queue-pass@127.0.0.1:5672/', base + 'nova', '0'],
        ]
        self.assertEqual(setup.validate_cells(rows, secrets), {'cell0', 'cell1'})
        for mutation in ('database', 'transport', 'duplicate', 'disabled', 'extra'):
            with self.subTest(mutation=mutation):
                changed = [r.copy() for r in rows]
                if mutation == 'database':
                    changed[1][3] += '_other'
                elif mutation == 'transport':
                    changed[1][2] = 'rabbit://different:private@elsewhere/'
                elif mutation == 'duplicate':
                    changed.append(rows[1].copy())
                elif mutation == 'disabled':
                    changed[1][4] = '1'
                else:
                    changed[1][0] = 'cell2'
                with self.assertRaises(RuntimeError) as caught:
                    setup.validate_cells(changed, secrets)
                self.assertNotIn('db-pass', str(caught.exception))
                self.assertNotIn('private', str(caught.exception))

    def test_config_uses_loopback_services_and_retained_credentials(self):
        secrets = {key: key + '-example' for key in setup.REQUIRED_KEYS}
        options = setup.desired_options(secrets, '192.0.2.10')
        self.assertEqual(options['DEFAULT']['my_ip'], '192.0.2.10')
        self.assertEqual(options['DEFAULT']['osapi_compute_listen'], '127.0.0.1')
        self.assertEqual(options['neutron']['password'], secrets['NEUTRON_PASS'])
        self.assertEqual(options['placement']['password'], secrets['PLACEMENT_PASS'])
        self.assertEqual(options['vnc']['enabled'], 'false')
        self.assertNotIn('use_neutron', options['DEFAULT'])

    def test_identity_retry_reuses_users_and_region_endpoints_without_reset(self):
        identity = FakeIdentity()
        def connect(secrets, username='admin', scoped=True):
            user_id = username + '-id'
            return FakeConnection(identity, user_id)
        with patch.object(setup, 'connection', side_effect=connect), patch.object(setup, 'progress'):
            setup.configure_identity({'NOVA_PASS': 'nova-example', 'NEUTRON_PASS': 'neutron-example'})
            setup.configure_identity({'NOVA_PASS': 'nova-example', 'NEUTRON_PASS': 'neutron-example'})
        self.assertEqual(set(identity.users), {'nova', 'neutron'})
        self.assertEqual(len(identity.endpoint_records), 6)
        self.assertEqual({e.region_id for e in identity.endpoint_records}, {'RegionOne'})
        self.assertEqual(identity.user_creations, 2)

    def test_identity_conflicting_endpoint_is_preserved(self):
        identity = FakeIdentity()
        def connect(secrets, username='admin', scoped=True):
            return FakeConnection(identity, username + '-id')
        with patch.object(setup, 'connection', side_effect=connect), patch.object(setup, 'progress'):
            secrets = {'NOVA_PASS': 'nova-example', 'NEUTRON_PASS': 'neutron-example'}
            setup.configure_identity(secrets)
            identity.endpoint_records[0].url = 'http://existing.example/'
            with self.assertRaisesRegex(RuntimeError, 'conflicting nova public endpoint'):
                setup.configure_identity(secrets)
        self.assertEqual(identity.endpoint_records[0].url, 'http://existing.example/')
        self.assertEqual(len(identity.endpoint_records), 6)

    def test_incorrect_existing_identity_does_not_get_role_assignment(self):
        identity = FakeIdentity()
        def connect(secrets, username='admin', scoped=True):
            return FakeConnection(identity, 'unrelated-user-id')
        with patch.object(setup, 'connection', side_effect=connect), patch.object(setup, 'progress'):
            with self.assertRaisesRegex(RuntimeError, 'does not match retained credentials'):
                setup.configure_identity({'NOVA_PASS': 'nova-example', 'NEUTRON_PASS': 'neutron-example'})
        self.assertEqual(identity.assignments, [])

    def test_compute_readiness_requires_service_hypervisor_and_complete_inventory(self):
        service = SimpleNamespace(state='up', status='enabled')
        hypervisor = SimpleNamespace(state='up', status='enabled', name='local-host')
        provider = SimpleNamespace(name='local-host', id='provider-id')
        inventories = [SimpleNamespace(resource_class=rc, total=8192, reserved=6144)
                       for rc in ('VCPU', 'MEMORY_MB', 'DISK_GB')]
        cloud = SimpleNamespace(
            compute=SimpleNamespace(services=lambda **kwargs: [service],
                                    hypervisors=lambda **kwargs: [hypervisor]),
            placement=SimpleNamespace(resource_providers=lambda: [provider],
                                      resource_provider_inventories=lambda p: inventories))
        self.assertIsNotNone(setup.observed_compute(cloud))
        inventories.pop()
        self.assertIsNone(setup.observed_compute(cloud))
        inventories.append(SimpleNamespace(resource_class='DISK_GB', total=10, reserved=0))
        provider.name = 'different-host'
        self.assertIsNone(setup.observed_compute(cloud))
        provider.name = 'local-host'
        service.state = 'down'
        self.assertIsNone(setup.observed_compute(cloud))

    def test_compute_readiness_rejects_insufficient_schedulable_ram(self):
        inventories = [SimpleNamespace(resource_class=rc, total=6200, reserved=6144)
                       for rc in ('VCPU', 'MEMORY_MB', 'DISK_GB')]
        cloud = SimpleNamespace(
            compute=SimpleNamespace(
                services=lambda **kwargs: [SimpleNamespace(state='up', status='enabled')],
                hypervisors=lambda **kwargs: [SimpleNamespace(state='up', status='enabled', name='host')]),
            placement=SimpleNamespace(
                resource_providers=lambda: [SimpleNamespace(name='host')],
                resource_provider_inventories=lambda p: inventories))
        with self.assertRaisesRegex(RuntimeError, 'schedulable RAM'):
            setup.observed_compute(cloud)


class FakeIdentity:
    def __init__(self):
        self.users = {}
        self.services = {}
        self.roles = {'admin': SimpleNamespace(id='admin-role')}
        self.endpoint_records = []
        self.assignments = []
        self.user_creations = 0

    def find_project(self, *args, **kwargs):
        return SimpleNamespace(id='service-project')

    def find_role(self, name, **kwargs):
        return self.roles.get(name)

    def create_role(self, name):
        self.roles[name] = SimpleNamespace(id=name + '-role')
        return self.roles[name]

    def find_user(self, name, **kwargs):
        return self.users.get(name)

    def create_user(self, name, **kwargs):
        self.user_creations += 1
        self.users[name] = SimpleNamespace(id=name + '-id')
        return self.users[name]

    def assign_project_role_to_user(self, project, user, role):
        self.assignments.append((project.id, user.id, role.id))

    def find_service(self, name):
        return self.services.get(name)

    def create_service(self, name, **kwargs):
        self.services[name] = SimpleNamespace(id=name + '-service', **kwargs)
        return self.services[name]

    def endpoints(self, service_id):
        return [e for e in self.endpoint_records if e.service_id == service_id]

    def create_endpoint(self, **kwargs):
        endpoint = SimpleNamespace(**kwargs)
        self.endpoint_records.append(endpoint)
        return endpoint


class FakeConnection:
    def __init__(self, identity, user_id):
        self.identity = identity
        self.session = SimpleNamespace(get_user_id=lambda: user_id, get_token=lambda: 'unused-token')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


if __name__ == '__main__':
    unittest.main()
