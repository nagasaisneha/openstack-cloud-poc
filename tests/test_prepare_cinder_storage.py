"""Offline safety tests for the dedicated Cinder loop-backend preparer."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location(
        'task18_prepare_cinder_storage', SCRIPTS / 'prepare_cinder_storage.py')
    setup = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(setup)
finally:
    sys.path.remove(str(SCRIPTS))


class PrepareCinderStorageTests(unittest.TestCase):
    def test_loop_inventory_empty_and_expected_attachment(self):
        with patch.object(setup, 'run', return_value=json.dumps({'loopdevices': []})):
            self.assertIsNone(setup.loop_inventory())
        with patch.object(setup, 'run', return_value=json.dumps({'loopdevices': [
                {'name': '/dev/loop0', 'back-file': None}]})):
            self.assertIsNone(setup.loop_inventory())
        with patch.object(setup, 'run', return_value=json.dumps({'loopdevices': [
                {'name': '/dev/loop0', 'back-file': '/var/lib/cinder-poc/cinder-backend.img'}]})):
            self.assertEqual(setup.loop_inventory(), '/var/lib/cinder-poc/cinder-backend.img')

    def test_loop_inventory_rejects_other_attached_image(self):
        with patch.object(setup, 'run', return_value=json.dumps({'loopdevices': [
                {'name': '/dev/loop0', 'back-file': '/srv/important.img'}]})):
            backing = setup.loop_inventory()
        with self.assertRaisesRegex(RuntimeError, 'different backing file'):
            setup.validate_loop_attachment(backing)

    def test_pv_inventory_accepts_empty_or_exact_report(self):
        empty = {'report': [{'pv': []}]}
        exact = {'report': [{'pv': [{'pv_name': ' /dev/loop0 ',
                                     'vg_name': ' cinder-poc-vg ', 'pv_uuid': ' 1234 '}]}]}
        with patch.object(setup.subprocess, 'run', return_value=NS(
                stdout=json.dumps(empty), stderr='Failed to find physical volume', returncode=5)):
            self.assertIsNone(setup.pv_inventory())
        with patch.object(setup.subprocess, 'run', return_value=NS(
                stdout=json.dumps(exact), stderr='', returncode=0)):
            self.assertEqual(setup.pv_inventory()['vg_name'].strip(), 'cinder-poc-vg')

    def test_pvs_exit_five_is_accepted_only_for_valid_empty_report(self):
        empty = json.dumps({'report': [{'pv': []}]})
        populated = json.dumps({'report': [{'pv': [{'pv_name': '/dev/loop0'}]}]})
        self.assertEqual(setup.parse_pv_report(empty, 5), {'report': [{'pv': []}]})
        with self.assertRaisesRegex(RuntimeError, 'inconsistent status'):
            setup.parse_pv_report(populated, 5)
        with self.assertRaisesRegex(RuntimeError, 'inconsistent status'):
            setup.parse_pv_report(empty, 1)

    def test_api_config_requires_only_loopback_listen_and_vhost(self):
        safe = 'Listen 127.0.0.1:8776\n<VirtualHost 127.0.0.1:8776>\n</VirtualHost>\n'
        wildcard = 'Listen 8776\n<VirtualHost *:8776>\n</VirtualHost>\n'
        duplicate = safe + 'Listen 8776\n'
        self.assertTrue(setup.loopback_api_config(safe))
        self.assertFalse(setup.loopback_api_config(wildcard))
        self.assertFalse(setup.loopback_api_config(duplicate))

    def test_listener_report_exposes_only_local_address_field(self):
        output = 'LISTEN 0 511 127.0.0.1:8776 0.0.0.0:* users:("apache2",pid=1,fd=5)'
        with patch.object(setup, 'run', return_value=output):
            self.assertEqual(setup.listen_addresses('8776'), ['127.0.0.1:8776'])

    def test_state_is_explicitly_non_overwriting_and_no_global_lvm_filter_change(self):
        self.assertEqual(setup.SIZE, 8 * 1024 ** 3)
        self.assertEqual(setup.VG, 'cinder-poc-vg')
        self.assertEqual(setup.LVM_CONFIG, Path('/etc/cinder/cinder-poc-lvm.conf'))
        self.assertNotEqual(setup.LVM_CONFIG, Path('/etc/lvm/lvm.conf'))
        self.assertIn('^/dev/loop0$', setup.LVM_TEXT)
        self.assertTrue(any(isinstance(value, tuple) and 'global_lvm_filter_changed' in value
                            for value in setup.write_state.__code__.co_consts))


if __name__ == '__main__':
    unittest.main()
