"""Preserve ambiguous browser submissions and never disclose service credentials."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    import browser_resources as resources
    import show_browser_access as access
finally:
    sys.path.remove(str(SCRIPTS))


class BrowserResourceSafetyTests(unittest.TestCase):
    def test_uncertain_submission_preserves_intent_without_second_write(self):
        original = {'resource_submission_intents': {'a:server': {'name': 'owned', 'at': 'earlier'}}}
        with patch.object(resources.tenants, 'save') as save:
            with self.assertRaises(resources.safety.GuardError):
                resources.intent(original, 'a', 'server', 'replacement')
            save.assert_not_called()
        self.assertEqual(original['resource_submission_intents']['a:server']['name'], 'owned')

    def test_foreign_resource_is_not_recorded_as_tenant_creation(self):
        state = {'projects': {'a': {'id': 'project-a'}}}
        with patch.object(resources.tenants, 'save') as save:
            with self.assertRaises(resources.safety.GuardError):
                resources.record(state, 'a', 'network', NS(id='id', name='name', project_id='project-b'))
            save.assert_not_called()
        self.assertNotIn('resources', state)

    def test_local_access_defaults_to_names_and_never_exposes_collector_or_keys(self):
        private = {'collector_password': 'do-not-display', 'private_key': 'do-not-display',
                   'tenants': {x: {'username': x, 'project_name': 'project-' + x,
                                   'password': 'password-' + x} for x in ('a', 'b')}}
        default = access.access(private)
        self.assertTrue(all('password' not in item for item in default['accounts']))
        opted = access.access(private, 'admin-password', True)
        self.assertEqual([item['password'] for item in opted['accounts']],
                         ['admin-password', 'password-a', 'password-b'])
        self.assertNotIn('do-not-display', str(opted))


if __name__ == '__main__':
    unittest.main()
