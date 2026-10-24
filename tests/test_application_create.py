"""Offline safety checks for the operator-run application acceptance stage."""
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('task11_application_create', SCRIPTS / 'validate_application_create.py')
    setup = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(setup)
finally:
    sys.path.remove(str(SCRIPTS))


class ApplicationCreateSafetyTests(unittest.TestCase):
    def inventory(self, ports, subnets=()):
        network = NS(id='ac0d6dd9-ccae-4a7a-9754-713a2a3aa466')
        admin = NS(network=NS(ports=Mock(return_value=ports), subnets=Mock(return_value=subnets)))
        state = {'project_id': 'member-project'}
        return admin, network, state

    def metadata_port(self):
        return NS(id=str(uuid4()), network_id='ac0d6dd9-ccae-4a7a-9754-713a2a3aa466',
                  project_id='member-project', device_owner='network:distributed',
                  device_id='ovnmeta-ac0d6dd9-ccae-4a7a-9754-713a2a3aa466', fixed_ips=[])

    def test_empty_ovn_metadata_port_is_preserved_and_inventory_persisted(self):
        port = self.metadata_port()
        admin, network, state = self.inventory([port])
        with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'progress'):
            setup.pre_subnet_inventory(admin, network, state, Path(directory) / 'state.json')
        self.assertEqual(state['pre_subnet_inventory']['ports'][0]['port_id'], port.id)
        self.assertEqual(port.fixed_ips, [])
        admin.network.ports.assert_called_once_with(network_id=network.id)
        admin.network.subnets.assert_called_once_with(network_id=network.id)

    def test_network_without_ports_remains_allowed(self):
        admin, network, state = self.inventory([])
        with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'progress'):
            setup.pre_subnet_inventory(admin, network, state, Path(directory) / 'state.json')
        self.assertEqual(state['pre_subnet_inventory'], {'ports': [], 'subnet_ids': []})

    def test_workload_router_and_unbound_ports_are_not_metadata_exemptions(self):
        for owner in ('compute:nova', 'network:router_interface', ''):
            port = self.metadata_port()
            port.device_owner = owner
            admin, network, state = self.inventory([port])
            with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'progress'), self.assertRaises(RuntimeError):
                setup.pre_subnet_inventory(admin, network, state, Path(directory) / 'state.json')
            self.assertNotIn('pre_subnet_inventory', state)

    def test_wrong_project_network_or_metadata_device_is_rejected(self):
        for field in ('project_id', 'network_id', 'device_id', 'id'):
            port = self.metadata_port()
            setattr(port, field, 'unexpected-sensitive-sentinel')
            admin, network, state = self.inventory([port])
            with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'progress') as output, self.assertRaises(RuntimeError):
                setup.pre_subnet_inventory(admin, network, state, Path(directory) / 'state.json')
            self.assertNotIn('sensitive-sentinel', str(output.call_args_list))

    def test_allocated_or_unknown_metadata_ips_cannot_be_ignored(self):
        for addresses in ([{'subnet_id': str(uuid4()), 'ip_address': '172.30.241.2'}], None):
            port = self.metadata_port()
            port.fixed_ips = addresses
            admin, network, state = self.inventory([port])
            with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'progress'), self.assertRaises(RuntimeError):
                setup.pre_subnet_inventory(admin, network, state, Path(directory) / 'state.json')

    def test_duplicate_metadata_or_any_existing_subnet_still_blocks(self):
        for ports, subnets in (([self.metadata_port(), self.metadata_port()], []),
                               ([self.metadata_port()], [NS(id=str(uuid4()))])):
            admin, network, state = self.inventory(ports, subnets)
            with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'progress'), self.assertRaises(RuntimeError):
                setup.pre_subnet_inventory(admin, network, state, Path(directory) / 'state.json')

    def test_only_real_404_allows_new_image_creation(self):
        class ProviderError(Exception):
            def __init__(self, status):
                self.status_code = status
        self.assertIsNone(setup.missing_only(Mock(side_effect=ProviderError(404)), 'id'))
        for failure in (ProviderError(403), ProviderError(500), TimeoutError()):
            with self.assertRaises(type(failure)):
                setup.missing_only(Mock(side_effect=failure), 'id')

    def test_incomplete_source_file_is_preserved_and_not_downloaded_again(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(setup.launch, 'DATA_DIR', Path(directory)), \
             patch.object(setup.guest, 'protected_file'):
            path = Path(directory) / 'application-cirros-source.qcow2'
            path.write_bytes(b'incomplete')
            admin = NS(image=NS(get_image=Mock(), download_image=Mock()))
            with self.assertRaisesRegex(RuntimeError, 'failed integrity'):
                setup.source_image(admin)
            self.assertEqual(path.read_bytes(), b'incomplete')
            admin.image.download_image.assert_not_called()

    def rule(self, protocol='tcp', source='172.30.241.2/32'):
        return NS(security_group_id='group', direction='ingress', ether_type='IPv4',
                  protocol=protocol, remote_ip_prefix=source, port_range_min=22 if protocol == 'tcp' else None,
                  port_range_max=22 if protocol == 'tcp' else None, description='marker',
                  remote_group_id=None, remote_address_group_id=None)

    def test_unexpected_broad_rule_is_rejected_before_any_rule_change(self):
        cloud = NS(network=NS(security_group_rules=Mock(return_value=[self.rule(source='0.0.0.0/0')])))
        with patch.object(setup.launch, 'allow_metadata_probe') as change, self.assertRaisesRegex(RuntimeError, 'policy drift'):
            setup.ensure_probe_rules(cloud, NS(id='group'), '172.30.241.2', 'marker')
        change.assert_not_called()

    def test_duplicate_rule_is_not_silently_accepted(self):
        cloud = NS(network=NS(security_group_rules=Mock(return_value=[self.rule(), self.rule()])))
        with patch.object(setup.launch, 'allow_metadata_probe') as change, self.assertRaises(RuntimeError):
            setup.ensure_probe_rules(cloud, NS(id='group'), '172.30.241.2', 'marker')
        change.assert_not_called()

    def test_two_exact_probe_rules_are_observed(self):
        rules = [self.rule(), self.rule('icmp')]
        cloud = NS(network=NS(security_group_rules=Mock(return_value=rules)))
        with patch.object(setup.launch, 'allow_metadata_probe'):
            setup.ensure_probe_rules(cloud, NS(id='group'), '172.30.241.2', 'marker')

    def test_missing_rule_after_creation_is_not_success(self):
        cloud = NS(network=NS(security_group_rules=Mock(return_value=[])))
        with patch.object(setup.launch, 'allow_metadata_probe'), self.assertRaisesRegex(RuntimeError, 'not observed'):
            setup.ensure_probe_rules(cloud, NS(id='group'), '172.30.241.2', 'marker')


if __name__ == '__main__':
    unittest.main()
