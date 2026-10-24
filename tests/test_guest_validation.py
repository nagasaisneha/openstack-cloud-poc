"""Offline checks for truthful guest evidence and restricted SSH/probe scope."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('task11_guest_probe', SCRIPTS / 'validate_guest.py')
    setup = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(setup)
finally:
    sys.path.remove(str(SCRIPTS))


class GuestValidationTests(unittest.TestCase):
    def state(self):
        return {'run_id': 'a' * 32, 'project_id': 'project-id', 'guest_ip': '172.30.240.154',
                'cidr': '172.30.240.0/24', 'metadata_ip': '172.30.240.2',
                'namespace': 'ovnmeta-test', 'ids': {'server': 'server-id'}}

    def report(self, **overrides):
        state = self.state()
        values = {
            'INTERFACE': 'eth0', 'BOOT_ID': '039800cc-1545-4900-8a0b-eb3a1d5c4cb4',
            'ADDRESS': 'inet 172.30.240.154/24 brd 172.30.240.255 scope global eth0',
            'MAC': 'fa:16:3e:12:34:56', 'MTU': '1442',
            'DHCP': "ip_address='172.30.240.154'\ndhcp_lease_time=43200",
            'METADATA': json.dumps({'uuid': 'server-id', 'name': 'task11-' + state['run_id'][:12] + '-vm',
                                    'project_id': 'project-id'})}
        values.update(overrides)
        return ''.join(f'TASK11_{key}_BEGIN\n{value}\nTASK11_{key}_END\n' for key, value in values.items())

    def test_full_guest_evidence_passes_with_no_secret_or_full_metadata_persisted(self):
        result = setup.parse_guest_report(self.report(), self.state(), 1442, 'fa:16:3e:12:34:56')
        self.assertEqual(result['ipv4'], '172.30.240.154')
        self.assertTrue(result['dhcp_lease_verified'])
        self.assertEqual(result['metadata_uuid'], 'server-id')
        self.assertNotIn('METADATA', result)

    def test_missing_or_ambiguous_frame_is_rejected(self):
        text = self.report()
        for changed in (text.replace('TASK11_DHCP_BEGIN', 'MISSING'),
                        text + 'TASK11_DHCP_BEGIN\nip_address=172.30.240.154\nTASK11_DHCP_END\n'):
            with self.subTest(changed=changed[-100:]), self.assertRaises(RuntimeError):
                setup.parse_guest_report(changed, self.state(), 1442, 'fa:16:3e:12:34:56')

    def test_applied_address_mtu_mac_and_lease_must_match_provider_evidence(self):
        variants = ({'ADDRESS': 'inet 172.30.240.153/24'}, {'ADDRESS': 'inet 172.30.240.154/16'},
                    {'MAC': 'fa:16:3e:00:00:01'}, {'MTU': '1500'},
                    {'DHCP': 'ip_address=172.30.240.153'}, {'DHCP': 'no lease'})
        for changed in variants:
            with self.subTest(changed=changed), self.assertRaises(RuntimeError):
                setup.parse_guest_report(self.report(**changed), self.state(), 1442, 'fa:16:3e:12:34:56')

    def test_wrong_metadata_instance_name_or_project_is_rejected(self):
        for field, value in [('uuid', 'another-server'), ('name', 'another-name'), ('project_id', 'another-project')]:
            metadata = json.loads(setup.frame(self.report(), 'METADATA'))
            metadata[field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                setup.parse_guest_report(self.report(METADATA=json.dumps(metadata)), self.state(), 1442, 'fa:16:3e:12:34:56')

    def test_ssh_uses_only_retained_key_and_exact_namespace_source(self):
        command = setup.ssh_command(self.state())
        self.assertEqual(command[:4], ['ip', 'netns', 'exec', 'ovnmeta-test'])
        self.assertEqual(command[command.index('-b') + 1], '172.30.240.2')
        for setting in ('BatchMode=yes', 'PasswordAuthentication=no', 'KbdInteractiveAuthentication=no',
                        'StrictHostKeyChecking=accept-new', 'IdentitiesOnly=yes',
                        'ProxyCommand=none', 'ForwardAgent=no', 'ClearAllForwardings=yes'):
            self.assertIn(setting, command)
        self.assertEqual(command[command.index('-F') + 1], '/dev/null')
        self.assertEqual(command[-3:], ['172.30.240.154', 'sh', '-s'])

    def test_mtu_ping_uses_payload_with_headers_and_requires_all_replies(self):
        with patch.object(setup.common, 'run', return_value='3 packets transmitted, 3 received, 0% packet loss') as run:
            setup.probe_ping(self.state(), 1442)
            command = run.call_args.args[0]
            self.assertEqual(command[command.index('-s') + 1], '1414')
            self.assertEqual(command[command.index('-I') + 1], '172.30.240.2')
            self.assertIn('do', command)
            self.assertNotIn('-e', command)  # Unsupported by the verified host ping.
            self.assertEqual(command[command.index('ping') + 1:],
                ['-n', '-I', '172.30.240.2', '-c', '3', '-W', '2',
                 '-M', 'do', '-s', '1414', '172.30.240.154'])
        with patch.object(setup.common, 'run', return_value='3 packets transmitted, 1 received'):
            with self.assertRaises(RuntimeError):
                setup.probe_ping(self.state(), 1442)

    def test_guest_probe_contains_lease_dump_and_link_local_metadata_only(self):
        self.assertIn('dhcpcd -U', setup.GUEST_PROBE)
        self.assertIn('http://169.254.169.254/openstack/latest/meta_data.json', setup.GUEST_PROBE)
        self.assertNotIn('reboot', setup.GUEST_PROBE)
        self.assertNotIn('apt', setup.GUEST_PROBE)

    def test_ssh_connection_failure_is_bounded_and_never_marks_ready(self):
        with patch.object(setup.time, 'monotonic', side_effect=[0, 1, 181]), \
             patch.object(setup.time, 'sleep'), patch.object(setup, 'progress'), \
             patch.object(setup.subprocess, 'run', return_value=SimpleNamespace(returncode=255, stdout='connection unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'SSH readiness timed out'):
                setup.inspect_guest(self.state(), 1442, 'fa:16:3e:12:34:56')

    def test_guest_command_failure_is_reported_without_retrying_as_boot_delay(self):
        with patch.object(setup.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='lease inspection unavailable')) as run:
            with self.assertRaisesRegex(RuntimeError, 'inspection command failed'):
                setup.inspect_guest(self.state(), 1442, 'fa:16:3e:12:34:56')
        run.assert_called_once()

    def test_failed_ping_clears_stale_success_and_preserves_guest_evidence(self):
        state = self.state()
        state['results'] = {'guest': {'dhcp_lease_verified': True},
                            'mtu_ping': {'verified': True}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'run.json'
            with patch.object(setup, 'probe_ping', side_effect=RuntimeError('probe failed')):
                with self.assertRaisesRegex(RuntimeError, 'probe failed'):
                    setup.record_ping(state, path, 1442)
            stored = json.loads(path.read_text())
        self.assertFalse(stored['results']['mtu_ping']['verified'])
        self.assertTrue(stored['results']['guest']['dhcp_lease_verified'])

    def test_successful_ping_records_only_its_own_evidence(self):
        state = self.state()
        state['results'] = {'existing': 'retained'}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'run.json'
            with patch.object(setup, 'probe_ping') as probe:
                setup.record_ping(state, path, 1442)
            stored = json.loads(path.read_text())
        probe.assert_called_once_with(state, 1442)
        self.assertTrue(stored['results']['mtu_ping']['verified'])
        self.assertEqual(stored['results']['existing'], 'retained')
        self.assertNotIn('guest', stored['results'])

    def manifest(self):
        state = self.state()
        state['ids'] = {name: value for name, value in zip(
            ('server', 'network', 'subnet', 'security_group'),
            ('82382430-4b64-4386-a55b-8468e04da011',
             '6ee5ad82-efc5-405d-bf5c-a4953e9a063b',
             '25f8362e-436b-405e-8158-2e2d552cd2e7',
             '5a3b3c35-d5d6-4cbe-bf62-41ca04aec923'))}
        state['namespace'] = 'ovnmeta-' + state['ids']['network']
        state['image_id'] = setup.launch.IMAGE_ID
        return state

    def fake_root_lstat(self):
        original = Path.lstat
        def lstat(path):
            values = list(original(path))
            values[4] = 0  # Test protected-file policy without actual root access.
            return os.stat_result(values)
        return lstat

    def test_ping_only_manifest_does_not_require_or_touch_ssh_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            directory.chmod(0o700)
            manifest = directory / 'run.json'
            manifest.write_text(json.dumps(self.manifest()))
            manifest.chmod(0o600)
            known_hosts = directory / 'known_hosts'
            known_hosts.write_text('retained public host key\n')
            known_hosts.chmod(0o644)
            cloud = SimpleNamespace(session=SimpleNamespace(get_project_id=lambda: 'project-id'))
            with patch.object(setup.launch, 'DATA_DIR', directory), \
                 patch.object(Path, 'lstat', self.fake_root_lstat()):
                state, _ = setup.load_run(cloud, require_ssh=False)
            self.assertEqual(state['ids']['server'], self.manifest()['ids']['server'])
            self.assertFalse((directory / 'id_ed25519').exists())
            self.assertEqual(known_hosts.read_text(), 'retained public host key\n')
            self.assertEqual(known_hosts.stat().st_mode & 0o777, 0o644)

    def test_ssh_manifest_still_rejects_wrong_known_hosts_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            directory.chmod(0o700)
            for name, content, mode in (
                ('run.json', json.dumps(self.manifest()), 0o600),
                ('id_ed25519', 'test-key', 0o600),
                ('known_hosts', 'retained public host key\n', 0o644),
            ):
                path = directory / name
                path.write_text(content)
                path.chmod(mode)
            cloud = SimpleNamespace(session=SimpleNamespace(get_project_id=lambda: 'project-id'))
            with patch.object(setup.launch, 'DATA_DIR', directory), \
                 patch.object(Path, 'lstat', self.fake_root_lstat()):
                with self.assertRaisesRegex(RuntimeError, 'known_hosts must be'):
                    setup.load_run(cloud)
            self.assertEqual((directory / 'known_hosts').read_text(), 'retained public host key\n')


if __name__ == '__main__':
    unittest.main()
