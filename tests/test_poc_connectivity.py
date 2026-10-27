"""Connectivity plans must preserve overlay mappings and reject broad exposure."""
import configparser
from pathlib import Path
import sys
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    import configure_poc_connectivity as access
finally:
    sys.path.remove(str(SCRIPTS))


class ConnectivitySafetyTests(unittest.TestCase):
    def test_provider_mapping_preserves_existing_consumers_and_rejects_collision(self):
        self.assertEqual(access.mappings('public:br-ex'), 'poc-ex:br-poc-ex,public:br-ex')
        self.assertEqual(access.mappings(access.mappings('public:br-ex')), access.mappings('public:br-ex'))
        for value in ('poc-ex:some-other-bridge', 'public:a,public:b'):
            with self.subTest(value=value), self.assertRaises(access.safety.GuardError):
                access.mappings(value)

    def test_ml2_change_keeps_geneve_and_existing_configuration(self):
        text = '[ml2]\ntype_drivers = geneve\nmechanism_drivers = ovn\n[unrelated]\nsetting = preserve\n'
        result = access.ml2_config(text)
        parsed = configparser.ConfigParser()
        parsed.read_string(result)
        self.assertEqual(parsed.get('ml2', 'type_drivers'), 'geneve,flat')
        self.assertEqual(parsed.get('unrelated', 'setting'), 'preserve')
        self.assertEqual(result, access.ml2_config(result))
        with self.assertRaises(access.safety.GuardError):
            access.ml2_config(text + '[ml2_type_flat]\nflat_networks = *\n')

    def test_forward_cannot_listen_publicly_or_point_outside_the_isolated_pool(self):
        unit = access.forward_unit('192.0.2.100', 22, 2221)
        self.assertIn('bind=127.0.0.1', unit)
        self.assertIn('User=nobody', unit)
        self.assertIn('NoNewPrivileges=true', unit)
        for address, port, listen in [('203.0.113.10', 22, 2221), ('192.0.2.100', 3306, 2221),
                                      ('192.0.2.100', 22, 22)]:
            with self.assertRaises(access.safety.GuardError):
                access.forward_unit(address, port, listen)


if __name__ == '__main__':
    unittest.main()
