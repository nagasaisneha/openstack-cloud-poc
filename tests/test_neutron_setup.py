"""Offline Neutron setup safety checks; no host changes or API access."""
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('task11_configure_neutron', SCRIPTS / 'configure_neutron.py')
    setup = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(setup)
finally:
    sys.path.remove(str(SCRIPTS))


class NeutronSetupTests(unittest.TestCase):
    def secrets(self):
        keys = setup.common.REQUIRED_KEYS + (setup.DB_KEY,)
        return {key: key + '-test' for key in keys}

    def test_ml2_and_metadata_use_local_endpoints_and_matching_shared_secret(self):
        secrets = self.secrets()
        neutron, ml2, metadata = setup.desired_options(secrets, 1500)
        self.assertEqual(neutron['DEFAULT']['bind_host'], '127.0.0.1')
        self.assertEqual(neutron['DEFAULT']['core_plugin'], 'ml2')
        self.assertEqual(neutron['DEFAULT']['service_plugins'], 'ovn-router')
        self.assertEqual(ml2['ml2']['type_drivers'], 'geneve')
        self.assertEqual(ml2['ml2']['extension_drivers'], 'port_security')
        self.assertEqual(ml2['ml2_type_geneve']['max_header_size'], '38')
        self.assertEqual(ml2['ovn']['neutron_sync_mode'], 'log')
        self.assertEqual(ml2['ovn']['ovn_metadata_enabled'], 'true')
        self.assertEqual(metadata['ovs']['ovsdb_connection'], 'unix:/run/openvswitch/db.sock')
        self.assertEqual(metadata['DEFAULT']['metadata_proxy_shared_secret'], secrets['METADATA_SECRET'])
        self.assertEqual(metadata['DEFAULT']['metadata_workers'], '0')
        self.assertEqual(neutron['nova']['password'], secrets['NOVA_PASS'])
        self.assertEqual(ml2['ovn']['ovn_sb_connection'], metadata['ovn']['ovn_sb_connection'])

    def test_invalid_mtu_is_rejected_without_configuration(self):
        for mtu in (100, 1279, 65536, '1500'):
            with self.subTest(mtu=mtu), self.assertRaises(RuntimeError):
                setup.desired_options(self.secrets(), mtu)

    def test_plugin_defaults_edit_preserves_unrelated_shell_configuration(self):
        original = '# example\nOTHER_SETTING=keep\nNEUTRON_PLUGIN_CONFIG=/old/path\n'
        changed = setup.plugin_environment(original)
        self.assertIn('OTHER_SETTING=keep', changed)
        self.assertIn('# example', changed)
        self.assertIn(setup.PLUGIN_CONFIG, changed)
        self.assertNotIn('/old/path', changed)
        self.assertEqual(changed, setup.plugin_environment(changed))

    def test_plugin_defaults_adds_missing_variable_and_rejects_duplicates(self):
        changed = setup.plugin_environment('# package defaults\n')
        self.assertIn('NEUTRON_PLUGIN_CONFIG=', changed)
        with self.assertRaises(RuntimeError):
            setup.plugin_environment('NEUTRON_PLUGIN_CONFIG=first\nexport NEUTRON_PLUGIN_CONFIG=second\n')

    def cloud(self, agents, aliases=None):
        if aliases is None:
            aliases = ['router', 'security-group', 'port-security']
        return SimpleNamespace(network=SimpleNamespace(
            networks=lambda: [], agents=lambda: agents,
            extensions=lambda: [SimpleNamespace(alias=alias) for alias in aliases]))

    def agent(self, kind, alive=True, admin=True, host='local-host'):
        return SimpleNamespace(agent_type=kind, is_alive=alive, is_admin_state_up=admin, host=host)

    def test_readiness_requires_live_controller_and_metadata_on_same_host(self):
        controller = self.agent('OVN Controller agent')
        metadata = self.agent('OVN Metadata agent')
        self.assertIsNotNone(setup.network_readiness(self.cloud([controller, metadata])))
        for agents in ([controller], [metadata],
                       [controller, self.agent('OVN Metadata agent', alive=False)],
                       [controller, self.agent('OVN Metadata agent', admin=False)],
                       [controller, self.agent('OVN Metadata agent', host='other-host')]):
            with self.subTest(agents=agents):
                self.assertIsNone(setup.network_readiness(self.cloud(agents)))

    def test_readiness_refuses_missing_security_extension(self):
        agents = [self.agent('OVN Controller agent'), self.agent('OVN Metadata agent')]
        with self.assertRaisesRegex(RuntimeError, 'missing required'):
            setup.network_readiness(self.cloud(agents, ['router', 'port-security']))

    def test_backend_preflight_rejects_encap_drift_before_writing(self):
        original = {'ovn-encap-ip': '192.0.2.2', 'ovn-encap-type': 'geneve', 'system-id': 'preserved-id'}
        with patch.object(setup, 'read_ovs_external_ids', return_value=original), \
             patch.object(setup.common, 'run', return_value='') as run:
            with self.assertRaisesRegex(RuntimeError, 'differs from current eth0'):
                setup.verify_backend('192.0.2.1')
        self.assertEqual(run.call_count, 1)
        self.assertEqual(original['system-id'], 'preserved-id')

    def test_backend_preflight_rejects_wildcard_listener(self):
        original = {'ovn-encap-ip': '192.0.2.1', 'ovn-encap-type': 'geneve', 'system-id': 'preserved-id'}
        with patch.object(setup, 'read_ovs_external_ids', return_value=original), \
             patch.object(setup.common, 'run', side_effect=['', 'ptcp:6641:0.0.0.0']):
            with self.assertRaisesRegex(RuntimeError, 'loopback topology'):
                setup.verify_backend('192.0.2.1')

    def test_generic_config_writer_preserves_content_and_original_metadata_backup(self):
        # A local temp file exercises the actual atomic writer without host config.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'defaults'
            backup = Path(directory) / 'before'
            original = 'OTHER=retained\n'
            path.write_text(original)
            path.chmod(0o640)
            setup.common.update_text_file(path, backup, setup.plugin_environment)
            self.assertEqual(backup.read_text(), original)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)
            self.assertTrue(Path(str(backup) + '.metadata.json').is_file())
            self.assertIn('OTHER=retained', path.read_text())


if __name__ == '__main__':
    unittest.main()
