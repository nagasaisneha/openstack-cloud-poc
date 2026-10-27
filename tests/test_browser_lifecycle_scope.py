"""No disposable workflow may adopt or mutate retained/demo/foreign data."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    import browser_lifecycle as lifecycle
finally:
    sys.path.remove(str(SCRIPTS))


class LifecycleScopeTests(unittest.TestCase):
    def fixtures(self):
        source = {'retained_vm_id': 'retained', 'projects': {'a': {'id': 'a'}},
                  'resources': {'a': {'server': {'id': 'primary'}, 'volume': {'id': 'existing-volume'}}}}
        state = {'marker': 'owned-marker', 'prefix': 'poc-t49-owned', 'resources': {'server': {'id': 'test'}}}
        resource = NS(id='test', project_id='a', description='owned-marker', name='poc-t49-owned-vm')
        return source, state, resource

    def test_original_or_foreign_resources_are_rejected_even_if_state_attempts_adoption(self):
        for identifier in ('retained', 'primary', 'existing-volume'):
            source, state, resource = self.fixtures()
            state['resources']['server']['id'] = resource.id = identifier
            with self.subTest(identifier=identifier), self.assertRaises(lifecycle.safety.GuardError):
                lifecycle.disposable(resource, source, state, 'a', 'server')
        for change in ({'project_id': 'foreign'}, {'description': 'other'}, {'name': 'unrelated'}, {'id': 'unrecorded'}):
            source, state, resource = self.fixtures()
            for key, value in change.items():
                setattr(resource, key, value)
            with self.subTest(change=change), self.assertRaises(lifecycle.safety.GuardError):
                lifecycle.disposable(resource, source, state, 'a', 'server')

    def test_only_the_recorded_marked_owned_resource_is_eligible(self):
        source, state, resource = self.fixtures()
        self.assertIs(lifecycle.disposable(resource, source, state, 'a', 'server'), resource)


if __name__ == '__main__':
    unittest.main()
