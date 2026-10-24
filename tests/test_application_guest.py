"""Offline acceptance guard tests; all cloud/SSH/packet calls use doubles."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('task11_application_guest', SCRIPTS / 'validate_application_guest.py')
    setup = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(setup)
finally:
    sys.path.remove(str(SCRIPTS))

MAC = 'fa:16:3e:11:22:33'


class ApplicationGuestTests(unittest.TestCase):
    def interface_inventory(self):
        addresses = [{'ifindex': 11, 'ifname': 'tap-test',
                      'addr_info': [{'family': 'inet', 'local': '172.30.241.2', 'prefixlen': 24}]}]
        links = [{'ifindex': 11, 'ifname': 'tap-test', 'link_type': 'ether',
                  'flags': ['BROADCAST', 'UP', 'LOWER_UP'], 'address': MAC}]
        return addresses, links

    def test_ipv4_inventory_without_mac_uses_explicit_correlated_link_lookup(self):
        state, _ = self.manifests()
        addresses, links = self.interface_inventory()
        self.assertNotIn('address', addresses[0])
        with patch.object(setup.common, 'run', side_effect=[json.dumps(addresses), json.dumps(links)]) as run:
            self.assertEqual(setup.namespace_interface(state), {'name': 'tap-test', 'mac': MAC})
        self.assertEqual(run.call_args_list[1].args[0], ['ip', 'netns', 'exec', state['namespace'],
                                                        'ip', '-j', 'link', 'show', 'dev', 'tap-test'])

    def test_stale_mac_in_address_dump_does_not_override_current_link_mac(self):
        state, _ = self.manifests()
        addresses, links = self.interface_inventory()
        addresses[0]['address'] = 'fa:16:3e:99:99:99'
        with patch.object(setup.common, 'run', side_effect=[json.dumps(addresses), json.dumps(links)]):
            self.assertEqual(setup.namespace_interface(state)['mac'], MAC)

    def test_missing_or_ambiguous_source_interface_stops_before_link_lookup(self):
        state, _ = self.manifests()
        addresses, _ = self.interface_inventory()
        for inventory in ([], [addresses[0], addresses[0]]):
            with patch.object(setup.common, 'run', return_value=json.dumps(inventory)) as run, self.assertRaises(RuntimeError):
                setup.namespace_interface(state)
            run.assert_called_once()

    def test_link_name_index_type_and_up_state_must_match(self):
        state, _ = self.manifests()
        for field, value in (('ifindex', 12), ('ifname', 'different'), ('link_type', 'loopback'), ('flags', [])):
            addresses, links = self.interface_inventory()
            links[0][field] = value
            with patch.object(setup.common, 'run', side_effect=[json.dumps(addresses), json.dumps(links)]), self.assertRaises(RuntimeError):
                setup.namespace_interface(state)

    def test_missing_invalid_multicast_or_zero_link_mac_is_not_guessed(self):
        state, _ = self.manifests()
        for mac in (None, 'invalid', 'ff:ff:ff:ff:ff:ff', '00:00:00:00:00:00'):
            addresses, links = self.interface_inventory()
            links[0]['address'] = mac
            with patch.object(setup.common, 'run', side_effect=[json.dumps(addresses), json.dumps(links)]), self.assertRaisesRegex(RuntimeError, 'valid unicast MAC'):
                setup.namespace_interface(state)

    def manifests(self):
        ids = {key: str(uuid4()) for key in ('server', 'network', 'subnet', 'security_group', 'port', 'flavor')}
        state = {'run_id': 'b' * 32, 'project_id': 'project-a', 'user_id': 'user-a', 'ids': ids,
            'image_id': str(uuid4()), 'cidr': '172.30.241.0/24', 'guest_ip': '172.30.241.100',
            'metadata_ip': '172.30.241.2', 'namespace': 'ovnmeta-' + ids['network'],
            'create_job_id': 'openstack-job-' + 'a' * 32, 'server_name': 'openstack-job-' + 'a' * 32,
            'image_upload': {'phase': 'verified'}, 'results': {'application_create': {'verified': True}}}
        original = {'run_id': state['run_id'], 'project_id': 'admin-project', 'namespace': 'ovnmeta-original',
            'guest_ip': '172.30.240.154', 'cidr': '172.30.240.0/24', 'ids': {'network': 'original-net'},
            'results': {'tenant_api': {'tenants': {'a': {'project_id': 'project-a', 'user_id': 'user-a',
                                                      'network_id': ids['network']}}}}}
        return state, original

    def test_manifest_requires_distinct_projects_correct_job_and_private_subnet_scope(self):
        state, original = self.manifests()
        setup.validate_manifest(state, original)
        for key, value in (('project_id', 'other'), ('server_name', 'foreign-name'),
                           ('namespace', original['namespace']), ('cidr', original['cidr']),
                           ('metadata_ip', state['guest_ip'])):
            changed = dict(state, **{key: value})
            with self.assertRaises(RuntimeError):
                setup.validate_manifest(changed, original)

    def test_read_only_journal_must_match_provider_id_actor_and_original_request(self):
        state, _ = self.manifests()
        request = {'operation': 'compute.create', 'project_id': state['project_id'], 'image_id': state['image_id'],
                   'flavor_id': state['ids']['flavor'], 'network_id': state['ids']['network']}
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.launch, 'DATA_DIR', Path(directory)), \
             patch.object(setup.guest, 'protected_file'):
            journal = Path(directory) / 'application-jobs.sqlite'
            with sqlite3.connect(journal) as database:
                database.execute('CREATE TABLE jobs(job_id,project_id,actor_id,run_id,operation,state,instance_id,request)')
                database.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)',
                    (state['create_job_id'], state['project_id'], state['user_id'], state['run_id'], 'compute.create',
                     'succeeded', state['ids']['server'], json.dumps(request)))
            setup.verify_journal(state)
            for field, value in (('state', 'running'), ('actor_id', 'foreign'), ('instance_id', str(uuid4()))):
                with sqlite3.connect(journal) as database:
                    old = database.execute(f'SELECT {field} FROM jobs').fetchone()[0]
                    database.execute(f'UPDATE jobs SET {field}=?', (value,))
                with self.assertRaises(RuntimeError):
                    setup.verify_journal(state)
                with sqlite3.connect(journal) as database:
                    database.execute(f'UPDATE jobs SET {field}=?', (old,))

    def test_application_trust_file_is_separate_private_and_existing_contents_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.launch, 'DATA_DIR', Path(directory)), \
             patch.object(setup.guest, 'protected_file'):
            original = Path(directory) / 'known_hosts'
            original.write_text('existing pinned key')
            path = setup.trust_file()
            self.assertNotEqual(path, original)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.write_text('new pinned key')
            self.assertEqual(setup.trust_file().read_text(), 'new pinned key')
            self.assertEqual(original.read_text(), 'existing pinned key')

    def test_raw_probe_errors_are_not_negative_packet_evidence(self):
        state, _ = self.manifests()
        interface = {'name': 'tap-test', 'mac': MAC}
        with patch.object(setup.common, 'run', side_effect=RuntimeError('raw socket unavailable')), self.assertRaises(RuntimeError):
            setup.arp_probe(state, interface, '172.30.240.154')
        bad = {'attempts': 3, 'source_mac': MAC, 'source_ip': state['metadata_ip'],
               'target_ip': '172.30.240.154', 'replies': [[], []]}
        with patch.object(setup.common, 'run', return_value=json.dumps(bad)), self.assertRaises(RuntimeError):
            setup.arp_probe(state, interface, '172.30.240.154')

    def test_three_empty_windows_are_required_and_any_foreign_reply_fails(self):
        setup.require_negative({'attempts': 3, 'replies': [[], [], []]})
        for result in ({'attempts': 0, 'replies': []}, {'attempts': 3, 'replies': [[], []]},
                       {'attempts': 3, 'replies': [[], [MAC], []]}):
            with self.assertRaises(RuntimeError):
                setup.require_negative(result)
        setup.require_positive({'attempts': 3, 'replies': [[MAC], [MAC], [MAC]]}, MAC)
        with self.assertRaises(RuntimeError):
            setup.require_positive({'attempts': 3, 'replies': [[MAC], [], [MAC]]}, MAC)

    def test_off_subnet_arp_suppression_is_not_mislabelled_as_network_isolation(self):
        state, _ = self.manifests()
        text = 'TASK11_ARP_POLICY_BEGIN\n0\n0\n2\n0\nTASK11_ARP_POLICY_END\n'
        with patch.object(setup.common, 'run', return_value=text), self.assertRaisesRegex(RuntimeError, 'could suppress'):
            setup.read_arp_policy(state, 'eth0')

    def run_validation(self, packet_results, *, reboot=False):
        state, original = self.manifests()
        report = {'boot_id': 'before', 'interface': 'eth0', 'metadata_uuid': state['ids']['server']}
        changed = dict(report, boot_id='changed') if reboot else report
        network, port = NS(mtu=1442), NS(mac_address=MAC)
        with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'progress'), \
             patch.object(setup, 'application_attachment', return_value=(network, port)), \
             patch.object(setup.guest, 'validate_attachment', return_value=(network, port)), \
             patch.object(setup.policy, 'select_probe_rules'), \
             patch.object(setup.guest, 'inspect_guest', side_effect=[report, report, changed, report]), \
             patch.object(setup.guest, 'probe_ping'), patch.object(setup, 'read_arp_policy'), \
             patch.object(setup, 'namespace_interface', return_value={'name': 'tap-test', 'mac': MAC}), \
             patch.object(setup, 'arp_probe', side_effect=packet_results):
            path = Path(directory) / 'application.json'
            try:
                setup.validate(None, None, original, state, path, Path(directory) / 'trust')
            except RuntimeError:
                return json.loads(path.read_text()), False
            return json.loads(path.read_text()), True

    def test_full_packet_acceptance_requires_before_after_controls_and_guest_continuity(self):
        positive = {'attempts': 3, 'replies': [[MAC], [MAC], [MAC]]}
        negative = {'attempts': 3, 'replies': [[], [], []]}
        state, passed = self.run_validation([positive, positive, negative, negative, positive, positive])
        self.assertTrue(passed)
        self.assertTrue(state['results']['application_guest']['verified'])
        self.assertTrue(state['results']['packet_isolation']['verified'])
        for packets, reboot in (([negative], False),
                ([positive, positive, negative, negative, negative], False),
                ([positive, positive, negative, negative, positive, positive], True)):
            state, passed = self.run_validation(packets, reboot=reboot)
            self.assertFalse(passed)
            self.assertFalse(state['results']['packet_isolation']['verified'])
            self.assertTrue(state['results']['application_guest']['verified'])


if __name__ == '__main__':
    unittest.main()
