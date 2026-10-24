"""Offline checks for truthful guest evidence and restricted SSH/probe scope."""
import importlib.util
import json
from pathlib import Path
import sys
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


if __name__ == '__main__':
    unittest.main()
