"""Offline safety checks for the operator-run application acceptance stage."""
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
    SPEC = importlib.util.spec_from_file_location('task11_application_create', SCRIPTS / 'validate_application_create.py')
    setup = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(setup)
finally:
    sys.path.remove(str(SCRIPTS))


class ApplicationCreateSafetyTests(unittest.TestCase):
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
