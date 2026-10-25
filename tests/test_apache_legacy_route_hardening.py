import copy
import importlib.util
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('task11_apache_legacy_routes',
        SCRIPTS / 'harden_apache_legacy_routes.py')
    harden = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(harden)
finally:
    sys.path.remove(str(SCRIPTS))


class ApacheLegacyRouteHardeningTests(unittest.TestCase):
    def test_only_requested_route_gets_loopback_authorization(self):
        source = (harden.TARGETS[next(path for path in harden.TARGETS
                                     if harden.TARGETS[path]['route'] == '/image')]['proxy']
                  + '\nKeepAlive Off\nSetEnv proxy-sendchunked 1\n')
        result = harden.route_edit(source, '/image', harden.TARGETS[next(
            path for path in harden.TARGETS if harden.TARGETS[path]['route'] == '/image')]['proxy'])
        self.assertIn('<Location "/image">\n    Require ip 127.0.0.1 ::1\n</Location>', result)
        self.assertIn('KeepAlive Off\nSetEnv proxy-sendchunked 1', result)
        self.assertEqual(result.count('ProxyPass '), 1)

    def test_cinder_duplicate_pass_lines_are_preserved(self):
        item = next(item for item in harden.TARGETS.values() if item['route'] == '/volume')
        source = (item['proxy'] + '\n') * 6 + 'KeepAlive Off\n'
        result = harden.route_edit(source, item['route'], item['proxy'])
        self.assertEqual(result.count(item['proxy']), 6)
        self.assertEqual(result.count('<Location "/volume">'), 1)

    def test_exact_rule_is_idempotent(self):
        item = next(item for item in harden.TARGETS.values() if item['route'] == '/image')
        source = item['proxy'] + '\n\n<Location "/image">\n    Require ip 127.0.0.1 ::1\n</Location>\n'
        self.assertEqual(harden.route_edit(source, item['route'], item['proxy']), source)

    def test_original_proxy_whitespace_is_preserved(self):
        item = next(item for item in harden.TARGETS.values() if item['route'] == '/image')
        source = item['proxy'] + '  \n'
        self.assertTrue(harden.route_edit(source, item['route'], item['proxy']).startswith(source))

    def test_unexpected_upstream_or_authorization_stops(self):
        item = next(item for item in harden.TARGETS.values() if item['route'] == '/image')
        with self.assertRaises(RuntimeError):
            harden.route_edit('ProxyPass "/image" "http://127.0.0.1:1234"\n', item['route'], item['proxy'])
        with self.assertRaises(RuntimeError):
            harden.route_edit(item['proxy'] + '\n<Location "/image">\n    Require all granted\n</Location>\n',
                              item['route'], item['proxy'])

    def test_preserved_routes_must_keep_identical_statuses(self):
        before = self.web_snapshot()
        after = copy.deepcopy(before)
        after['eth0_v4']['/volume'] = {'status': 403}
        after['eth0_v4']['/image'] = {'status': 403}
        harden.compare_access(before, after)

        changed = copy.deepcopy(after)
        changed['eth0_v4']['/'] = {'status': 404}
        with self.assertRaises(RuntimeError):
            harden.compare_access(before, changed)

    def test_loopback_must_remain_allowed_and_external_ipv4_must_be_denied(self):
        before = self.web_snapshot()
        after = copy.deepcopy(before)
        after['eth0_v4']['/volume'] = {'status': 403}
        after['eth0_v4']['/image'] = {'status': 403}
        after['loopback_v4']['/volume'] = {'status': 403}
        with self.assertRaises(RuntimeError):
            harden.compare_access(before, after)

        after['loopback_v4']['/volume'] = {'status': 503}
        after['eth0_v4']['/image'] = {'status': 503}
        with self.assertRaises(RuntimeError):
            harden.compare_access(before, after)

    def test_discovery_statuses_for_all_openstack_apis_must_be_unchanged(self):
        before = self.web_snapshot()
        after = copy.deepcopy(before)
        after['api_glance']['/'] = {'status': 500}
        with self.assertRaises(RuntimeError):
            harden.compare_access(before, after)

    def web_snapshot(self):
        before = {
            'loopback_v4': {'/': {'status': 200}, '/identity': {'status': 503}, '/placement': {'status': 500},
                            '/volume': {'status': 503}, '/image': {'status': 503}},
            'eth0_v4': {'/': {'status': 200}, '/identity': {'status': 403}, '/placement': {'status': 403},
                        '/volume': {'status': 503}, '/image': {'status': 503}},
        }
        for name in harden.HTTP_APIS:
            before['api_' + name] = {'/': {'status': 200}}
        return before


if __name__ == '__main__':
    unittest.main()
