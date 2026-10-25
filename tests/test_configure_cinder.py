"""Offline configuration-shape tests for the local Cinder setup script."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location(
        'task18_configure_cinder', SCRIPTS / 'configure_cinder.py')
    setup = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(setup)
finally:
    sys.path.remove(str(SCRIPTS))


class ConfigureCinderTests(unittest.TestCase):
    def test_backend_and_endpoint_settings_are_loopback_scoped(self):
        values = {'RABBIT_PASS': 'a' * 64, 'CINDER_PASS': 'b' * 64,
                  'CINDER_DBPASS': 'c' * 64}
        options = setup.config_options(values)
        self.assertEqual(options['DEFAULT']['enabled_backends'], 'lvm')
        self.assertEqual(options['DEFAULT']['my_ip'], '127.0.0.1')
        self.assertEqual(options['lvm']['target_helper'], 'tgtadm')
        self.assertEqual(options['lvm']['target_ip_address'], '127.0.0.1')
        self.assertEqual(options['lvm']['volume_group'], 'cinder-poc-vg')
        self.assertEqual(options['lvm']['lvm_conf_file'], '/etc/cinder/cinder-poc-lvm.conf')
        self.assertEqual(options['keystone_authtoken']['service_token_roles_required'], 'true')
        self.assertEqual(options['service_user']['send_service_user_token'], 'true')

    def test_tgt_include_is_narrow_to_cinder_generated_target_files(self):
        self.assertEqual(setup.TGT_CONF, Path('/etc/tgt/conf.d/cinder.conf'))
        self.assertEqual(setup.API_URL, 'http://127.0.0.1:8776/v3/%(project_id)s')

    def test_role_ids_read_keystone_nested_role_reference(self):
        assignments = [SimpleNamespace(role={'id': 'role-a'}),
                       SimpleNamespace(role=SimpleNamespace(id='role-b')),
                       SimpleNamespace(role_id='role-c')]
        self.assertEqual(setup.role_ids_from_assignments(assignments),
                         {'role-a', 'role-b', 'role-c'})

    def test_endpoint_accepts_template_or_scoped_project_resolution(self):
        self.assertTrue(setup.expected_cinder_endpoint(
            setup.API_URL, 'project-id'))
        self.assertTrue(setup.expected_cinder_endpoint(
            'http://127.0.0.1:8776/v3/project-id', 'project-id'))

    def test_endpoint_rejects_other_hosts_and_paths(self):
        self.assertFalse(setup.expected_cinder_endpoint(
            'http://0.0.0.0:8776/v3/%(project_id)s', 'project-id'))
        self.assertFalse(setup.expected_cinder_endpoint(
            'http://127.0.0.1:8776/v2/project-id', 'project-id'))


if __name__ == '__main__':
    unittest.main()
