"""Swift preflight boundaries; no root access, live cloud or object mutations."""
import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('task19_swift_readiness', SCRIPTS / 'inspect_swift_readiness.py')
    setup = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(setup)
finally:
    sys.path.remove(str(SCRIPTS))


PROJECT = '1' * 32
ENDPOINT = 'http://127.0.0.1:8080/v1/AUTH_' + PROJECT


class SwiftReadinessTests(unittest.TestCase):
    def ready_report(self):
        return {'host': {
            'mem_available_kib': setup.MIN_AVAILABLE_KIB,
            'filesystem': {'type': 'ext4', 'writable_mount': True,
                           'xattrs_explicitly_disabled': False,
                           'free_bytes': setup.MIN_FREE_BYTES, 'free_inodes': 4096},
            'storage_parents': {name: {'kind': 'directory', 'root_owned': True,
                                      'group_or_other_writable': False} for name in ('/srv', '/var/lib')},
            'loop_control_present': True,
            'packages': {name: {'installed': False} for name in setup.PACKAGES},
            'package_candidates': {name: True for name in setup.SERVER_PACKAGES},
            'units': {name: {'active': 'inactive', 'load': 'not-found'} for name in setup.UNITS[:4]} |
                     {'memcached.service': {'active': 'active'}},
            'paths': {name: {'present': False} for name in setup.PATHS},
            'listeners': {str(port): {'count': 0, 'loopback_only': False} for port in setup.PORTS}},
            'cloud': {'keystone_auth': True, 'endpoint_present': False, 'endpoint_status': 'not_in_catalog'}}

    def cloud(self, endpoint=ENDPOINT):
        return NS(session=NS(get_project_id=Mock(return_value=PROJECT),
                             get_endpoint=Mock(return_value=endpoint),
                             head=Mock(return_value=NS(status_code=204, headers={'SECRET': 'TOKEN'}))))

    def test_ready_means_only_guarded_setup_and_never_object_acceptance(self):
        result = setup.installation_readiness(self.ready_report())
        self.assertEqual(result['status'], 'ready_for_guarded_setup')
        self.assertEqual(result['object_round_trip'], 'not_tested')
        self.assertTrue(all(result['checks'].values()))

    def test_capacity_or_auth_loss_blocks_setup(self):
        report = self.ready_report()
        for section, key, value in (
                ('host', 'mem_available_kib', setup.MIN_AVAILABLE_KIB - 1),
                ('cloud', 'keystone_auth', False), ('host', 'loop_control_present', False)):
            changed = copy.deepcopy(report)
            changed[section][key] = value
            self.assertEqual(setup.installation_readiness(changed)['status'], 'blocked_prerequisites')
        for key, value in (('free_bytes', setup.MIN_FREE_BYTES - 1), ('free_inodes', 4095),
                           ('type', '9p'), ('writable_mount', False), ('xattrs_explicitly_disabled', True)):
            changed = copy.deepcopy(report)
            changed['host']['filesystem'][key] = value
            self.assertEqual(setup.installation_readiness(changed)['status'], 'blocked_prerequisites')

    def test_existing_empty_paths_installed_software_or_ports_require_review(self):
        report = self.ready_report()
        for path in setup.PATHS:
            changed = copy.deepcopy(report)
            changed['host']['paths'][path] = {'present': True, 'empty': True}
            self.assertEqual(setup.installation_readiness(changed)['status'], 'review_existing_state')
        for name in setup.SERVER_PACKAGES:
            changed = copy.deepcopy(report)
            changed['host']['packages'][name]['installed'] = True
            self.assertEqual(setup.installation_readiness(changed)['status'], 'review_existing_state')
        for port in setup.PORTS:
            changed = copy.deepcopy(report)
            changed['host']['listeners'][str(port)]['count'] = 1
            self.assertEqual(setup.installation_readiness(changed)['status'], 'review_existing_state')
        report['cloud']['endpoint_present'] = True
        self.assertEqual(setup.installation_readiness(report)['status'], 'review_existing_state')

    def test_unit_inventory_failure_cannot_authorize_fresh_install(self):
        report = self.ready_report()
        report['host']['units']['swift-proxy.service'] = {'active': 'unknown', 'load': 'unavailable'}
        self.assertEqual(setup.installation_readiness(report)['status'], 'review_existing_state')

    def test_unsafe_parent_or_unavailable_package_blocks_setup(self):
        report = self.ready_report()
        report['host']['storage_parents']['/srv']['kind'] = 'symlink'
        self.assertEqual(setup.installation_readiness(report)['status'], 'blocked_prerequisites')
        report = self.ready_report()
        report['host']['package_candidates']['swift-proxy'] = False
        self.assertEqual(setup.installation_readiness(report)['status'], 'blocked_prerequisites')

    def test_only_exact_current_loopback_account_is_probed_without_redirects(self):
        cloud = self.cloud()
        result = setup.cloud_inventory(cloud)
        self.assertEqual(result['account_head'], 204)
        cloud.session.head.assert_called_once_with(ENDPOINT, allow_redirects=False, raise_exc=False, timeout=10)
        self.assertNotIn(PROJECT, repr(result))
        self.assertNotIn('TOKEN', repr(result))

    def test_remote_malformed_wrong_project_and_credential_urls_receive_no_token(self):
        for endpoint in ('https://example.org/v1/AUTH_' + PROJECT,
                         ENDPOINT.replace('127.0.0.1', 'localhost'),
                         ENDPOINT.replace(PROJECT, '2' * 32), ENDPOINT + '/',
                         ENDPOINT + '?token=SECRET', ENDPOINT + '#SECRET',
                         ENDPOINT.replace('127.0.0.1', 'user:SECRET@127.0.0.1'),
                         'http://[invalid', None):
            cloud = self.cloud(endpoint)
            result = setup.cloud_inventory(cloud)
            cloud.session.head.assert_not_called()
            self.assertNotIn('SECRET', repr(result))

    def test_missing_catalog_auth_failure_and_head_errors_are_distinct_and_redacted(self):
        cloud = self.cloud()
        error = RuntimeError('SECRET_TOKEN PRIVATE_ENDPOINT')
        error.status_code = 403
        cloud.session.get_endpoint.side_effect = error
        result = setup.cloud_inventory(cloud)
        self.assertTrue(result['keystone_auth'])
        self.assertFalse(result['endpoint_present'])
        self.assertEqual(result['failure']['http_status'], 403)
        cloud.session.head.assert_not_called()
        report = self.ready_report()
        report['cloud'] = result
        self.assertEqual(setup.installation_readiness(report)['status'], 'blocked_prerequisites')
        cloud.session.get_project_id.side_effect = error
        self.assertFalse(setup.cloud_inventory(cloud)['keystone_auth'])
        cloud = self.cloud()
        cloud.session.head.side_effect = error
        result = setup.cloud_inventory(cloud)
        self.assertTrue(result['endpoint_present'])
        self.assertNotIn('SECRET', repr(result))

    def test_typed_endpoint_absence_is_verified_separately_from_catalog_failure(self):
        from keystoneauth1.exceptions.catalog import EndpointNotFound

        cloud = self.cloud()
        cloud.session.get_endpoint.side_effect = EndpointNotFound('PRIVATE_ENDPOINT')
        result = setup.cloud_inventory(cloud)
        self.assertTrue(result['keystone_auth'])
        self.assertEqual(result['endpoint_status'], 'not_in_catalog')
        cloud.session.head.assert_not_called()
        report = self.ready_report()
        report['cloud'] = result
        self.assertEqual(setup.installation_readiness(report)['status'], 'ready_for_guarded_setup')
        self.assertNotIn('PRIVATE_ENDPOINT', repr(result))

    def test_head_404_or_redirect_does_not_claim_account_ready(self):
        for status in (301, 302, 401, 403, 404, 500):
            cloud = self.cloud()
            cloud.session.head.return_value.status_code = status
            self.assertEqual(setup.cloud_inventory(cloud)['endpoint_status'], 'not_ready')

    def test_listener_summary_flags_wildcards_and_omits_other_hosts(self):
        output = ('LISTEN 0 128 127.0.0.1:8080 0.0.0.0:*\n'
                  'LISTEN 0 128 [::1]:6200 [::]:*\n'
                  'LISTEN 0 128 *:6201 *:*\n'
                  'LISTEN 0 128 [::]:8080 [::]:*\n'
                  'LISTEN 0 128 192.0.2.19:22 0.0.0.0:*\n')
        result = setup.listener_inventory(output)
        self.assertEqual(result['8080'], {'count': 2, 'loopback_only': False})
        self.assertTrue(result['6200']['loopback_only'])
        self.assertFalse(result['6201']['loopback_only'])
        self.assertNotIn('192.0.2.19', repr(result))
        with self.assertRaises(RuntimeError):
            setup.listener_inventory('garbled output')

    def test_symlink_is_observed_without_following_or_listing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'swift'
            path.symlink_to('/root')
            result = setup.path_observation(path)
            self.assertEqual(result['kind'], 'symlink')
            self.assertNotIn('empty', result)
            self.assertEqual(setup.path_observation(Path(directory) / 'absent'), {'present': False})

    def test_package_candidate_none_does_not_pass(self):
        output = ''.join(name + ':\n  Installed: (none)\n  Candidate: ' +
                         ('(none)' if name == 'swift-object' else '2.33.0-0ubuntu1~cloud0') + '\n'
                         for name in setup.SERVER_PACKAGES)
        with patch.object(setup.common, 'run', return_value=output):
            result = setup.package_candidates()
        self.assertFalse(result['swift-object'])
        self.assertTrue(result['swift-proxy'])


if __name__ == '__main__':
    unittest.main()
