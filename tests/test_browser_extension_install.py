"""Authenticated middleware ordering and preservation of unrelated config."""
import configparser
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts/local-openstack'
sys.path.insert(0, str(SCRIPTS))
try:
    SPEC = importlib.util.spec_from_file_location('browser_install', SCRIPTS / 'install_poc_browser.py')
    installer = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(installer)
finally:
    sys.path.remove(str(SCRIPTS))

BASE = '''[DEFAULT]
bind_ip = 127.0.0.1
[pipeline:main]
pipeline = catch_errors authtoken keystoneauth proxy-server
[filter:authtoken]
password = offline-placeholder
[filter:keystoneauth]
operator_roles = admin,member
'''


class ExtensionInstallTests(unittest.TestCase):
    def test_service_code_directories_remain_traversable_with_private_umask(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'release' / 'package' / 'templates'
            previous = os.umask(0o077)
            try:
                installer.public_code_directory(path)
            finally:
                os.umask(previous)
            for item in (path, path.parent, path.parent.parent):
                self.assertEqual(item.stat().st_mode & 0o777, 0o755)

    def test_collector_restriction_is_after_token_validation_before_authorization(self):
        result = installer.swift_metering_config(BASE)
        parser = configparser.ConfigParser()
        parser.read_string(result)
        self.assertEqual(parser.get('pipeline:main', 'pipeline').split(),
                         ['catch_errors', 'authtoken', 'poc_metering', 'keystoneauth', 'proxy-server'])
        self.assertEqual(parser.get('filter:authtoken', 'password'), 'offline-placeholder')
        self.assertEqual(parser.get('DEFAULT', 'bind_ip'), '127.0.0.1')
        self.assertEqual(parser.get('filter:keystoneauth', 'operator_roles'), 'admin,member,billing_collector')

    def test_config_application_is_idempotent(self):
        once = installer.swift_metering_config(BASE)
        self.assertEqual(installer.swift_metering_config(once), once)

    def test_missing_or_misordered_auth_cannot_enable_collector_access(self):
        for pipeline in ('keystoneauth authtoken proxy-server', 'keystoneauth proxy-server',
                         'poc_metering authtoken keystoneauth proxy-server'):
            text = BASE.replace('catch_errors authtoken keystoneauth proxy-server', pipeline)
            with self.subTest(pipeline=pipeline), self.assertRaises(installer.safety.GuardError):
                installer.swift_metering_config(text)


if __name__ == '__main__':
    unittest.main()
