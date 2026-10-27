"""Protect existing identities and the collector's retained credentials."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('browser_tenant_safety', SCRIPTS / 'prepare_browser_tenants.py')
    tenants = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(tenants)
finally:
    sys.path.remove(str(SCRIPTS))


class BrowserTenantSafetyTests(unittest.TestCase):
    def test_matching_name_cannot_adopt_foreign_identity_or_project(self):
        for change in ({'description': 'foreign'}, {'domain_id': 'other-domain'}, {'id': 'other-id'}):
            resource = NS(**dict({'id': 'owned-id', 'name': 'poc', 'description': 'marker', 'domain_id': 'default'}, **change))
            with self.subTest(change=change), self.assertRaises(tenants.safety.GuardError):
                tenants.owned(resource, 'poc', 'marker', 'owned-id')

    def test_missing_resource_is_not_replaced_by_ownership_check(self):
        with self.assertRaises(tenants.safety.GuardError):
            tenants.owned(None, 'poc', 'marker')

    def test_config_update_preserves_enrolled_projects_and_rejects_password_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'collector.json'
            account = NS(pw_uid=os.geteuid(), pw_gid=os.getegid())
            initial = {'user_id': 'owned', 'password': 'test-placeholder', 'project_ids': ['a']}
            with patch.object(tenants, 'RUNTIME', path), patch.object(tenants.pwd, 'getpwnam', return_value=account):
                tenants.write_collector_config(initial)
                tenants.write_collector_config(dict(initial, project_ids=['b']))
                self.assertEqual(json.loads(path.read_text())['project_ids'], ['a', 'b'])
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                with self.assertRaises(tenants.safety.GuardError):
                    tenants.write_collector_config(dict(initial, password='replacement'))
                self.assertEqual(json.loads(path.read_text())['password'], 'test-placeholder')


if __name__ == '__main__':
    unittest.main()
