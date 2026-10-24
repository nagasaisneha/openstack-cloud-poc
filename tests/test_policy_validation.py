"""Offline safety and evidence tests for the owned-rule policy experiment."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('task11_policy', SCRIPTS / 'validate_policy.py')
    setup = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(setup)
finally:
    sys.path.remove(str(SCRIPTS))


class PolicyValidationTests(unittest.TestCase):
    def state(self):
        return {'run_id': 'run-marker', 'project_id': 'project-id', 'metadata_ip': '172.30.240.2',
                'guest_ip': '172.30.240.154', 'namespace': 'ovnmeta-test',
                'ids': {'security_group': 'group-id'}, 'results': {}}

    def rule(self, state, protocol, **overrides):
        values = dict(setup.expected_rule(state, protocol), id='rule-' + protocol,
                      remote_group_id=None, project_id=state['project_id'])
        values.update(overrides)
        return SimpleNamespace(**values)

    def cloud(self, state, rules):
        group = SimpleNamespace(project_id=state['project_id'],
                                description='Task 11 local POC run ' + state['run_id'])
        return SimpleNamespace(network=SimpleNamespace(
            get_security_group=lambda _: group, security_group_rules=lambda **kwargs: rules,
            create_security_group_rule=Mock(return_value=SimpleNamespace(id='restored-rule')),
            delete_security_group_rule=Mock()))

    def root_fstat(self, uid=0):
        original = os.fstat
        def fstat(descriptor):
            values = list(original(descriptor))
            values[4] = uid
            return os.stat_result(values)
        return fstat

    def test_trust_mode_correction_keeps_original_pinned_key_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'known_hosts'
            path.write_bytes(b'existing pinned public key\n')
            path.chmod(0o644)
            with patch.object(os, 'fstat', self.root_fstat()):
                setup.tighten_known_hosts(path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.read_bytes(), b'existing pinned public key\n')

    def test_trust_mode_correction_refuses_wrong_owner_and_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'known_hosts'
            path.write_text('preserved')
            path.chmod(0o644)
            with patch.object(os, 'fstat', self.root_fstat(uid=12345)):
                with self.assertRaises(RuntimeError):
                    setup.tighten_known_hosts(path)
            link = Path(temporary) / 'link'
            link.symlink_to(path)
            with self.assertRaises(OSError):
                setup.tighten_known_hosts(link)
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)
            self.assertEqual(path.read_text(), 'preserved')

    def test_only_two_exact_owned_ingress_rules_are_accepted(self):
        state = self.state()
        rules = [self.rule(state, 'tcp'), self.rule(state, 'icmp')]
        setup.select_probe_rules(self.cloud(state, rules), state)
        for changed in (
            rules + [self.rule(state, 'tcp', remote_ip_prefix='0.0.0.0/0')],
            [self.rule(state, 'tcp', description='unrelated'), rules[1]],
            [self.rule(state, 'tcp', port_range_max=23), rules[1]],
            [self.rule(state, 'tcp', remote_group_id='another-group'), rules[1]],
        ):
            with self.subTest(changed=changed), self.assertRaises(RuntimeError):
                setup.select_probe_rules(self.cloud(state, changed), state)

    def test_denial_requires_three_consecutive_distinct_timeout_connections(self):
        outcomes = [dict(outcome=kind, source_port=port) for kind, port in (
            ('timeout', 40001), ('connected', 40002), ('timeout', 40003),
            ('timeout', 40003), ('timeout', 40004), ('timeout', 40005))]
        probe = Mock(side_effect=outcomes)
        ports = {40000}
        self.assertEqual(setup.observe_denial(self.state(), ports, probe, lambda _: None), 3)
        self.assertEqual(probe.call_count, 6)

    def test_connection_refusal_or_reused_tuple_is_not_denial_proof(self):
        for probe in (
            Mock(return_value=dict(outcome='error', errno=111, source_port=40001)),
            Mock(return_value=dict(outcome='timeout', source_port=40000)),
        ):
            with self.subTest(probe=probe), self.assertRaises(RuntimeError):
                setup.observe_denial(self.state(), {40000}, probe, lambda _: None)

    def test_restore_retry_reuses_identical_rule_and_preserves_other_rules(self):
        state = self.state()
        state['policy_restore_pending'] = {'run_id': state['run_id'], 'group_id': 'group-id',
                                          'source': '172.30.240.2/32'}
        rules = [self.rule(state, 'tcp'), self.rule(state, 'icmp')]
        cloud = self.cloud(state, rules)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'state.json'
            self.assertEqual(setup.restore_pending_rule(cloud, state, path), 'rule-tcp')
            self.assertNotIn('policy_restore_pending', json.loads(path.read_text()))
        cloud.network.create_security_group_rule.assert_not_called()
        cloud.network.delete_security_group_rule.assert_not_called()

    def test_failed_restore_retains_recovery_marker(self):
        state = self.state()
        state['policy_restore_pending'] = {'run_id': state['run_id'], 'group_id': 'group-id',
                                          'source': '172.30.240.2/32'}
        cloud = self.cloud(state, [])
        cloud.network.create_security_group_rule.side_effect = RuntimeError('temporary API failure')
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(RuntimeError):
                setup.restore_pending_rule(cloud, state, Path(temporary) / 'state.json')
        self.assertIn('policy_restore_pending', state)

    def test_missing_rule_is_restored_with_exact_original_narrow_scope(self):
        state = self.state()
        state['policy_restore_pending'] = {'run_id': state['run_id'], 'group_id': 'group-id',
                                          'source': '172.30.240.2/32'}
        cloud = self.cloud(state, [self.rule(state, 'icmp')])
        with tempfile.TemporaryDirectory() as temporary:
            setup.restore_pending_rule(cloud, state, Path(temporary) / 'state.json')
        cloud.network.create_security_group_rule.assert_called_once_with(**setup.expected_rule(state, 'tcp'))
        self.assertEqual(state['last_restored_ssh_rule_id'], 'restored-rule')
        cloud.network.delete_security_group_rule.assert_not_called()

    def test_finally_restores_rule_when_packet_verification_fails(self):
        state = self.state()
        rules = [self.rule(state, 'tcp'), self.rule(state, 'icmp')]
        cloud = self.cloud(state, rules)
        report = {'boot_id': 'boot-id'}
        with tempfile.TemporaryDirectory() as temporary, patch.object(setup, 'progress'), \
             patch.object(setup.guest, 'inspect_guest', return_value=report), \
             patch.object(setup.guest, 'probe_ping'), \
             patch.object(setup, 'tcp_probe', return_value=dict(outcome='connected', source_port=40000)), \
             patch.object(setup, 'observe_denial', side_effect=RuntimeError('denial test failed')):
            path = Path(temporary) / 'state.json'
            with self.assertRaisesRegex(RuntimeError, 'denial test failed'):
                setup.validate_policy(cloud, state, path, SimpleNamespace(mtu=1442), SimpleNamespace(mac_address='mac'))
            self.assertNotIn('policy_restore_pending', json.loads(path.read_text()))
        cloud.network.delete_security_group_rule.assert_called_once_with('rule-tcp', ignore_missing=False)
        self.assertFalse(state['results']['security_group_policy']['verified'])


if __name__ == '__main__':
    unittest.main()
