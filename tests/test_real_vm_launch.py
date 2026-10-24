"""Offline launch safety tests. These do not create guests or contact APIs."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('task11_vm_launch', SCRIPTS / 'launch_test_vm.py')
    setup = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(setup)
finally:
    sys.path.remove(str(SCRIPTS))


class RealVmLaunchTests(unittest.TestCase):
    def state(self):
        return {'run_id': 'run-identifier', 'project_id': 'project-id', 'ids': {}}

    def resource(self, server=False):
        return SimpleNamespace(id='resource-id', project_id='project-id',
            description='Task 11 local POC run run-identifier',
            metadata={'task11_run_id': 'run-identifier'} if server else {})

    def test_cidr_choice_skips_default_route_and_avoids_specific_routes(self):
        chosen = setup.choose_cidr(['0.0.0.0/0', '172.19.160.0/20', '192.168.122.0/24'])
        self.assertEqual(chosen, '172.30.240.0/24')
        self.assertEqual(setup.choose_cidr(['172.16.0.0/12']), '10.203.0.0/24')

    def test_cidr_choice_refuses_overlapping_address_subnet_or_all_candidates(self):
        self.assertEqual(setup.choose_cidr(['172.30.240.5/32']), '10.203.0.0/24')
        with self.assertRaises(RuntimeError):
            setup.choose_cidr(['172.16.0.0/12', '10.0.0.0/8', '192.168.0.0/16'])

    def test_owned_resource_checks_project_and_marker(self):
        for server in (False, True):
            resource = self.resource(server)
            marker = resource.description
            setup.assert_owned(resource, self.state(), marker, server=server)
            resource.project_id = 'unrelated-project'
            with self.assertRaisesRegex(RuntimeError, 'another project'):
                setup.assert_owned(resource, self.state(), marker, server=server)
            resource.project_id = 'project-id'
            resource.description = 'unrelated'
            resource.metadata = {}
            with self.assertRaisesRegex(RuntimeError, 'ownership marker'):
                setup.assert_owned(resource, self.state(), marker, server=server)

    def test_interrupted_create_records_id_before_fetch_and_retry_does_not_create(self):
        state = self.state()
        create = Mock(return_value=SimpleNamespace(id='resource-id'))
        get = Mock(side_effect=RuntimeError('temporary fetch failure'))
        with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'progress'):
            path = Path(directory) / 'state.json'
            with self.assertRaisesRegex(RuntimeError, 'temporary fetch'):
                setup.ensure_owned(state, path, 'server', get, lambda: [], create, server=True)
            self.assertEqual(json.loads(path.read_text())['ids']['server'], 'resource-id')
            get.side_effect = None
            get.return_value = self.resource(server=True)
            setup.ensure_owned(state, path, 'server', get, lambda: [], create, server=True)
        create.assert_called_once()

    def test_foreign_candidate_and_duplicate_candidate_are_not_adopted_or_recreated(self):
        resource = self.resource()
        resource.description = 'pre-existing unrelated resource'
        create = Mock()
        with tempfile.TemporaryDirectory() as directory, patch.object(setup, 'progress'):
            for candidates in ([resource], [resource, resource]):
                state = self.state()
                path = Path(directory) / 'state.json'
                with self.assertRaises(RuntimeError):
                    setup.ensure_owned(state, path, 'network', lambda _: resource,
                                       lambda: candidates, create)
                self.assertNotIn('network', state['ids'])
        create.assert_not_called()

    def test_kvm_check_requires_both_domain_type_and_actual_monitor_acceleration(self):
        setup.assert_kvm('<domain type="kvm"/>', '{"return":{"enabled":true,"present":true}}')
        for xml, monitor in (
            ('<domain type="qemu"/>', '{"return":{"enabled":true,"present":true}}'),
            ('<domain type="kvm"/>', '{"return":{"enabled":false,"present":true}}'),
            ('<domain type="kvm"/>', '{"error":{"class":"GenericError"}}'),
        ):
            with self.subTest(xml=xml, monitor=monitor), self.assertRaises(RuntimeError):
                setup.assert_kvm(xml, monitor)

    def test_probe_rules_limit_ssh_and_icmp_to_one_metadata_address(self):
        create = Mock()
        cloud = SimpleNamespace(network=SimpleNamespace(
            security_group_rules=lambda **kwargs: [], create_security_group_rule=create))
        setup.allow_metadata_probe(cloud, SimpleNamespace(id='owned-group'), '172.30.240.2', 'marker')
        self.assertEqual(create.call_count, 2)
        for call in create.call_args_list:
            self.assertEqual(call.kwargs['remote_ip_prefix'], '172.30.240.2/32')
            self.assertEqual(call.kwargs['direction'], 'ingress')
            if call.kwargs['protocol'] == 'tcp':
                self.assertEqual(call.kwargs['port_range_min'], 22)
                self.assertEqual(call.kwargs['port_range_max'], 22)


if __name__ == '__main__':
    unittest.main()
